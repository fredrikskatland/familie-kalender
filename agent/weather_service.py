import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

TIMEZONE = ZoneInfo("Europe/Oslo")
WEATHER_LAT = float(os.environ.get("WEATHER_LAT", "59.928"))
WEATHER_LON = float(os.environ.get("WEATHER_LON", "11.173"))
MET_API_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
USER_AGENT = "familie-kalender/1.0 github.com/fredrikskatland/familie-kalender"

SYMBOL_MAP = {
    "clearsky":            "☀️ klarvær",
    "fair":                "🌤️ lettskyet",
    "partlycloudy":        "⛅ delvis skyet",
    "cloudy":              "☁️ overskyet",
    "fog":                 "🌫️ tåke",
    "lightrain":           "🌦️ lett regn",
    "rain":                "🌧️ regn",
    "heavyrain":           "🌧️ kraftig regn",
    "lightrainshowers":    "🌦️ lette regnbyger",
    "rainshowers":         "🌦️ regnbyger",
    "heavyrainshowers":    "🌧️ kraftige regnbyger",
    "lightsleet":          "🌨️ lett sludd",
    "sleet":               "🌨️ sludd",
    "heavysleet":          "🌨️ kraftig sludd",
    "lightsnow":           "❄️ lett snø",
    "snow":                "❄️ snø",
    "heavysnow":           "❄️ kraftig snø",
    "thunderstorm":        "⛈️ tordenvær",
    "lightrainandthunder": "⛈️ lett regn og torden",
    "rainandthunder":      "⛈️ regn og torden",
}


def _symbol_to_text(symbol_code: str) -> str:
    """Konverterer yr-symbolkode til lesbar norsk tekst."""
    if not symbol_code:
        return ""
    base = symbol_code.split("_")[0]
    # Eksakt treff først
    if base in SYMBOL_MAP:
        return SYMBOL_MAP[base]
    # Prefiks-treff
    for key, val in SYMBOL_MAP.items():
        if base.startswith(key):
            return val
    return base


def _fetch_timeseries() -> list:
    with httpx.Client(timeout=10) as client:
        resp = client.get(
            MET_API_URL,
            params={"lat": WEATHER_LAT, "lon": WEATHER_LON},
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
    return resp.json()["properties"]["timeseries"]


def _summarise_day(timeseries: list, target_date) -> str:
    """Returnerer en kort værtekst for én dato."""
    temps = []
    precip_total = 0.0
    symbols = []

    for entry in timeseries:
        t = datetime.fromisoformat(entry["time"].replace("Z", "+00:00")).astimezone(TIMEZONE)
        if t.date() != target_date:
            continue

        instant = entry["data"].get("instant", {}).get("details", {})
        temp = instant.get("air_temperature")
        if temp is not None:
            temps.append(temp)

        next6 = entry["data"].get("next_6_hours", {})
        precip = next6.get("details", {}).get("precipitation_amount")
        if precip is not None:
            precip_total += precip

        symbol = next6.get("summary", {}).get("symbol_code")
        if symbol:
            symbols.append(symbol)

    if not temps:
        return ""

    temp_min = round(min(temps))
    temp_max = round(max(temps))
    dominant_symbol = symbols[len(symbols) // 2] if symbols else ""
    weather_text = _symbol_to_text(dominant_symbol)

    parts = [f"{weather_text}, {temp_min}–{temp_max}°C"]
    if precip_total >= 0.5:
        parts.append(f"{round(precip_total, 1)} mm nedbør")

    return ", ".join(parts)


def get_weather(days_ahead: int = 1) -> str:
    """
    Henter og returnerer en lesbar værtekst for antall dager frem i tid.
    - days_ahead=1 → i morgen
    - days_ahead=2 → i morgen + dagen etter (brukes for helge-oppsummering)
    """
    try:
        timeseries = _fetch_timeseries()
        now = datetime.now(TIMEZONE)

        if days_ahead == 1:
            target = (now + timedelta(days=1)).date()
            summary = _summarise_day(timeseries, target)
            logger.info("Vær for %s: %s", target, summary)
            return summary

        # Helg: lørdag og søndag
        results = []
        for offset in range(1, days_ahead + 1):
            target = (now + timedelta(days=offset)).date()
            day_summary = _summarise_day(timeseries, target)
            if day_summary:
                from calendar import day_abbr
                day_names = ["man", "tir", "ons", "tor", "fre", "lør", "søn"]
                label = day_names[target.weekday()]
                results.append(f"{label}: {day_summary}")
            logger.info("Vær for %s: %s", target, day_summary)
        return " | ".join(results)

    except Exception:
        logger.exception("Feil ved værhenting fra yr.no")
        return ""
