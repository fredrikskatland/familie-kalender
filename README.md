# Familie Kalender – WhatsApp Bot (Meta Cloud API)

WhatsApp-bot med eget botnummer som leser meldinger, bilder og PDF-er fra skole og barnehage, og oppdaterer en delt Google Calendar automatisk via GPT-4o.

## Arkitektur

```text
Bruker/Samboer (WhatsApp)
        |
  Meta Cloud API
        | webhook (HTTPS)
  Cloudflare Tunnel
        |
  Python Agent (FastAPI)
        |          |
   OpenAI API   Google Calendar API
```

Én Docker-container (Python) + Cloudflare Tunnel. Ingen Node.js, ingen Chromium.

---

## Steg 1: Google Calendar (Service Account)

1. Gå til [Google Cloud Console](https://console.cloud.google.com)
2. Opprett prosjekt → aktiver **Google Calendar API**
3. "Credentials" → "Service Account" → last ned JSON-nøkkel
4. Lagre som `agent/credentials/service_account.json`
5. Opprett en ny Google Calendar → del den med service account-e-postadressen (tilgang: "Make changes to events")
6. Kopier **Calendar ID** fra kalenderinnstillingene

---

## Steg 2: Meta WhatsApp Cloud API (~20 min)

1. Gå til [developers.facebook.com](https://developers.facebook.com) → "My Apps" → "Create App"
2. Velg **Business** som app-type
3. Legg til **WhatsApp**-produktet
4. Under "API Setup":
   - Kopier **Phone Number ID** → `WHATSAPP_PHONE_ID` i `.env`
   - Klikk "Generate token" (eller lag permanent token via System User) → `WHATSAPP_TOKEN`
5. Legg til mottaker-numre under "To" (inntil 5 på test-tier) – ditt og samboerens nummer
6. Send en testmelding for å verifisere at nummeret er aktivert

> For permanent drift: legg til et ekte norsk nummer under "Phone Numbers" (krever verifisering). Eller bruk testnummeret – det fungerer fint for familibruk.

---

## Steg 3: Cloudflare Tunnel (gratis, permanent URL)

1. Opprett gratis konto på [cloudflare.com](https://cloudflare.com)
2. Gå til **Zero Trust** → **Networks** → **Tunnels** → "Create a tunnel"
3. Gi tunnelen et navn, f.eks. "familie-kalender"
4. Velg **Docker** som installasjon – kopier token → `CLOUDFLARE_TUNNEL_TOKEN` i `.env`
5. Under "Public Hostname":
   - Subdomain: `kalender` (eller hva du vil)
   - Domain: velg ditt Cloudflare-domene (eller bruk gratis `<navn>.cfargotunnel.com`)
   - Service: `http://agent:8000`
6. Lagre – du får en permanent HTTPS-URL, f.eks. `https://kalender.dindomene.no`

---

## Steg 4: Konfigurer Meta webhook

1. Tilbake i Meta Developer Dashboard → WhatsApp → Configuration
2. "Webhook" → "Edit"
   - **Callback URL**: `https://kalender.dindomene.no/webhook`
   - **Verify token**: samme verdi som `WEBHOOK_VERIFY_TOKEN` i `.env`
3. Klikk "Verify and Save"
4. Klikk "Manage" → aktiver **messages**-abonnementet

---

## Steg 5: Konfigurer og start

Kopier `.env.example` til `.env` og fyll inn alle verdier:

```bash
cp .env.example .env
```

Start:

```bash
docker compose up --build
```

---

## Bruk

Send meldinger direkte til botnummeret fra din og samboerens WhatsApp:

- `"Foreldremøte 3. juni kl 18:00"` → oppretter hendelse
- Send bilde av informasjonsskriv → oppretter alle hendelser automatisk
- Send PDF med terminplan → oppretter alle datoer
- `"Hva skjer denne uken?"` → lister hendelser

Boten svarer på norsk og er proaktiv – oppretter hendelser uten å spørre om bekreftelse.

---

## Logging og debugging

Stream live-logger fra agenten:

```bash
docker compose logs -f agent
```

For å se prompts, tool-argumenter og svar – sett `LOG_LEVEL=DEBUG` i `.env` og restart:

```bash
docker compose up -d
```

Tilbake til normal: sett `LOG_LEVEL=INFO`.

---

## Filstruktur

```text
familie-kalender/
├── docker-compose.yml
├── .env
├── .env.example
└── agent/
    ├── Dockerfile
    ├── requirements.txt
    ├── main.py              # FastAPI + Meta webhook
    ├── meta_service.py      # Meta Cloud API (send/motta)
    ├── llm_service.py       # GPT-4o agent med tool use
    ├── calendar_service.py  # Google Calendar
    └── credentials/
        └── service_account.json
```
