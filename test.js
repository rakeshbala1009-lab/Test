const TelegramBot = require('node-telegram-bot-api');
const axios = require('axios');
const fs = require('fs');
const path = require('path');
const { Client: WhatsAppClient, LocalAuth } = require('whatsapp-web.js');
const QRCode = require('qrcode');
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  delay
} = require('@whiskeysockets/baileys');

// ═══════════════ CONFIG ═══════════════
const TELEGRAM_TOKEN = '8752592084:AAHjk_eHKfx0O3h7dGU6esH0K_jOgy3I2QI';

// ═══════════════ STATE ═══════════════
let expectingMaytapiUrl = false;
let expectingNumberForPairing = false;
let isConnected = false;
let activeBackend = null;                // 'maytapi' | 'qr' | 'baileys'
let activeMaytapi = null;
let activeQRClient = null;
let activeBaileysClient = null;

// Maytapi pool
let apiPool = [];
let activeStatusPollInterval = null;

// Number checking
let checkingChatId = null;
let checkingMessageId = null;
let checkingTotal = 0;
let checkingDone = 0;

// QR message tracking
let qrMessageId = null;

const bot = new TelegramBot(TELEGRAM_TOKEN, { polling: true });

// ─── KBS‑styled reply keyboard ───
function getMainKeyboard() {
    return {
        reply_markup: {
            keyboard: [
                [{ text: '🔗 Connect WhatsApp', style: 'primary' }],
                [{ text: '🔌 Disconnect', style: 'danger' },
                 { text: '📂 Check WhatsApp', style: 'success' }]
            ],
            resize_keyboard: true,
            one_time_keyboard: false
        }
    };
}

// ─── Helpers ───
function cleanNumber(raw) { return raw.replace(/\D/g, ''); }
function parseMaytapiUrl(url) {
    const m = url.match(/\/api\/([^\/]+)\/([^\/]+)\/(?:screen|status|qrCode)(?:\?|$)/);
    if (!m) return null;
    const urlObj = new URL(url);
    const token = urlObj.searchParams.get('token');
    if (!token) return null;
    return { productId: m[1], phoneId: m[2], token };
}

// ─── Maytapi helpers ───
async function maytapiCheckNumber(api, phone) {
    const res = await axios.get(`https://api.maytapi.com/api/${api.productId}/${api.phoneId}/checkNumberStatus`, {
        params: { token: api.token, number: `${phone}@c.us` }
    });
    return res.data?.result?.status === 200;
}
async function maytapiGetStatus(api) {
    const res = await axios.get(`https://api.maytapi.com/api/${api.productId}/${api.phoneId}/status?token=${api.token}`);
    return res.data;
}
function maytapiIsConnected(raw) {
    if (!raw) return false;
    if (raw.status?.loggedIn) return true;
    if (raw.status?.state?.state === 'CONNECTED') return true;
    if (raw.connected) return true;
    return false;
}
async function trySetActiveMaytapi() {
    if (activeStatusPollInterval) clearInterval(activeStatusPollInterval);
    if (activeMaytapi) {
        try {
            const st = await maytapiGetStatus(activeMaytapi);
            if (maytapiIsConnected(st)) return true;
        } catch {}
        apiPool = apiPool.filter(a => a.productId !== activeMaytapi.productId || a.phoneId !== activeMaytapi.phoneId);
        activeMaytapi = null;
    }
    for (let i = 0; i < apiPool.length; i++) {
        try {
            const st = await maytapiGetStatus(apiPool[i]);
            if (maytapiIsConnected(st)) {
                activeMaytapi = apiPool.splice(i, 1)[0];
                isConnected = true;
                activeStatusPollInterval = setInterval(async () => {
                    try {
                        const st2 = await maytapiGetStatus(activeMaytapi);
                        if (!maytapiIsConnected(st2)) await trySetActiveMaytapi();
                    } catch { await trySetActiveMaytapi(); }
                }, 10000);
                return true;
            }
        } catch {}
    }
    isConnected = false;
    return false;
}

