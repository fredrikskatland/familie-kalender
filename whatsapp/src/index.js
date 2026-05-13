const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const fs = require('fs');
const path = require('path');

// Fjern Chromium SingletonLock-filer rekursivt (kan ligge i undermapper)
const LOCK_FILES = new Set(['SingletonLock', 'SingletonCookie', 'SingletonSocket']);
function cleanChromiumLocks(dir) {
  try {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        cleanChromiumLocks(fullPath);
      } else if (LOCK_FILES.has(entry.name)) {
        fs.unlinkSync(fullPath);
        console.log(`Fjernet stale lock-fil: ${fullPath}`);
      }
    }
  } catch (e) {
    // Ikke kritisk, fortsett uansett
  }
}
cleanChromiumLocks('/app/.wwebjs_auth');

const AGENT_URL = process.env.AGENT_URL || 'http://agent:8000';
const ALLOWED_NUMBERS = (process.env.ALLOWED_NUMBERS || '')
  .split(',')
  .map(n => n.trim().replace('+', ''))
  .filter(Boolean);
const GROUP_NAME = (process.env.GROUP_NAME || '').trim();

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: '/app/.wwebjs_auth' }),
  puppeteer: {
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium',
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ],
  },
});

client.on('qr', (qr) => {
  console.log('\n=== Skann QR-koden med WhatsApp ===\n');
  qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
  console.log('WhatsApp-klient er klar!');
  console.log(`Lytter på gruppe: "${GROUP_NAME || '(alle)'}"`);
  console.log(`Godkjente numre: ${ALLOWED_NUMBERS.join(', ') || '(alle)'}`);
});

client.on('auth_failure', (msg) => {
  console.error('Autentisering feilet:', msg);
});

// Holder styr på meldinger boten selv har sendt som svar (unngå loop)
const botReplies = new Set();

// message_create fanger opp alle meldinger, inkl. egne (for at bot-eier skal kunne bruke boten)
client.on('message_create', async (message) => {
  if (message.from === 'status@broadcast') return;

  // Ignorer kun meldinger som er bot-svar (ikke meldinger bot-eier selv skriver)
  if (message.fromMe && botReplies.has(message.id._serialized)) {
    botReplies.delete(message.id._serialized);
    return;
  }

  const isGroup = message.from.endsWith('@g.us');
  const senderNumber = isGroup
    ? (message.author || '').replace('@c.us', '')
    : message.from.replace('@c.us', '');

  // Debug: vis alle gruppemeldinger med gruppenavn
  if (isGroup) {
    const chat = await message.getChat();
    console.log(`[DEBUG] Gruppe: "${chat.name}" | Avsender: ${senderNumber} | Tekst: ${message.body || '[media]'}`);

    // Sjekk om det er riktig gruppe
    if (GROUP_NAME && chat.name !== GROUP_NAME) return;
  }

  // Ignorer direktemeldinger hvis GROUP_NAME er satt
  if (!isGroup && GROUP_NAME) return;

  // For direktemeldinger: sjekk at avsender er godkjent
  // For gruppemeldinger er gruppemedlemskap selve sikkerhetsgrensen
  if (!isGroup && ALLOWED_NUMBERS.length > 0 && !ALLOWED_NUMBERS.includes(senderNumber)) {
    console.log(`Ignorerer direktemelding fra ukjent nummer: ${senderNumber}`);
    return;
  }

  console.log(`Behandler melding fra ${senderNumber}: ${message.body || '[media]'}`);

  try {
    const payload = {
      from: senderNumber,
      text: message.body || '',
      media: null,
    };

    if (message.hasMedia) {
      const media = await message.downloadMedia();
      if (media) {
        payload.media = {
          mimetype: media.mimetype,
          data: media.data,
          filename: media.filename || null,
        };
      }
    }

    const response = await axios.post(`${AGENT_URL}/message`, payload, {
      timeout: 60000,
    });

    const replyText = response.data?.reply;
    if (replyText) {
      const sent = await message.reply(replyText);
      // Registrer bot-svaret så vi ikke behandler det på nytt
      if (sent?.id?._serialized) {
        botReplies.add(sent.id._serialized);
        setTimeout(() => botReplies.delete(sent.id._serialized), 30000);
      }
    }
  } catch (err) {
    console.error('Feil ved kommunikasjon med agenten:', err.message);
    await message.reply('Beklager, noe gikk galt. Prøv igjen om litt.');
  }
});

client.initialize();
