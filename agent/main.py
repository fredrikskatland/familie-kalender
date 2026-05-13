import base64
import logging
import os

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response

from llm_service import process_message
from meta_service import download_media, parse_incoming, send_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WEBHOOK_VERIFY_TOKEN = os.environ.get("WEBHOOK_VERIFY_TOKEN", "")
FAMILY_NUMBERS = [n.strip() for n in os.environ.get("FAMILY_NUMBERS", "").split(",") if n.strip()]

app = FastAPI(title="Familie Kalender Agent")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
):
    """Meta sender en GET-forespørsel for å verifisere webhook-endepunktet."""
    if hub_mode == "subscribe" and hub_verify_token == WEBHOOK_VERIFY_TOKEN:
        logger.info("Webhook verifisert")
        return Response(content=hub_challenge, media_type="text/plain")
    logger.warning("Webhook-verifisering feilet (feil token?)")
    raise HTTPException(status_code=403, detail="Feil verify token")


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """Mottar innkommende meldinger fra Meta Cloud API."""
    data = await request.json()

    # Meta forventer alltid 200 OK raskt – prosessering skjer i bakgrunnen
    background_tasks.add_task(handle_messages, data)
    return {"status": "ok"}


async def handle_messages(data: dict) -> None:
    """Prosesserer innkommende meldinger og sender svar."""
    messages = parse_incoming(data)
    for msg in messages:
        sender = msg["from"]
        text = msg["text"]
        media_id = msg["media_id"]
        mime_type = msg["mime_type"]

        logger.info("Melding fra %s: '%s' media=%s", sender, text[:80], bool(media_id))

        try:
            # Last ned media hvis det finnes
            media = None
            if media_id:
                raw_bytes, actual_mime = await download_media(media_id)
                media = type("Media", (), {
                    "mimetype": actual_mime or mime_type,
                    "data": base64.b64encode(raw_bytes).decode(),
                    "filename": None,
                })()

            reply = await process_message(sender=sender, text=text, media=media)

            # Send svar til alle familiemedlemmer, eller bare avsenderen hvis listen er tom
            recipients = FAMILY_NUMBERS if FAMILY_NUMBERS else [sender]
            for recipient in recipients:
                await send_message(to=recipient, text=reply)

        except Exception:
            logger.exception("Feil ved behandling av melding fra %s", sender)
            await send_message(to=sender, text="Beklager, noe gikk galt. Prøv igjen om litt.")
