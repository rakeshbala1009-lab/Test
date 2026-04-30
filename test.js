const TelegramBot = require('node-telegram-bot-api');
const pino = require('pino');

const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason
} = require('@whiskeysockets/baileys');

const bot = new TelegramBot('8216427126:AAE-bmVHoQFr0zDefu0wW-DOxzdJWPKllQs', { polling: true });

// Start message
bot.onText(/\/start/, (msg) => {
  bot.sendMessage(
    msg.chat.id,
    "Send your WhatsApp number:\n\nFormat:\n<countrycode> <number>\nExample:\n880 1XXXXXXXXX"
  );
});

// Handle number
bot.on('message', async (msg) => {
  const chatId = msg.chat.id;
  if (!msg.text || msg.text.startsWith('/')) return;

  const parts = msg.text.trim().split(" ");
  if (parts.length !== 2) {
    return bot.sendMessage(chatId, "❌ Invalid format.\nUse: 880 1XXXXXXXXX");
  }

  const number = parts.join('');

  try {
    const { state, saveCreds } = await useMultiFileAuthState(`./sessions/${chatId}`);

    const sock = makeWASocket({
      auth: state,
      logger: pino({ level: 'silent' }),
      browser: ['TelegramBot', 'Chrome', '1.0']
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect } = update;

      if (connection === 'open') {
        bot.sendMessage(chatId, "✅ WhatsApp linked successfully!");
      }

      if (connection === 'close') {
        const reason = lastDisconnect?.error?.output?.statusCode;
        if (reason === DisconnectReason.loggedOut) {
          bot.sendMessage(chatId, "❌ Session logged out.");
        }
      }
    });

    // 🔑 THIS IS THE REAL WHATSAPP LINKING CODE
    const code = await sock.requestPairingCode(number);

    bot.sendMessage(
      chatId,
      `🔗 REAL WhatsApp Linking Code:\n\n${code}\n\n` +
      `➡️ Open WhatsApp\n` +
      `➡️ Linked Devices\n` +
      `➡️ Link with phone number\n` +
      `➡️ Enter this code`
    );

  } catch (err) {
    console.error(err);
    bot.sendMessage(chatId, "❌ Failed to generate real linking code.");
  }
});