// ─── QR Client ───
async function sendQRForClient(chatId, client) {
    if (qrMessageId) { try { await bot.deleteMessage(chatId, qrMessageId); } catch {} qrMessageId = null; }
    const qrPromise = new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('QR timeout')), 45000);
        client.once('qr', (qr) => { clearTimeout(timeout); resolve(qr); });
        client.once('ready', () => { clearTimeout(timeout); reject(new Error('already_authenticated')); });
    });
    try {
        const qr = await qrPromise;
        const qrImage = await QRCode.toBuffer(qr, { type: 'png', width: 400 });
        const imgPath = path.join(__dirname, `qr_${Date.now()}.png`);
        fs.writeFileSync(imgPath, qrImage);
        const sent = await bot.sendPhoto(chatId, imgPath, {
            caption: '📷 Scan this QR code with your WhatsApp\n(Linked Devices → Link a Device)',
            reply_markup: { inline_keyboard: [[{ text: '🔄 New QR', callback_data: 'new_qr' }]] }
        });
        qrMessageId = sent.message_id;
        fs.unlinkSync(imgPath);
    } catch (e) {
        if (e.message === 'already_authenticated') bot.sendMessage(chatId, '✅ Already authenticated.');
        else bot.sendMessage(chatId, '❌ Failed to generate QR code.');
    }
}
function startQRLogin(chatId) {
    if (activeQRClient) { sendQRForClient(chatId, activeQRClient).catch(() => {}); return; }
    const client = new WhatsAppClient({
        authStrategy: new LocalAuth({ clientId: 'bot_session' }),
        puppeteer: { headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox'] }
    });
    activeQRClient = client;
    client.on('ready', async () => {
        activeBackend = 'qr';
        isConnected = true;
        const me = client.info.me?.user || client.info.wid?.user || '';
        bot.sendMessage(chatId, me ? `✅ QR login successful! Connected as +${me}.` : '✅ QR login successful!');
        if (activeMaytapi) { activeMaytapi = null; clearInterval(activeStatusPollInterval); }
        if (qrMessageId) { try { await bot.deleteMessage(chatId, qrMessageId); } catch {} qrMessageId = null; }
    });
    client.on('disconnected', (reason) => {
        console.log('QR client disconnected:', reason);
        if (activeBackend === 'qr') { activeBackend = null; isConnected = false; }
        client.destroy().catch(() => {});
        activeQRClient = null;
        qrMessageId = null;
    });
    client.initialize()
        .then(() => { sendQRForClient(chatId, client).catch(() => {}); })
        .catch(err => { console.error('QR init error:', err); bot.sendMessage(chatId, '❌ Failed to start QR login.'); activeQRClient = null; });
}

// ─── Baileys Pairing Code (FIXED – real code, no premature request) ───
async function startBaileysPairing(chatId, phoneNumber) {
    if (activeBaileysClient) {
        try { activeBaileysClient.end(); } catch {}
        activeBaileysClient = null;
    }

    const sessionDir = path.join(__dirname, `session_${chatId}`);
    try { fs.rmSync(sessionDir, { recursive: true, force: true }); } catch {}

    const { state, saveCreds } = await useMultiFileAuthState(sessionDir);

    const sock = makeWASocket({
        auth: state,
        printQRInTerminal: false,
        browser: ['Chrome (Linux)', '', ''],   // match working API
        logger: undefined
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect } = update;
        if (connection === 'open') {
            activeBackend = 'baileys';
            isConnected = true;
            activeBaileysClient = sock;
            const me = sock.user?.id?.split(':')[0] || '';
            bot.sendMessage(chatId, `✅ Linked! Connected as +${me || 'unknown'}.`);
        }
        if (connection === 'close') {
            const statusCode = lastDisconnect?.error?.output?.statusCode;
            if (statusCode === DisconnectReason.loggedOut) {
                if (activeBaileysClient === sock) {
                    activeBaileysClient = null;
                    if (activeBackend === 'baileys') { activeBackend = null; isConnected = false; }
                    bot.sendMessage(chatId, '🔌 Session logged out.');
                }
            }
        }
    });

    try {
        // Crucial: wait long enough for the socket to fully initialise
        await delay(1600);
        const code = await sock.requestPairingCode(phoneNumber);
        const display = code.length === 8 ? `${code.slice(0,4)}-${code.slice(4)}` : code;
        bot.sendMessage(chatId,
            `🔐 *Your real WhatsApp linking code*\n\n` +
            `\`${display}\`\n\n` +
            `Type **${code}** (no dash) on that phone:\n` +
            `WhatsApp → Linked Devices → Link a Device`,
            { parse_mode: 'Markdown' }
        );
    } catch (err) {
        console.error('requestPairingCode error:', err);
        bot.sendMessage(chatId, '❌ Failed to get pairing code. Try again later.');
        try { sock.end(); } catch {}
        activeBaileysClient = null;
    }
}

