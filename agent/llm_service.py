import asyncio
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
MODEL = "gpt-5"

calendar = CalendarService()
client = OpenAI(api_key=OPENAI_API_KEY)

_lock = asyncio.Lock()

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
MEMORY_DIR = os.path.join(os.path.dirname(__file__), "memory")
FACTS_FILE = os.path.join(MEMORY_DIR, "facts.md")
HISTORY_FILE = os.path.join(MEMORY_DIR, "history.json")

os.makedirs(MEMORY_DIR, exist_ok=True)


# --- Langtidsminne (facts.md) ---

def _load_facts() -> str:
    if not os.path.exists(FACTS_FILE):
        return ""
    with open(FACTS_FILE, encoding="utf-8") as f:
        return f.read().strip()


def get_facts() -> str:
    """Returnerer lagrede fakta om familien (til bruk i andre moduler)."""
    return _load_facts()


def _save_fact(person: str, fact: str) -> None:
    existing = ""
    if os.path.exists(FACTS_FILE):
        with open(FACTS_FILE, encoding="utf-8") as f:
            existing = f.read()

    section = f"## {person}"
    entry = f"- {fact}"

    if section in existing:
        idx = existing.index(section) + len(section)
        next_sec = existing.find("\n## ", idx)
        insert_at = next_sec if next_sec != -1 else len(existing)
        block = existing[idx:insert_at].rstrip()
        existing = existing[:idx] + block + f"\n{entry}\n" + existing[insert_at:]
    else:
        existing = existing.rstrip() + f"\n\n{section}\n{entry}\n"

    with open(FACTS_FILE, "w", encoding="utf-8") as f:
        f.write(existing.lstrip())


# --- Kortidsminne / samtalehistorikk (history.json) ---

def _load_history_from_disk() -> list:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.warning("Kunne ikke laste konversasjonshistorikk fra disk")
        return []


def _save_history_to_disk(history: deque) -> None:
    # Strip base64-media fra bruker-meldinger før lagring
    serializable = []
    for msg in history:
        if msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, list):
                text_parts = [p for p in content if p.get("type") == "text"]
                serializable.append({
                    "role": "user",
                    "content": text_parts if text_parts else [{"type": "text", "text": "[media]"}],
                })
            else:
                serializable.append(msg)
        else:
            serializable.append(msg)
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.warning("Kunne ikke lagre konversasjonshistorikk til disk")


# Delt konversasjonshistorikk (maks 20 meldingspar), lastet fra disk ved oppstart
_history: deque = deque(_load_history_from_disk(), maxlen=20)


def _load_system_prompt() -> str:
    with open(PROMPT_FILE, encoding="utf-8") as f:
        prompt = f.read()
    facts = _load_facts()
    if facts:
        prompt += f"\n\n---\n\n## Langtidsminne – kjent om familien\n\n{facts}"
    return prompt


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
                    "start_datetime": {"type": "string", "description": "Starttidspunkt: YYYY-MM-DDTHH:MM:SS for tidspunkt, eller YYYY-MM-DD for heldagshendelse"},
                    "end_datetime": {"type": "string", "description": "Sluttidspunkt: samme format som start_datetime. For heldagshendelse: dagen etter (eksklusiv slutt)"},
                    "person": {"type": "string", "enum": ["Fredrik", "Sarah", "Lotta", "Morten", "Alle"], "description": "Hvem hendelsen gjelder"},
                    "description": {"type": "string", "description": "Valgfri beskrivelse eller notater"},
                    "location": {"type": "string", "description": "Valgfri lokasjon"},
                    "recurrence_frequency": {"type": "string", "enum": ["DAILY", "WEEKLY", "MONTHLY", "YEARLY"], "description": "Gjentakelsesfrekvens for hendelsen"},
                    "recurrence_until": {"type": "string", "description": "Sluttdato for gjentakelse, YYYY-MM-DD. Bruk enten denne eller recurrence_count."},
                    "recurrence_count": {"type": "integer", "description": "Antall ganger hendelsen skal gjenta seg"},
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
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "Lagre et faktum om familien til langtidsminnet. Bruk dette proaktivt når du "
                "lærer noe stabilt og gjenbrukbart: faste aktiviteter og rutiner, "
                "skole/barnehage-informasjon, allergier, preferanser, faste hente- og leveringstider. "
                "Skriv faktum presist og kortfattet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "person": {
                        "type": "string",
                        "enum": ["Fredrik", "Sarah", "Lotta", "Morten", "Generelt"],
                        "description": "Hvem faktumet gjelder",
                    },
                    "fact": {
                        "type": "string",
                        "description": "Faktum som skal huskes, f.eks. 'Fotballag: Stjernene FK, trening tirsdager 17-18'",
                    },
                },
                "required": ["person", "fact"],
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
            tool_input["recurrence_frequency"] = tool_input.pop("recurrence_frequency", None)
            tool_input["recurrence_until"] = tool_input.pop("recurrence_until", None)
            tool_input["recurrence_count"] = tool_input.pop("recurrence_count", None)
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
        elif tool_name == "save_memory":
            _save_fact(tool_input["person"], tool_input["fact"])
            logger.info("Minne lagret – %s: %s", tool_input["person"], tool_input["fact"])
            result = json.dumps({"status": "ok"})
        else:
            result = json.dumps({"error": f"Ukjent verktøy: {tool_name}"})
        logger.debug("Tool result: %s", result)
        return result
    except Exception as e:
        logger.exception("Feil i verktøy %s", tool_name)
        return json.dumps({"error": str(e)})


async def process_message(sender: str, text: str, media) -> str:
    async with _lock:
        return await _process_message(sender, text, media)


async def _process_message(sender: str, text: str, media) -> str:
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
        )

        finish_reason = response.choices[0].finish_reason
        msg = response.choices[0].message
        messages.append(msg)

        # Logg token-bruk og reasoning hvis tilgjengelig
        usage = response.usage
        if usage:
            details = getattr(usage, "completion_tokens_details", None)
            reasoning_tokens = getattr(details, "reasoning_tokens", None)
            text_tokens = getattr(details, "text_tokens", None)
            logger.info(
                "Token-bruk — total: %s, reasoning: %s, output: %s",
                usage.completion_tokens,
                reasoning_tokens if reasoning_tokens is not None else "?",
                text_tokens if text_tokens is not None else "?",
            )
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            logger.debug("Reasoning: %s", reasoning[:1000])

        if finish_reason == "stop":
            reply = msg.content or "Ferdig."
            logger.info("Assistentsvar: %s", reply)
            _history.append({"role": "assistant", "content": reply})
            _save_history_to_disk(_history)
            return reply

        if finish_reason == "tool_calls":
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
            logger.error("Uventet finish_reason fra modellen: %s — avbryter", finish_reason)
            break

    return "Beklager, jeg klarte ikke å fullføre forespørselen."
