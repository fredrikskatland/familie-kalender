import base64
import logging
import os

import httpx

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.facebook.com/v25.0"
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")


async def send_message(to: str, text: str) -> None:
    """Send en tekstmelding til et telefonnummer via Meta Cloud API."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GRAPH_API_URL}/{PHONE_ID}/messages",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": text},
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error("Feil ved sending av melding: %s %s", resp.status_code, resp.text)
        else:
            logger.info("Sendte melding til %s", to)


async def download_media(media_id: str) -> tuple[bytes, str]:
    """Last ned mediafil fra Meta og returner (bytes, mimetype)."""
    async with httpx.AsyncClient() as client:
        # Hent URL og metadata for media-ID-en
        resp = await client.get(
            f"{GRAPH_API_URL}/{media_id}",
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=15,
        )
        resp.raise_for_status()
        info = resp.json()
        media_url = info["url"]
        mime_type = info.get("mime_type", "application/octet-stream")

        # Last ned selve filen
        resp = await client.get(
            media_url,
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content, mime_type


def parse_incoming(data: dict) -> list[dict]:
    """
    Parser innkommende Meta webhook-payload og returnerer en liste av meldinger:
    [{"from": "4712345678", "text": "...", "media_id": None, "mime_type": None}]
    """
    messages = []
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                sender = msg.get("from", "")
                msg_type = msg.get("type", "text")

                text = ""
                media_id = None
                mime_type = None

                if msg_type == "text":
                    text = msg.get("text", {}).get("body", "")
                elif msg_type == "image":
                    media_id = msg["image"]["id"]
                    mime_type = msg["image"].get("mime_type", "image/jpeg")
                    text = msg["image"].get("caption", "")
                elif msg_type == "document":
                    media_id = msg["document"]["id"]
                    mime_type = msg["document"].get("mime_type", "application/pdf")
                    text = msg["document"].get("caption", "")
                elif msg_type == "audio":
                    media_id = msg["audio"]["id"]
                    mime_type = msg["audio"].get("mime_type", "audio/ogg")
                else:
                    logger.info("Ukjent meldingstype: %s", msg_type)
                    continue

                messages.append({
                    "from": sender,
                    "text": text,
                    "media_id": media_id,
                    "mime_type": mime_type,
                })
    return messages
