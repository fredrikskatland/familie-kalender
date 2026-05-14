import base64
import io
import json
import logging
import os
from collections import deque
from datetime import date

from openai import OpenAI
from pdf2image import convert_from_bytes

from calendar_service import CalendarService

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MODEL = "gpt-4o"

calendar = CalendarService()
client = OpenAI(api_key=OPENAI_API_KEY)

# Delt konversasjonshistorikk for gruppen (maks 20 meldingspar)
_history: deque = deque(maxlen=20)

# Familiemedlemmer og tilhørende Google Calendar colorId
PERSON_COLORS = {
    "Fredrik": "9",   # Blueberry (blå)
    "Sarah": "4",     # Flamingo (rosa)
    "Lotta": "5",     # Banana (gul)
    "Morten": "2",    # Sage (grønn)
    "Alle": "1",      # Lavender (lilla)
}
COLOR_PERSON = {v: k for k, v in PERSON_COLORS.items()}

PROMPT_FILE = os.path.join(os.path.dirname(__file__), "system_prompt.md")


def _load_system_prompt() -> str:
    with open(PROMPT_FILE, encoding="utf-8") as f:
        return f.read()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": "Opprett en ny hendelse i familiekalenderen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Tittel på hendelsen"},
                    "start_datetime": {"type": "string", "description": "Starttidspunkt i ISO 8601-format, f.eks. 2025-06-15T09:00:00"},
                    "end_datetime": {"type": "string", "description": "Sluttidspunkt i ISO 8601-format"},
                    "person": {"type": "string", "enum": ["Fredrik", "Sarah", "Lotta", "Morten", "Alle"], "description": "Hvem hendelsen gjelder"},
                    "description": {"type": "string", "description": "Valgfri beskrivelse eller notater"},
                    "location": {"type": "string", "description": "Valgfri lokasjon"},
                },
                "required": ["title", "start_datetime", "end_datetime", "person"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": "Oppdater en eksisterende hendelse i familiekalenderen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "ID-en til hendelsen som skal oppdateres"},
                    "title": {"type": "string"},
                    "start_datetime": {"type": "string"},
                    "end_datetime": {"type": "string"},
                    "person": {"type": "string", "enum": ["Fredrik", "Sarah", "Lotta", "Morten", "Alle"]},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": "Slett en hendelse fra familiekalenderen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "ID-en til hendelsen som skal slettes"},
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": "Hent kommende hendelser fra familiekalenderen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "description": "Antall dager frem i tid å hente hendelser for (standard: 14)"},
                },
            },
        },
    },
]


def _pdf_to_images_base64(pdf_bytes: bytes) -> list[dict]:
    images = convert_from_bytes(pdf_bytes, dpi=150)
    result = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        result.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })
    return result


def _build_content(text: str, media) -> list:
    content = []
    if media:
        mime = media.mimetype.lower()
        raw_bytes = base64.b64decode(media.data)
        if "pdf" in mime:
            content.extend(_pdf_to_images_base64(raw_bytes))
            content.append({"type": "text", "text": text if text else "Se vedlagt PDF og opprett alle hendelser du finner."})
        elif mime.startswith("image/"):
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{media.data}"}})
            content.append({"type": "text", "text": text if text else "Se vedlagt bilde og opprett alle hendelser du finner."})
        else:
            content.append({"type": "text", "text": text or "(ukjent filtype)"})
    else:
        content.append({"type": "text", "text": text})
    return content


def _handle_tool_call(tool_name: str, tool_input: dict) -> str:
    try:
        logger.debug("Tool call: %s args=%s", tool_name, json.dumps(tool_input, ensure_ascii=False))
        if tool_name == "create_event":
            person = tool_input.pop("person", "Alle")
            tool_input["color_id"] = PERSON_COLORS.get(person, "1")
            event = calendar.create_event(**tool_input)
            result = json.dumps({"status": "ok", "event_id": event["id"], "link": event.get("htmlLink", "")})
        elif tool_name == "update_event":
            person = tool_input.pop("person", None)
            if person:
                tool_input["color_id"] = PERSON_COLORS.get(person)
            event = calendar.update_event(**tool_input)
            result = json.dumps({"status": "ok", "event_id": event["id"]})
        elif tool_name == "delete_event":
            calendar.delete_event(tool_input["event_id"])
            result = json.dumps({"status": "ok"})
        elif tool_name == "list_events":
            events = calendar.list_events(days_ahead=tool_input.get("days_ahead", 14))
            result = json.dumps({"events": events})
        else:
            result = json.dumps({"error": f"Ukjent verktøy: {tool_name}"})
        logger.debug("Tool result: %s", result)
        return result
    except Exception as e:
        logger.exception("Feil i verktøy %s", tool_name)
        return json.dumps({"error": str(e)})


async def process_message(sender: str, text: str, media) -> str:
    today = date.today().isoformat()
    system = _load_system_prompt().format(today=today)
    logger.debug("System prompt:\n%s", system)

    # Bygg ny brukermelding
    user_content = _build_content(text, media)
    logger.debug("Brukermelding fra %s: %s", sender, text[:500])
    _history.append({"role": "user", "content": user_content})

    # Sett sammen komplett meldingsliste: system + historikk
    messages = [{"role": "system", "content": system}] + list(_history)

    for _ in range(10):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            max_tokens=2048,
        )

        msg = response.choices[0].message
        messages.append(msg)

        if response.choices[0].finish_reason == "stop":
            reply = msg.content or "Ferdig."
            logger.debug("Assistentsvar: %s", reply)
            _history.append({"role": "assistant", "content": reply})
            return reply

        if response.choices[0].finish_reason == "tool_calls":
            for tool_call in msg.tool_calls:
                tool_input = json.loads(tool_call.function.arguments)
                logger.info("Kaller verktøy: %s med %s", tool_call.function.name, tool_input)
                result = _handle_tool_call(tool_call.function.name, tool_input)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })
        else:
            break

    return "Beklager, jeg klarte ikke å fullføre forespørselen."
