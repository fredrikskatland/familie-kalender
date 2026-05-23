import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from llm_service import MODEL, client
from calendar_service import CalendarService
from meta_service import send_message

logger = logging.getLogger(__name__)

DAILY_SUMMARY_TIME = os.environ.get("DAILY_SUMMARY_TIME", "20:00")
FAMILY_NUMBERS = [n.strip() for n in os.environ.get("FAMILY_NUMBERS", "").split(",") if n.strip()]
TIMEZONE = ZoneInfo("Europe/Oslo")

PROMPT_FILE = os.path.join(os.path.dirname(__file__), "daily_summary_prompt.md")

WEEKDAYS_NO = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
MONTHS_NO = ["januar", "februar", "mars", "april", "mai", "juni",
             "juli", "august", "september", "oktober", "november", "desember"]

calendar = CalendarService()


def _load_prompt() -> str:
    with open(PROMPT_FILE, encoding="utf-8") as f:
        return f.read()


def _seconds_until_next(hour: int, minute: int) -> float:
    now = datetime.now(TIMEZONE)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _format_date(dt: datetime) -> str:
    return f"{WEEKDAYS_NO[dt.weekday()]} {dt.day}. {MONTHS_NO[dt.month - 1]}"


async def daily_summary_loop():
    h, m = map(int, DAILY_SUMMARY_TIME.split(":"))
    while True:
        wait = _seconds_until_next(h, m)
        logger.info("Daglig oppsummering: neste kjøring om %.0f sekunder (%.1f timer)", wait, wait / 3600)
        await asyncio.sleep(wait)
        try:
            await send_daily_summary()
        except Exception:
            logger.exception("Feil ved daglig oppsummering")


async def send_daily_summary():
    now = datetime.now(TIMEZONE)
    tomorrow = now + timedelta(days=1)

    # Fredag kveld: inkluder lørdag og søndag
    if now.weekday() == 4:
        days_ahead = 2
        period_label = f"i helgen (lørdag {tomorrow.day}. og søndag {(now + timedelta(days=2)).day}. {MONTHS_NO[tomorrow.month - 1]})"
    else:
        days_ahead = 1
        period_label = f"i morgen ({_format_date(tomorrow)})"

    events = calendar.list_events(days_from=1, days_ahead=days_ahead)
    events_text = json.dumps(events, ensure_ascii=False, indent=2) if events else "Ingen hendelser registrert."

    prompt = _load_prompt().format(period_label=period_label, events_json=events_text)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=512,
    )

    message = response.choices[0].message.content
    logger.info("Daglig oppsummering: %s", message[:120])

    for number in FAMILY_NUMBERS:
        await send_message(to=number, text=message)
