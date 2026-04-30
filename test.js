const TelegramBot = require('node-telegram-bot-api');
const axios = require('axios');
const fs = require('fs');
const path = require('path');
const { Client: WhatsAppClient, LocalAuth } = require('whatsapp-web.js');
const QRCode = require('qrcode');

// ═══════════════ CONFIG ═══════════════
const TELEGRAM_TOKEN = '8216427126:AAGRORmkkd9aL-svdp0F5lkfTWLLP9q6n1Q';

// ═══════════════ STATE ═══════════════
let expectingMaytapiUrl = false;
let isConnected = false;
let activeBackend = null;               // 'maytapi' | 'qr'
let activeMaytapi = null;
let activeQRClient = null;

// Maytapi pool
let apiPool = [];
let activeStatusPollInterval = null;

// Number checking
let checkingChatId = null;
let checkingMessageId = null;
let checkingTotal = 0;
let checkingDone = 0;

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

// ─── Maytapi helpers ───
function cleanNumber(raw) { return raw.replace(/\D/g, ''); }
function parseMaytapiUrl(url) {
    const m = url.match(/\/api\/([^\/]+)\/([^\/]+)\/(?:screen|status|qrCode)(?:\?|$)/);
    if (!m) return null;
    const urlObj = new URL(url);
    const token = urlObj.searchParams.get('token');
    if (!token) return null;
    return { productId: m[1], phoneId: m[2], token };
}

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
function startQRLogin(chatId) {
    if (activeQRClient) {
        try { activeQRClient.destroy(); } catch {}
        activeQRClient = null;
    }

    const client = new WhatsAppClient({
        authStrategy: new LocalAuth({ clientId: 'bot_session' }),
        puppeteer: { headless: true }           // use headless Chrome
    });
    activeQRClient = client;

    client.on('qr', async (qr) => {
        try {
            const qrImage = await QRCode.toBuffer(qr, { type: 'png', width: 400 });
            const imgPath = path.join(__dirname, `qr_${Date.now()}.png`);
            fs.writeFileSync(imgPath, qrImage);
            await bot.sendPhoto(chatId, imgPath, { caption: '📷 Scan this QR code with your WhatsApp (Linked Devices → Link a Device).' });
            fs.unlinkSync(imgPath);
        } catch (e) {
            console.error('QR generation failed:', e);
            bot.sendMessage(chatId, '❌ Failed to generate QR code. Check console.');
        }
    });

    client.on('ready', async () => {
        activeBackend = 'qr';
        isConnected = true;
        const me = client.info.me?.user || client.info.wid?.user || '';
        const message = me ? `✅ QR login successful! Connected as +${me}.` : '✅ QR login successful!';
        bot.sendMessage(chatId, message);
        if (activeMaytapi) {
            activeMaytapi = null;
            if (activeStatusPollInterval) clearInterval(activeStatusPollInterval);
        }
    });

    client.on('disconnected', (reason) => {
        console.log('QR client disconnected:', reason);
        if (activeBackend === 'qr') {
            activeBackend = null;
            isConnected = false;
            bot.sendMessage(chatId, '🔌 QR session disconnected.');
        }
        client.destroy().catch(() => {});
        activeQRClient = null;
    });

    client.initialize()
        .then(() => {
            console.log('WhatsApp client initialized.');
        })
        .catch(err => {
            console.error('QR init error:', err);
            bot.sendMessage(chatId, '❌ Failed to start QR login. Check console for details.');
            activeQRClient = null;
        });
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
                    [{ text: '🌐 Maytapi', callback_data: 'connect_maytapi' },
                     { text: '📱 QR Code Login', callback_data: 'connect_qr' }]
                ]
            }
        });
        return;
    }

    if (text === '🔌 Disconnect') {
        await disconnectActive(chatId);
        return;
    }

    if (text === '📂 Check WhatsApp') {
        if (!isConnected) {
            bot.sendMessage(chatId, '⚠️ No active WhatsApp connection.');
        } else {
            bot.sendMessage(chatId, '📄 Send a .txt file with one number per line.');
        }
        return;
    }

    if (expectingMaytapiUrl) {
        expectingMaytapiUrl = false;
        const parsed = parseMaytapiUrl(text.trim());
        if (!parsed) {
            bot.sendMessage(chatId, '❌ Invalid Maytapi URL.');
            return;
        }
        apiPool.push({ ...parsed, chatId });
        const ok = await trySetActiveMaytapi();
        if (ok) {
            activeBackend = 'maytapi';
            isConnected = true;
            const st = await maytapiGetStatus(activeMaytapi);
            const num = st.status?.number || st.number || '';
            bot.sendMessage(chatId, `✅ Maytapi connected (+${num}).`);
        } else {
            bot.sendMessage(chatId, '📌 Maytapi added to pool. Waiting for connection…');
        }
        return;
    }
});

bot.on('callback_query', async query => {
    const chatId = query.message.chat.id;
    const data = query.data;

    if (data === 'connect_maytapi') {
        bot.answerCallbackQuery(query.id);
        expectingMaytapiUrl = true;
        bot.sendMessage(chatId, '🔗 Send your Maytapi screen URL (e.g. https://api.maytapi.com/api/.../screen?token=...)');
        return;
    }

    if (data === 'connect_qr') {
        bot.answerCallbackQuery(query.id);
        bot.sendMessage(chatId, '⏳ Starting QR login…');
        startQRLogin(chatId);
        return;
    }
});

// ── File check (lightning fast) ──
async function checkWithMaytapi(numbers) {
    const registered = [], fresh = [];
    for (const raw of numbers) {
        const clean = cleanNumber(raw);
        if (!clean) continue;
        const ok = await maytapiCheckNumber(activeMaytapi, clean);
        if (ok) registered.push(clean);
        else fresh.push(clean);
        checkingDone++;
        await updateProgress();
    }
    return { registered, fresh };
}

async function checkWithQR(numbers) {
    const registered = [], fresh = [];
    for (const raw of numbers) {
        const clean = cleanNumber(raw);
        if (!clean) continue;
        try {
            const jid = await activeQRClient.getNumberId(`${clean}@c.us`);
            if (jid) registered.push(clean);
            else fresh.push(clean);
        } catch {
            fresh.push(clean);
        }
        checkingDone++;
        await updateProgress();
    }
    return { registered, fresh };
}

async function updateProgress() {
    if (!checkingMessageId) return;
    try {
        await bot.editMessageText(`Number checking ${checkingDone}/${checkingTotal}`,
            { chat_id: checkingChatId, message_id: checkingMessageId });
    } catch {}
}

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
        else { bot.sendMessage(chatId, 'Unknown backend'); return; }

        await bot.deleteMessage(chatId, checkingMessageId).catch(() => {});

        let report = '';
        if (results.registered.length) {
            report += '*Already Created Account Number ✅:*\n' + results.registered.join('\n') + '\n\n';
        }
        if (results.fresh.length) {
            report += '*Fresh Number ❌*\n' + results.fresh.map(n => `+${n}`).join('\n');
        }
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

console.log('Bot running – KBS styled keyboard + QR/Maytapi');