async function disconnectActive(chatId) {
    if (activeBackend === 'maytapi' && activeMaytapi) {
        activeMaytapi = null;
        if (activeStatusPollInterval) clearInterval(activeStatusPollInterval);
    }
    if (activeBackend === 'qr' && activeQRClient) {
        try { await activeQRClient.logout(); } catch {}
        try { activeQRClient.destroy(); } catch {}
        activeQRClient = null;
        qrMessageId = null;
    }
    if (activeBackend === 'baileys' && activeBaileysClient) {
        try { activeBaileysClient.end(); } catch {}
        activeBaileysClient = null;
    }
    activeBackend = null;
    isConnected = false;
    bot.sendMessage(chatId, '🔌 Disconnected successfully.');
}

// ═══════════════ BOT HANDLERS ═══════════════
bot.onText(/\/start/, msg => bot.sendMessage(msg.chat.id, 'Welcome.', getMainKeyboard()));

bot.on('message', async msg => {
    const chatId = msg.chat.id;
    const text = msg.text;
    if (!text) return;

    if (text === '🔗 Connect WhatsApp') {
        bot.sendMessage(chatId, 'Choose connection method:', {
            reply_markup: {
                inline_keyboard: [
                    [{ text: '🌐 Maytapi', callback_data: 'connect_maytapi' }],
                    [{ text: '📱 QR Code Login', callback_data: 'connect_qr' }],
                    [{ text: '🔑 Pairing Code Login', callback_data: 'connect_pairing' }]
                ]
            }
        });
        return;
    }

    if (text === '🔌 Disconnect') { await disconnectActive(chatId); return; }

    if (text === '📂 Check WhatsApp') {
        if (!isConnected) bot.sendMessage(chatId, '⚠️ No active WhatsApp connection.');
        else bot.sendMessage(chatId, '📄 Send a .txt file with one number per line.');
        return;
    }

    if (expectingMaytapiUrl) {
        expectingMaytapiUrl = false;
        const parsed = parseMaytapiUrl(text.trim());
        if (!parsed) { bot.sendMessage(chatId, '❌ Invalid Maytapi URL.'); return; }
        apiPool.push({ ...parsed, chatId });
        const ok = await trySetActiveMaytapi();
        if (ok) {
            activeBackend = 'maytapi'; isConnected = true;
            const st = await maytapiGetStatus(activeMaytapi);
            const num = st.status?.number || st.number || '';
            bot.sendMessage(chatId, `✅ Maytapi connected (+${num}).`);
        } else {
            bot.sendMessage(chatId, '📌 Maytapi added to pool. Waiting for connection…');
        }
        return;
    }

    if (expectingNumberForPairing) {
        expectingNumberForPairing = false;
        const phoneNumber = cleanNumber(text);
        if (phoneNumber.length < 10) {
            bot.sendMessage(chatId, '❌ Invalid number. Must include country code.');
            expectingNumberForPairing = true;
            return;
        }
        bot.sendMessage(chatId, '⏳ Requesting pairing code from WhatsApp…');
        startBaileysPairing(chatId, phoneNumber).catch(err => {
            console.error(err);
            bot.sendMessage(chatId, '❌ Pairing failed.');
        });
        return;
    }
});

bot.on('callback_query', async query => {
    const chatId = query.message.chat.id;
    const data = query.data;

    if (data === 'connect_maytapi') { bot.answerCallbackQuery(query.id); expectingMaytapiUrl = true; bot.sendMessage(chatId, '🔗 Send Maytapi screen URL...'); }
    if (data === 'connect_qr') { bot.answerCallbackQuery(query.id); bot.sendMessage(chatId, '⏳ Starting QR login…'); startQRLogin(chatId); }
    if (data === 'connect_pairing') { bot.answerCallbackQuery(query.id); expectingNumberForPairing = true; bot.sendMessage(chatId, '📱 Send the WhatsApp number (with country code).'); }
    if (data === 'new_qr') {
        bot.answerCallbackQuery(query.id);
        if (!activeQRClient) { bot.sendMessage(chatId, '⚠️ No QR session active.'); return; }
        if (qrMessageId) { try { await bot.deleteMessage(chatId, qrMessageId); } catch {} qrMessageId = null; }
        sendQRForClient(chatId, activeQRClient).catch(() => {});
    }
});

