const TelegramBot = require('node-telegram-bot-api');
const pino = require('pino');

const {
  default: makeWASocket,
  useMultiFileAuthState
} = require('@whiskeysockets/baileys');

// 🔑 তোমার Telegram Bot Token বসাও
const bot = new TelegramBot('8216427126:AAE-bmVHoQFr0zDefu0wW-DOxzdJWPKllQs', { polling: true });

// Start command
bot.onText(/\/start/, (msg) => {
  bot.sendMessage(
    msg.chat.id,
    "📲 Send WhatsApp number:\n\nFormat:\n880173XXXXXXX"
  );
});

// Handle user input
bot.on('message', async (msg) => {
  const chatId = msg.chat.id;

  if (!msg.text || msg.text.startsWith('/')) return;

  let number = msg.text.trim();

  // clean number
  number = number.replace(/\D/g, ''); // remove spaces + symbols

  if (number.length < 10) {
    return bot.sendMessage(chatId, "❌ Invalid number format.");
  }

  try {
    const { state, saveCreds } = await useMultiFileAuthState(`./sessions/${chatId}`);

    const sock = makeWASocket({
      auth: state,
      printQRInTerminal: false,
      logger: pino({ level: 'silent' }),
      browser: ['Ubuntu', 'Chrome', '20.0.04']
    });

    sock.ev.on('creds.update', saveCreds);

    // 🔥 Delay fix (IMPORTANT)
    setTimeout(async () => {
      try {
        console.log("Requesting pairing for:", number);

        const code = await sock.requestPairingCode(number);

        await bot.sendMessage(
          chatId,
          `🔗 WhatsApp REAL Linking Code:\n\n${code}\n\n` +
          `➡️ Open WhatsApp\n` +
          `➡️ Linked Devices\n` +
          `➡️ Link with phone number\n` +
          `➡️ Enter this code`
        );

      } catch (err) {
        console.log("PAIR ERROR:", err);
        bot.sendMessage(chatId, "❌ Failed to generate real linking code.");
      }
    }, 4000); // ⏳ wait for socket ready

    // connection update
    sock.ev.on('connection.update', (update) => {
      const { connection } = update;

      if (connection === 'open') {
        bot.sendMessage(chatId, "✅ WhatsApp linked successfully!");
      }
    });

  } catch (err) {
    console.log(err);
    bot.sendMessage(chatId, "❌ Error occurred.");
  }
});