// ─── Progress updater ───
async function updateProgress() {
    if (!checkingMessageId) return;
    try {
        await bot.editMessageText(
            `Number checking ${checkingDone}/${checkingTotal}`,
            { chat_id: checkingChatId, message_id: checkingMessageId }
        );
    } catch {}
}

// ─── Check functions ───
async function checkWithMaytapi(numbers) {
    const registered = [], fresh = [];
    for (let raw of numbers) {
        const c = cleanNumber(raw); if (!c) continue;
        const ok = await maytapiCheckNumber(activeMaytapi, c);
        if (ok) registered.push(c); else fresh.push(c);
        checkingDone++; await updateProgress();
    }
    return { registered, fresh };
}

async function checkWithQR(numbers) {
    const registered = [], fresh = [];
    for (let raw of numbers) {
        const c = cleanNumber(raw); if (!c) continue;
        try {
            const jid = await activeQRClient.getNumberId(`${c}@c.us`);
            if (jid) registered.push(c); else fresh.push(c);
        } catch { fresh.push(c); }
        checkingDone++; await updateProgress();
    }
    return { registered, fresh };
}

async function checkWithBaileys(numbers) {
    const registered = [], fresh = [];
    for (let raw of numbers) {
        const c = cleanNumber(raw); if (!c) continue;
        try {
            const infos = await activeBaileysClient.onWhatsApp(`${c}@s.whatsapp.net`);
            if (infos && infos.length > 0) registered.push(c); else fresh.push(c);
        } catch { fresh.push(c); }
        checkingDone++; await updateProgress();
    }
    return { registered, fresh };
}

// ─── File check handler ───
bot.on('document', async msg => {
    const chatId = msg.chat.id;
    if (!isConnected) return bot.sendMessage(chatId, '❌ No active connection.');

    try {
        const filePath = await bot.downloadFile(msg.document.file_id, './');
        const content = fs.readFileSync(filePath, 'utf-8');
        const numbers = content.split(/\r?\n/).map(l => l.trim()).filter(l => l);
        fs.unlinkSync(filePath);

        checkingChatId = chatId;
        checkingTotal = numbers.length;
        checkingDone = 0;

        const progressMsg = await bot.sendMessage(chatId, `Number checking 0/${checkingTotal}`);
        checkingMessageId = progressMsg.message_id;

        let results;
        if (activeBackend === 'maytapi') results = await checkWithMaytapi(numbers);
        else if (activeBackend === 'qr') results = await checkWithQR(numbers);
        else if (activeBackend === 'baileys') results = await checkWithBaileys(numbers);
        else { bot.sendMessage(chatId, 'Unknown backend'); return; }

        await bot.deleteMessage(chatId, checkingMessageId).catch(() => {});

        let report = '';
        if (results.registered.length)
            report += '*Already Created Account Number ✅:*\n' + results.registered.join('\n') + '\n\n';
        if (results.fresh.length)
            report += '*Fresh Number ❌*\n' + results.fresh.map(n => `+${n}`).join('\n');
        if (report) bot.sendMessage(chatId, report, { parse_mode: 'Markdown' });

        if (results.fresh.length) {
            const freshPath = path.join(__dirname, 'Freash_Number.txt');
            fs.writeFileSync(freshPath, results.fresh.map(n => `+${n}`).join('\n'), 'utf-8');
            await bot.sendDocument(chatId, freshPath, { caption: `Fresh numbers (${results.fresh.length})` });
            fs.unlinkSync(freshPath);
        } else {
            bot.sendMessage(chatId, 'No fresh numbers found ✅');
        }

    } catch (err) {
        console.error(err);
        bot.sendMessage(chatId, '❌ Error processing file.');
    }
});

console.log('Bot running – Real pairing code, number checking ready');
