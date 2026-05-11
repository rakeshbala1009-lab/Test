import asyncio
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta

import requests
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram import CopyTextButton
from telegram.constants import KeyboardButtonStyle as KBS
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# ==================== CONFIGURATION ====================

BOT_TOKEN = "8255460482:AAGT6i_wo4q9cTj9Cj_QeRmePZ1WY-n30YA"
ADMIN_IDS = [8478266638]

# ==================== LOAD COUNTRIES FROM JSON FILE ====================

def load_countries_db():
    try:
        with open('countries.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("⚠️ countries.json not found! Creating default...")
        default = {
            "Pakistan": {"code": "+92", "iso": "PK", "flag": "🇵🇰"},
            "India": {"code": "+91", "iso": "IN", "flag": "🇮🇳"},
            "Venezuela": {"code": "+58", "iso": "VE", "flag": "🇻🇪"},
            "Nigeria": {"code": "+234", "iso": "NG", "flag": "🇳🇬"},
        }
        with open('countries.json', 'w', encoding='utf-8') as f:
            json.dump(default, f, indent=2, ensure_ascii=False)
        return default

def save_countries_db(data):
    with open('countries.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

COUNTRIES_DATA = load_countries_db()

def get_country_info(country_name):
    return COUNTRIES_DATA.get(country_name, {"flag": "🏁", "code": ""})

# ==================== JOIN CHECK CONFIGURATION (DISABLED) ====================

# Required channels are no longer enforced, kept only for reference
REQUIRED_CHANNELS = ["@RgxNumberChanel", "@RgxOtp"]

GROUP_LINKS = {
    "number_channel": "https://t.me/RgxNumberChanel",
    "otp_group": "https://t.me/RgxOtp",
    "tech_channel": "https://t.me/+T7gjlfiPeKdmNzY9"
}

# ==================== API CONFIGURATION ====================

API_URL = "put your panel api url"
API_TOKEN = "Put your panel api token"

# ==================== DATABASE SETUP ====================

conn = sqlite3.connect('mrisbrand_master.db', check_same_thread=False)
db_lock = threading.Lock()
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS users
             (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
              joined_date TEXT, last_active TEXT,
              current_number_id INTEGER DEFAULT NULL,
              current_number TEXT DEFAULT NULL, current_country TEXT DEFAULT NULL,
              current_service TEXT DEFAULT NULL, number_expiry TEXT DEFAULT NULL,
              last_menu_message_id INTEGER DEFAULT NULL,
              joined_check INTEGER DEFAULT 0)''')

c.execute('''CREATE TABLE IF NOT EXISTS numbers
             (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, number TEXT,
              country TEXT, service TEXT, assigned_date TEXT, status TEXT DEFAULT 'active',
              expiry_time TEXT)''')

c.execute('''CREATE TABLE IF NOT EXISTS otps
             (id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT, otp TEXT,
              message TEXT, timestamp TEXT, forwarded INTEGER DEFAULT 0, user_id INTEGER)''')

c.execute('''CREATE TABLE IF NOT EXISTS countries
             (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, service TEXT,
              flag TEXT, active INTEGER DEFAULT 1, stock INTEGER DEFAULT 0)''')

c.execute('''CREATE TABLE IF NOT EXISTS available_numbers
             (id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT, service TEXT,
              number TEXT, used INTEGER DEFAULT 0)''')

c.execute('''CREATE TABLE IF NOT EXISTS used_numbers
             (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, number TEXT,
              country TEXT, service TEXT, assigned_date TEXT)''')

c.execute('''CREATE TABLE IF NOT EXISTS services
             (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
              display_name TEXT, active INTEGER DEFAULT 1)''')

default_services = ["WhatsApp", "Telegram", "Facebook", "IMO", "Google", "Tinder", "Uber", "Instagram", "Twitter", "Snapchat"]
for service in default_services:
    c.execute("INSERT OR IGNORE INTO services (name, display_name, active) VALUES (?, ?, 1)", (service, service))

conn.commit()
print("✅ Database setup completed")

# ==================== STATE TRACKING ====================

admin_mode = {}
admin_panel_state = {}
admin_temp_data = {}

# ==================== HELPER ====================

def safe_url(url: str) -> str | None:
    if url and isinstance(url, str) and (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
        return url
    return None

# ==================== KEYBOARD BUILDERS ====================

BTN_GET_NUMBER = "📱 Get Number"
BTN_LIVE_STOCK = "📊 Live Stock"
BTN_SUPPORT = "🎧 Support"
BTN_ADMIN = "👑 Admin Panel"

def bottom_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [
            KeyboardButton(BTN_GET_NUMBER, style=KBS.PRIMARY),
            KeyboardButton(BTN_LIVE_STOCK, style=KBS.PRIMARY),
        ],
        [
            KeyboardButton(BTN_SUPPORT, style=KBS.SUCCESS),
        ],
    ]
    if user_id in ADMIN_IDS:
        rows.append([KeyboardButton(BTN_ADMIN, style=KBS.DANGER)])
    return ReplyKeyboardMarkup(
        rows, resize_keyboard=True, is_persistent=True,
        input_field_placeholder="Choose an option...")

def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📱 Get Number", callback_data="menu_get_number", style=KBS.PRIMARY),
            InlineKeyboardButton("📊 Live Stock", callback_data="menu_live_stock", style=KBS.PRIMARY),
        ],
        [
            InlineKeyboardButton("🎧 Support", callback_data="menu_support", style=KBS.SUCCESS),
        ],
    ]
    if user_id in ADMIN_IDS:
        rows.append([
            InlineKeyboardButton("👑 Admin Panel", callback_data="menu_admin", style=KBS.DANGER),
        ])
    return InlineKeyboardMarkup(rows)

def back_to_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_menu", style=KBS.PRIMARY)],
    ])

# Keep join keyboard for reference but no longer used
def join_required_keyboard() -> InlineKeyboardMarkup:
    rows = []
    tech = safe_url(GROUP_LINKS.get("tech_channel"))
    otp = safe_url(GROUP_LINKS.get("otp_group"))
    if tech:
        rows.append([InlineKeyboardButton("📢 Join Tech Channel", url=tech, style=KBS.PRIMARY)])
    if otp:
        rows.append([InlineKeyboardButton("👥 Join OTP Group", url=otp, style=KBS.PRIMARY)])
    rows.append([InlineKeyboardButton("✅ I've Joined All — Verify", callback_data="check_joined", style=KBS.SUCCESS)])
    return InlineKeyboardMarkup(rows)

def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Statistics", callback_data="admin_stats", style=KBS.PRIMARY),
            InlineKeyboardButton("📤 Upload Stock", callback_data="admin_upload", style=KBS.SUCCESS),
        ],
        [
            InlineKeyboardButton("🗑 Delete Stock", callback_data="admin_delete", style=KBS.DANGER),
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast", style=KBS.PRIMARY),
        ],
        [
            InlineKeyboardButton("👥 Give Account", callback_data="admin_giveaway", style=KBS.SUCCESS),
            InlineKeyboardButton("🌍 Country Manager", callback_data="admin_country_manager", style=KBS.PRIMARY),
        ],
        [
            InlineKeyboardButton("🔧 Service Manager", callback_data="admin_service_manager", style=KBS.PRIMARY),
            InlineKeyboardButton("❌ Exit Admin", callback_data="admin_exit", style=KBS.DANGER),
        ],
        [
            InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_menu", style=KBS.PRIMARY),
        ],
    ])

def admin_back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back", style=KBS.PRIMARY),
    ]])

def admin_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel", callback_data="admin_back", style=KBS.DANGER),
    ]])

def number_action_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🔄 NEW NUMBER", callback_data="next_number", style=KBS.SUCCESS),
            InlineKeyboardButton("🌍 Change Country", callback_data="back_to_countries", style=KBS.SUCCESS),
        ],
    ]
    otp = safe_url(GROUP_LINKS.get("otp_group"))
    if otp:
        rows.append([InlineKeyboardButton("👥 Join OTP Group", url=otp, style=KBS.PRIMARY)])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_menu", style=KBS.DANGER)])
    return InlineKeyboardMarkup(rows)

def countries_keyboard(countries: list) -> InlineKeyboardMarkup:
    rows = []
    for country in countries:
        country_name = country[0]
        service = country[1]
        stock = country[2]
        country_info = get_country_info(country_name)
        flag = country_info.get("flag", "🏁")
        btn_text = f"{flag} {country_name} — {service} ({stock})"
        cb_data = f"sel|{country_name}|{service}"
        if len(cb_data.encode('utf-8')) > 64:
            cb_data = cb_data[:60]
        rows.append([InlineKeyboardButton(btn_text, callback_data=cb_data, style=KBS.SUCCESS)])
    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="back_to_menu", style=KBS.DANGER)])
    return InlineKeyboardMarkup(rows)

def stock_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh Stock", callback_data="refresh_stock", style=KBS.SUCCESS)],
        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="back_to_menu", style=KBS.DANGER)],
    ])

def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Contact Admin Support", url="t.me/BloodyV0id", style=KBS.SUCCESS)],
        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="back_to_menu", style=KBS.DANGER)],
    ])

# ==================== DATABASE HELPER FUNCTIONS ====================

def db_exec(query, params=()):
    with db_lock:
        c.execute(query, params)
        conn.commit()

def db_fetch_one(query, params=()):
    with db_lock:
        c.execute(query, params)
        return c.fetchone()

def db_fetch_all(query, params=()):
    with db_lock:
        c.execute(query, params)
        return c.fetchall()

def extract_country_from_filename(filename):
    try:
        name = filename.replace('.txt', '')
        for country_name in COUNTRIES_DATA.keys():
            if country_name.lower() in name.lower():
                return country_name
        return None
    except Exception:
        return None

def extract_service_from_filename(filename):
    try:
        name = filename.replace('.txt', '').lower()
        services = [row[0] for row in db_fetch_all("SELECT name FROM services WHERE active = 1")]
        for service in services:
            if service.lower() in name:
                return service
        return "Unknown"
    except Exception:
        return "Unknown"

def load_numbers_from_file(file_path, filename):
    try:
        country = extract_country_from_filename(filename)
        service = extract_service_from_filename(filename)

        if not country:
            return 0, "❌ Country not found in database! Please add to countries.json first."

        country_info = get_country_info(country)
        flag = country_info.get("flag", "🏁")

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
            numbers = file.read().strip().split('\n')

        valid_numbers = []
        for num in numbers:
            num = num.strip()
            if num:
                if not num.startswith('+'):
                    num = '+' + num
                valid_numbers.append(num)

        if not valid_numbers:
            return 0, "❌ No valid numbers found in file!"

        with db_lock:
            for number in valid_numbers:
                c.execute('''INSERT INTO available_numbers (country, service, number)
                             VALUES (?, ?, ?)''', (country, service, number))

            c.execute('''INSERT OR IGNORE INTO countries (name, service, flag, stock)
                         VALUES (?, ?, ?, 0)''', (country, service, flag))

            c.execute("SELECT stock FROM countries WHERE name = ? AND service = ?", (country, service))
            current = c.fetchone()
            current_stock = current[0] if current else 0

            c.execute('''UPDATE countries SET stock = ?, active = 1
                         WHERE name = ? AND service = ?''',
                      (current_stock + len(valid_numbers), country, service))
            conn.commit()

        return len(valid_numbers), f"✅ Added {len(valid_numbers)} {flag} {country} {service} numbers!"

    except Exception as e:
        print(f"Error loading file: {e}")
        return 0, f"❌ Error: {str(e)}"

def delete_country_stock(country, service):
    try:
        db_exec("DELETE FROM available_numbers WHERE country = ? AND service = ?", (country, service))
        db_exec("DELETE FROM countries WHERE name = ? AND service = ?", (country, service))
        return True
    except Exception as e:
        print(f"Error deleting stock: {e}")
        return False

def get_random_number_from_stock(country, service):
    try:
        with db_lock:
            c.execute('''SELECT COUNT(*) FROM available_numbers
                         WHERE country = ? AND service = ? AND used = 0''', (country, service))
            count = c.fetchone()[0]

            if count == 0:
                return None, None

            c.execute('''SELECT id, number FROM available_numbers
                         WHERE country = ? AND service = ? AND used = 0
                         ORDER BY RANDOM() LIMIT 1''', (country, service))
            result = c.fetchone()

            if result:
                number_id, number = result
                c.execute("UPDATE available_numbers SET used = 1 WHERE id = ?", (number_id,))
                c.execute(
                    "UPDATE countries SET stock = MAX(0, stock - 1) WHERE name = ? AND service = ?",
                    (country, service))
                conn.commit()
                return number_id, number
        return None, None
    except Exception as e:
        print(f"Error getting number: {e}")
        return None, None

def extract_otp_from_message(message_text):
    try:
        patterns = [r'(\d{3}-\d{3})', r'(\d{6})', r'(\d{4,8})']
        for pattern in patterns:
            match = re.search(pattern, message_text)
            if match:
                return match.group(1)
        return None
    except Exception:
        return None

def get_user_current_number(user_id):
    result = db_fetch_one(
        '''SELECT current_number, current_country, current_service, number_expiry
           FROM users WHERE user_id = ?''', (user_id,))
    if result and result[0]:
        number, country, service, expiry = result
        if expiry:
            try:
                if datetime.now() < datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S"):
                    return number, country, service, expiry
            except ValueError:
                pass
    return None, None, None, None

def format_number_message(country, service, number, first_name=None):
    if first_name is None:
        first_name = "User"
    country_info = get_country_info(country)
    flag = country_info.get("flag", "🏁")
    current_time = datetime.now().strftime('%I:%M %p')
    if not number.startswith('+'):
        number = '+' + number

    message = f"""
🔥 *{first_name}* Your *{service}* Number! 🌼

━━━━━━━━━━━━━━━━━━━━
🕐 *{current_time}*
"""
    number_btn = InlineKeyboardButton(
        f"⚡ {number}",
        copy_text=CopyTextButton(text=number),
        style=KBS.PRIMARY 
    )
    new_num_btn = InlineKeyboardButton("🔄 NEW NUMBER", callback_data="next_number", style=KBS.SUCCESS)
    change_country_btn = InlineKeyboardButton("🌍 Change Country", callback_data="back_to_countries", style=KBS.SUCCESS)
    
    rows = [[number_btn]]
    rows.append([new_num_btn, change_country_btn])
    
    otp_url = safe_url(GROUP_LINKS.get("otp_group"))
    if otp_url:
        rows.append([InlineKeyboardButton("👥 Join OTP Group", url=otp_url, style=KBS.PRIMARY)])
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_menu", style=KBS.DANGER)])
    
    keyboard = InlineKeyboardMarkup(rows)
    return message, keyboard

# ==================== JOIN CHECK FUNCTIONS (NO LONGER ENFORCED) ====================

# These functions are kept for potential future use but are no longer called.
async def check_joined_channels(bot, user_id):
    try:
        for channel in REQUIRED_CHANNELS:
            member = await bot.get_chat_member(channel, user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        return True
    except Exception as e:
        print(f"Join check error for user {user_id}: {e}")
        return False

async def verify_user_access(bot, user_id):
    # Always return True – no join enforcement
    return True

async def send_join_required(bot, user_id):
    # No longer used, kept for completeness
    pass

# ==================== OTP & CLEANUP JOBS ====================

async def monitor_otp_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        if "put your panel api url" in API_URL:
            return

        url = f"{API_URL}?token={API_TOKEN}"
        response = requests.get(url, timeout=15)

        if response.status_code != 200:
            return

        try:
            data = response.json()
        except Exception:
            return

        active_numbers = {
            row[0].replace('+', ''): row[1]
            for row in db_fetch_all(
                '''SELECT number, user_id FROM numbers
                   WHERE status = 'active' AND expiry_time > ?''',
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)
            )
        }

        if isinstance(data, list):
            for item in data:
                if isinstance(item, list) and len(item) >= 3:
                    service = item[0] if len(item) > 0 else "Unknown"
                    number = item[1] if len(item) > 1 else ""
                    message = item[2] if len(item) > 2 else ""
                    timestamp = item[3] if len(item) > 3 else datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    number_clean = number.replace('+', '')

                    if number_clean in active_numbers:
                        user_id = active_numbers[number_clean]
                        otp = extract_otp_from_message(message)

                        if otp:
                            existing = db_fetch_one(
                                'SELECT id FROM otps WHERE number = ? AND otp = ?', (number, otp))
                            if not existing:
                                db_exec(
                                    '''INSERT INTO otps (number, otp, message, timestamp, user_id)
                                       VALUES (?, ?, ?, ?, ?)''',
                                    (number, otp, message[:200], timestamp, user_id))

                                otp_msg = (
                                    "╔═══════════════════╗\n"
                                    "    📩 *OTP RECEIVED*  \n"
                                    "╚═══════════════════╝\n\n"
                                    f"📱 *Number:* `{number}`\n"
                                    f"🔑 *OTP CODE:* `{otp}`\n"
                                    f"💬 *Service:* {service}\n"
                                    f"⏱ *Time:* {timestamp}\n"
                                    "━━━━━━━━━━━━━━━━━"
                                )
                                try:
                                    await context.bot.send_message(
                                        user_id, otp_msg, parse_mode='Markdown')
                                except Exception:
                                    pass
    except Exception as e:
        print(f"OTP Monitor Error: {e}")

async def cleanup_expired_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db_exec(
            "UPDATE numbers SET status = 'expired' WHERE expiry_time < ? AND status = 'active'",
            (now,))
        db_exec(
            '''UPDATE users SET
               current_number = NULL, current_country = NULL,
               current_service = NULL, number_expiry = NULL
               WHERE number_expiry < ?''', (now,))
    except Exception as e:
        print(f"Cleanup Error: {e}")

# ==================== NOTIFY USERS HELPER ====================

async def notify_users_about_new_numbers(bot, country, service, flag, count):
    users = db_fetch_all("SELECT user_id FROM users WHERE joined_check = 1")
    notification = (
        f"✨ *{flag} {country} — {service}* ✨\n\n"
        f"🔥 *{count} Fresh Numbers Added!*\n"
        "⚡ Speed up — grab yours now!"
    )
    for user in users:
        try:
            await bot.send_message(user[0], notification, parse_mode='Markdown')
            await asyncio.sleep(0.05)
        except Exception:
            continue

# ==================== TEXT BUILDERS ====================

def welcome_text(user_id, first_name):
    return (
        f"✨ *Welcome to Developer ✆  RGX NUMBER BOT  Bot, {first_name}!* ✨\n\n"
        "🚀 _Your Premium Platform for Virtual Numbers._\n\n"
        f"🆔 *Your ID:* `{user_id}`\n"
        "✅ *You are a Verified Member!*\n\n"
        "🎮 _Tap a button below to navigate._\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👨‍💻 *Developer:* ✆  RGX NUMBER BOT "
    )

def stock_text():
    data = db_fetch_all(
        "SELECT name, service, stock FROM countries WHERE active = 1 ORDER BY name")
    if not data:
        return "📊 *CURRENT LIVE STOCK*\n━━━━━━━━━━━━━━━━━━━━\n\n❌ No stock available."
    text = "📊 *CURRENT LIVE STOCK*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for item in data:
        country_info = get_country_info(item[0])
        flag = country_info.get("flag", "🏁")
        stock_bar = "🟢" if item[2] > 0 else "🔴"
        text += f"{stock_bar} {flag} *{item[0]}* — {item[1]}: `{item[2]}`\n"
    text += f"\n🕒 *Updated:* `{datetime.now().strftime('%H:%M:%S | %d %B %Y')}`"
    return text

# ==================== /start COMMAND ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name or "User"

    print(f"📩 /start from user {user_id} ({first_name})")

    db_exec(
        '''INSERT OR IGNORE INTO users
           (user_id, username, first_name, joined_date, last_active)
           VALUES (?, ?, ?, ?, ?)''',
        (user_id, username, first_name,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    db_exec("UPDATE users SET last_active = ? WHERE user_id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id))

    # No join check – directly go to main menu
    await update.message.reply_text(
        welcome_text(user_id, first_name),
        reply_markup=bottom_menu_keyboard(user_id),
        parse_mode='Markdown',
    )

# ==================== MAIN MENU CALLBACK HANDLERS ====================

async def show_main_menu(query, user_id, first_name):
    try:
        await query.edit_message_text(
            welcome_text(user_id, first_name),
            reply_markup=main_menu_keyboard(user_id),
            parse_mode='Markdown')
    except Exception:
        try:
            await query.message.reply_text(
                welcome_text(user_id, first_name),
                reply_markup=main_menu_keyboard(user_id),
                parse_mode='Markdown')
        except Exception:
            pass

async def show_get_number(query, context, user_id, first_name):
    db_exec("UPDATE users SET last_active = ? WHERE user_id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id))

    countries = db_fetch_all(
        "SELECT name, service, stock FROM countries WHERE active = 1 AND stock > 0 ORDER BY name")

    if not countries:
        try:
            await query.edit_message_text(
                "❌ *No numbers available at the moment.*\n\n"
                "Please check back later or contact admin.",
                reply_markup=back_to_main_keyboard(),
                parse_mode='Markdown')
        except Exception:
            pass
        return

    try:
        await query.edit_message_text(
            "📲 *Select a Country & Service:*\n\n"
            "Choose from the list below to get your virtual number.",
            reply_markup=countries_keyboard(countries),
            parse_mode='Markdown')
    except Exception:
        pass

async def show_live_stock(query):
    try:
        await query.edit_message_text(
            stock_text(), reply_markup=stock_keyboard(), parse_mode='Markdown')
    except Exception:
        pass

async def show_support(query):
    try:
        await query.edit_message_text(
            "🎧 *CONTACT SUPPORT*\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "For any issues, questions, or requests — contact admin directly.\n\n"
            "👨‍💻 *Developer:* ✆  RGX NUMBER BOT ",
            reply_markup=support_keyboard(),
            parse_mode='Markdown')
    except Exception:
        pass

async def show_admin_panel_menu(query, user_id):
    if user_id not in ADMIN_IDS:
        await query.answer("❌ Unauthorized!", show_alert=True)
        return
    admin_mode[user_id] = True
    admin_panel_state[user_id] = "main"
    try:
        await query.edit_message_text(
            "🛡 *ADMIN PANEL*\n\n👨‍💻 *Developer:* ✆  RGX NUMBER BOT \n\nSelect an action below:",
            reply_markup=admin_panel_keyboard(),
            parse_mode='Markdown')
    except Exception:
        pass

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    first_name = query.from_user.first_name or "User"
    data = query.data

    # No join verification required
    await query.answer()
    action = data[len("menu_"):]

    if action == "get_number":
        await show_get_number(query, context, user_id, first_name)
    elif action == "live_stock":
        await show_live_stock(query)
    elif action == "support":
        await show_support(query)
    elif action == "admin":
        await show_admin_panel_menu(query, user_id)

async def back_to_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    first_name = query.from_user.first_name or "User"
    await query.answer()
    await show_main_menu(query, user_id, first_name)

# ==================== CALLBACK QUERY HANDLERS — NUMBER FLOW ====================

async def check_joined_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    # Since join is no longer enforced, just show a message and prompt to main
    await query.answer("✅ Access is unrestricted!", show_alert=True)

async def refresh_stock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    # No join check
    await query.answer("🔄 Refreshed!")
    try:
        await query.edit_message_text(
            stock_text(), reply_markup=stock_keyboard(), parse_mode='Markdown')
    except Exception:
        pass

async def select_country_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    first_name = query.from_user.first_name or "User"

    # No join check

    await query.answer("⏳ Allocating your number...")

    try:
        parts = query.data.split('|', 2)
        if len(parts) < 3:
            await query.answer("❌ Invalid selection. Please try again.", show_alert=True)
            return
        country = parts[1]
        service = parts[2]
    except Exception:
        await query.answer("❌ Error parsing selection. Please try again.", show_alert=True)
        return

    stock_result = db_fetch_one(
        "SELECT stock FROM countries WHERE name = ? AND service = ? AND active = 1",
        (country, service))

    if not stock_result or stock_result[0] <= 0:
        await query.answer("❌ No numbers left for this service! Try another.", show_alert=True)
        countries = db_fetch_all(
            "SELECT name, service, stock FROM countries WHERE active = 1 AND stock > 0 ORDER BY name")
        if countries:
            try:
                await query.edit_message_text(
                    "📲 *Select a Country & Service:*\n\nChoose from the list below.",
                    reply_markup=countries_keyboard(countries),
                    parse_mode='Markdown')
            except Exception:
                pass
        else:
            try:
                await query.edit_message_text(
                    "❌ No numbers available at the moment.",
                    reply_markup=back_to_main_keyboard())
            except Exception:
                pass
        return

    num_id, number = get_random_number_from_stock(country, service)
    if not number:
        await query.answer("❌ Failed to allocate number! Please try again.", show_alert=True)
        return

    expiry = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db_exec(
        '''INSERT INTO numbers (user_id, number, country, service, assigned_date, expiry_time)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (user_id, number, country, service, now_str, expiry))

    db_exec(
        '''UPDATE users SET
           current_number = ?, current_country = ?, current_service = ?, number_expiry = ?
           WHERE user_id = ?''',
        (number, country, service, expiry, user_id))

    msg, kb = format_number_message(country, service, number, first_name)
    try:
        await query.edit_message_text(msg, reply_markup=kb, parse_mode='Markdown')
    except Exception as e:
        print(f"Error editing message: {e}")
        await context.bot.send_message(user_id, msg, reply_markup=kb, parse_mode='Markdown')

async def next_number_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    first_name = query.from_user.first_name or "User"

    # No join check

    await query.answer("⏳ Getting next number...")

    # First check active session
    result = db_fetch_one(
        "SELECT current_country, current_service FROM users WHERE user_id = ?", (user_id,))

    # If no active session, fallback to most recent number in history
    if not result or not result[0]:
        fallback = db_fetch_one(
            "SELECT country, service FROM numbers WHERE user_id = ? ORDER BY assigned_date DESC LIMIT 1",
            (user_id,)
        )
        if fallback:
            country, service = fallback
        else:
            # No history at all → show country selection
            countries = db_fetch_all(
                "SELECT name, service, stock FROM countries WHERE active = 1 AND stock > 0 ORDER BY name")
            if countries:
                try:
                    await query.edit_message_text(
                        "📲 *Select a Country & Service:*",
                        reply_markup=countries_keyboard(countries),
                        parse_mode='Markdown')
                except Exception:
                    pass
            else:
                try:
                    await query.edit_message_text(
                        "❌ No numbers available at the moment.",
                        reply_markup=back_to_main_keyboard())
                except Exception:
                    pass
            return
    else:
        country, service = result

    # Verify stock exists for that country/service
    stock_result = db_fetch_one(
        "SELECT stock FROM countries WHERE name = ? AND service = ? AND active = 1",
        (country, service))

    if not stock_result or stock_result[0] <= 0:
        await query.answer(f"❌ No more {country} {service} numbers!", show_alert=True)
        countries = db_fetch_all(
            "SELECT name, service, stock FROM countries WHERE active = 1 AND stock > 0 ORDER BY name")
        if countries:
            try:
                await query.edit_message_text(
                    "📲 *Select a Country & Service:*",
                    reply_markup=countries_keyboard(countries),
                    parse_mode='Markdown')
            except Exception:
                pass
        else:
            try:
                await query.edit_message_text(
                    "❌ No numbers available at the moment.",
                    reply_markup=back_to_main_keyboard())
            except Exception:
                pass
        return

    num_id, number = get_random_number_from_stock(country, service)
    if not number:
        await query.answer("❌ Failed to get number!", show_alert=True)
        return

    expiry = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db_exec(
        '''INSERT INTO numbers (user_id, number, country, service, assigned_date, expiry_time)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (user_id, number, country, service, now_str, expiry))
    db_exec(
        '''UPDATE users SET current_number = ?, current_country = ?,
           current_service = ?, number_expiry = ? WHERE user_id = ?''',
        (number, country, service, expiry, user_id))

    msg, kb = format_number_message(country, service, number, first_name)
    try:
        await query.edit_message_text(msg, reply_markup=kb, parse_mode='Markdown')
    except Exception as e:
        print(f"Error editing message: {e}")
        await context.bot.send_message(user_id, msg, reply_markup=kb, parse_mode='Markdown')

async def back_to_countries_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    # No join check

    await query.answer()

    db_exec(
        '''UPDATE users SET current_number = NULL, current_country = NULL,
           current_service = NULL, number_expiry = NULL WHERE user_id = ?''',
        (user_id,))

    countries = db_fetch_all(
        "SELECT name, service, stock FROM countries WHERE active = 1 AND stock > 0 ORDER BY name")
    if countries:
        try:
            await query.edit_message_text(
                "📲 *Select a Country & Service:*\n\nChoose from the list below.",
                reply_markup=countries_keyboard(countries),
                parse_mode='Markdown')
        except Exception:
            pass
    else:
        try:
            await query.edit_message_text(
                "❌ No numbers available at the moment.",
                reply_markup=back_to_main_keyboard())
        except Exception:
            pass

# ==================== ADMIN SECTION (UNTCHANGED) ====================

async def enter_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS:
        admin_mode[user_id] = True
        admin_panel_state[user_id] = "main"
        await update.message.reply_text(
            "🛡 *ADMIN PANEL*\n\n👨‍💻 *Developer:* ✆  RGX NUMBER BOT \n\nSelect an action below:",
            reply_markup=admin_panel_keyboard(),
            parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Unauthorized access!")

async def exit_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in admin_mode:
        admin_mode.pop(user_id, None)
        admin_panel_state.pop(user_id, None)
        first_name = update.effective_user.first_name or "User"
        await update.message.reply_text(
            "✅ Admin mode deactivated!",
            reply_markup=main_menu_keyboard(user_id))
    else:
        await update.message.reply_text("❌ You're not in admin mode!")

async def show_admin_stats(query, user_id):
    total_users = db_fetch_one("SELECT COUNT(*) FROM users")[0]
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    active_users = db_fetch_one(
        "SELECT COUNT(*) FROM users WHERE last_active > ?", (yesterday,))[0]
    active_numbers = db_fetch_one(
        "SELECT COUNT(*) FROM numbers WHERE status = 'active'")[0]
    total_stock = db_fetch_one("SELECT SUM(stock) FROM countries")[0] or 0
    available_numbers = db_fetch_one(
        "SELECT COUNT(*) FROM available_numbers WHERE used = 0")[0]
    active_countries = db_fetch_one(
        "SELECT COUNT(*) FROM countries WHERE active = 1")[0]

    text = (
        "╔══════════════════════╗\n"
        "    📊 *BOT STATISTICS*  \n"
        "╚══════════════════════╝\n"
        "👨‍💻 *Developer:* ✆  RGX NUMBER BOT \n\n"
        "*👥 USERS*\n"
        f"• Total Users: `{total_users}`\n"
        f"• Active (24h): `{active_users}`\n"
        f"• Inactive: `{total_users - active_users}`\n\n"
        "*📱 NUMBERS*\n"
        f"• Active: `{active_numbers}`\n"
        f"• Total Stock: `{total_stock}`\n"
        f"• Available: `{available_numbers}`\n\n"
        "*🌍 COUNTRIES*\n"
        f"• Active Services: `{active_countries}`\n\n"
        f"🕐 `{datetime.now().strftime('%I:%M %p | %d %b %Y')}`"
    )

    countries = db_fetch_all(
        "SELECT name, service, stock FROM countries WHERE active = 1 ORDER BY name")
    if countries:
        text += "\n\n*📦 STOCK DETAILS:*\n"
        for country in countries:
            country_info = get_country_info(country[0])
            flag = country_info.get("flag", "🏁")
            stock_bar = "🟢" if country[2] > 0 else "🔴"
            text += f"{stock_bar} {flag} {country[0]} — {country[1]}: `{country[2]}`\n"

    try:
        await query.edit_message_text(text, reply_markup=admin_back_button(), parse_mode='Markdown')
    except Exception:
        pass

async def show_delete_options(query, user_id):
    countries = db_fetch_all(
        "SELECT name, service, stock FROM countries WHERE active = 1 ORDER BY name")

    if not countries:
        try:
            await query.edit_message_text(
                "❌ No countries to delete!", reply_markup=admin_back_button())
        except Exception:
            pass
        return

    rows = []
    for country in countries:
        country_info = get_country_info(country[0])
        flag = country_info.get("flag", "🏁")
        btn_text = f"🗑 {flag} {country[0]} — {country[1]} (Stock: {country[2]})"
        cb_data = f"admin_del|{country[0]}|{country[1]}"
        rows.append([InlineKeyboardButton(btn_text, callback_data=cb_data, style=KBS.DANGER)])
    rows.append([InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back", style=KBS.PRIMARY)])

    try:
        await query.edit_message_text(
            "*🗑 DELETE STOCK*\n\nSelect a country/service to delete all its numbers:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode='Markdown')
    except Exception:
        pass

async def request_upload(query, user_id):
    admin_panel_state[user_id] = "waiting_file"
    text = (
        "*📤 UPLOAD STOCK*\n\n"
        "Send a `.txt` file with phone numbers.\n\n"
        "*File Format Requirements:*\n"
        "• Filename must contain country name (from countries.json)\n"
        "• Filename must contain service name\n"
        "• One number per line\n\n"
        "*Filename Examples:*\n"
        "• `pakistan_whatsapp.txt`\n"
        "• `india_telegram.txt`\n"
        "• `venezuela_imo.txt`\n\n"
        "*Supported Services:* WhatsApp, Telegram, Facebook, IMO\n"
        "*Note:* Numbers without `+` prefix are fixed automatically."
    )
    try:
        await query.edit_message_text(
            text, reply_markup=admin_cancel_keyboard(), parse_mode='Markdown')
    except Exception:
        pass

async def request_broadcast(query, user_id):
    admin_panel_state[user_id] = "waiting_broadcast"
    try:
        await query.edit_message_text(
            "*📢 BROADCAST MESSAGE*\n\n"
            "Send the message you want to broadcast to ALL users.\n"
            "Supports Markdown formatting.",
            reply_markup=admin_cancel_keyboard(),
            parse_mode='Markdown')
    except Exception:
        pass

async def request_giveaway(query, user_id):
    admin_panel_state[user_id] = "waiting_giveaway"
    try:
        await query.edit_message_text(
            "*👥 GIVE FREE ACCOUNT*\n\n"
            "Send: `user_id count`\n"
            "Example: `123456789 5`\n\n"
            "This will give the user the specified number of free accounts.",
            reply_markup=admin_cancel_keyboard(),
            parse_mode='Markdown')
    except Exception:
        pass

async def exit_admin_callback_query(query, user_id, bot):
    admin_mode.pop(user_id, None)
    admin_panel_state.pop(user_id, None)
    first_name = query.from_user.first_name or "User"
    try:
        await query.edit_message_text(
            welcome_text(user_id, first_name),
            reply_markup=main_menu_keyboard(user_id),
            parse_mode='Markdown')
    except Exception:
        await bot.send_message(
            user_id, "🏠 Returned to main menu.",
            reply_markup=main_menu_keyboard(user_id))

# ==================== COUNTRY MANAGER ====================

async def country_manager_menu(query, user_id):
    if user_id not in admin_mode:
        await query.answer("❌ Admin mode required!", show_alert=True)
        return
    admin_panel_state[user_id] = "country_manager"
    rows = [
        [InlineKeyboardButton("➕ Add New Country", callback_data="country_add", style=KBS.SUCCESS)],
        [InlineKeyboardButton("📋 List All Countries", callback_data="country_list", style=KBS.PRIMARY)],
        [InlineKeyboardButton("✏️ Edit Country", callback_data="country_edit_select", style=KBS.PRIMARY)],
        [InlineKeyboardButton("🗑 Delete Country", callback_data="country_delete_select", style=KBS.DANGER)],
        [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back", style=KBS.PRIMARY)],
    ]
    try:
        await query.edit_message_text(
            "🌍 *COUNTRY MANAGER*\n\nSelect an option:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode='Markdown')
    except Exception:
        pass

async def country_add_start(query, user_id):
    admin_panel_state[user_id] = "waiting_country_name"
    admin_temp_data[user_id] = {}
    text = (
        "🌍 *ADD NEW COUNTRY*\n\n"
        "Please enter the country details in this format:\n\n"
        "`CountryName | Code | ISO | Flag`\n\n"
        "*Example:*\n"
        "`USA | +1 | US | 🇺🇸`\n\n"
        "Or send just the country name for auto-detection."
    )
    try:
        await query.edit_message_text(
            text, reply_markup=admin_cancel_keyboard(), parse_mode='Markdown')
    except Exception:
        pass

async def country_list_show(query):
    text = "🌍 *ALL COUNTRIES*\n\n"
    for name, info in COUNTRIES_DATA.items():
        text += f"• {info['flag']} *{name}*\n  Code: {info['code']} | ISO: {info['iso']}\n\n"
    try:
        await query.edit_message_text(
            text, reply_markup=admin_back_button(), parse_mode='Markdown')
    except Exception:
        pass

async def country_edit_select(query):
    rows = []
    for name in COUNTRIES_DATA.keys():
        info = COUNTRIES_DATA[name]
        rows.append([InlineKeyboardButton(
            f"{info['flag']} {name}", callback_data=f"country_edit|{name}", style=KBS.PRIMARY)])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_country_manager", style=KBS.PRIMARY)])
    try:
        await query.edit_message_text(
            "✏️ *Select country to edit:*",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode='Markdown')
    except Exception:
        pass

async def country_edit_start(query, user_id, country_name):
    admin_temp_data[user_id] = {"edit_country": country_name}
    admin_panel_state[user_id] = "waiting_country_edit"
    info = COUNTRIES_DATA[country_name]
    text = (
        f"✏️ *EDIT COUNTRY: {info['flag']} {country_name}*\n\n"
        f"Current details:\n"
        f"• Code: {info['code']}\n"
        f"• ISO: {info['iso']}\n"
        f"• Flag: {info['flag']}\n\n"
        "Send new details in format:\n"
        "`Code | ISO | Flag`\n\n"
        "*Example:*\n"
        "`+1 | US | 🇺🇸`\n\n"
        "Or send `/skip` to keep current values."
    )
    try:
        await query.edit_message_text(
            text, reply_markup=admin_cancel_keyboard(), parse_mode='Markdown')
    except Exception:
        pass

async def country_delete_select(query):
    rows = []
    for name in COUNTRIES_DATA.keys():
        info = COUNTRIES_DATA[name]
        rows.append([InlineKeyboardButton(
            f"🗑 {info['flag']} {name}", callback_data=f"country_delete|{name}", style=KBS.DANGER)])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_country_manager", style=KBS.PRIMARY)])
    try:
        await query.edit_message_text(
            "🗑 *Select country to delete (no confirmation):*",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode='Markdown')
    except Exception:
        pass

async def country_delete_direct(query, user_id, country_name):
    if user_id not in admin_mode:
        await query.answer("❌ Admin mode required!", show_alert=True)
        return
    if country_name in COUNTRIES_DATA:
        del COUNTRIES_DATA[country_name]
        save_countries_db(COUNTRIES_DATA)
        db_exec("DELETE FROM available_numbers WHERE country = ?", (country_name,))
        db_exec("DELETE FROM countries WHERE name = ?", (country_name,))
        await query.answer(f"✅ {country_name} deleted immediately!")
    else:
        await query.answer("❌ Country not found!", show_alert=True)
    await country_delete_select(query)

# ==================== SERVICE MANAGER ====================

async def service_manager_menu(query, user_id):
    if user_id not in admin_mode:
        await query.answer("❌ Admin mode required!", show_alert=True)
        return
    admin_panel_state[user_id] = "service_manager"
    rows = [
        [InlineKeyboardButton("➕ Add New Service", callback_data="service_add", style=KBS.SUCCESS)],
        [InlineKeyboardButton("📋 List All Services", callback_data="service_list", style=KBS.PRIMARY)],
        [InlineKeyboardButton("✅ Toggle Service Active", callback_data="service_toggle", style=KBS.PRIMARY)],
        [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back", style=KBS.PRIMARY)],
    ]
    try:
        await query.edit_message_text(
            "🔧 *SERVICE MANAGER*\n\nSelect an option:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode='Markdown')
    except Exception:
        pass

async def service_add_start(query, user_id):
    admin_panel_state[user_id] = "waiting_service_name"
    text = (
        "🔧 *ADD NEW SERVICE*\n\n"
        "Send the service name (e.g., WhatsApp, Telegram, Uber).\n\n"
        "This service will be auto-detected from uploaded filenames."
    )
    try:
        await query.edit_message_text(
            text, reply_markup=admin_cancel_keyboard(), parse_mode='Markdown')
    except Exception:
        pass

async def service_list_show(query):
    services = db_fetch_all("SELECT name, display_name, active FROM services ORDER BY name")
    text = "🔧 *ALL SERVICES*\n\n"
    for s in services:
        status = "✅" if s[2] else "❌"
        text += f"• {status} *{s[1]}* (keyword: `{s[0]}`)\n"
    try:
        await query.edit_message_text(
            text, reply_markup=admin_back_button(), parse_mode='Markdown')
    except Exception:
        pass

async def service_toggle_select(query):
    services = db_fetch_all("SELECT name, display_name, active FROM services ORDER BY name")
    rows = []
    for s in services:
        status = "✅" if s[2] else "❌"
        rows.append([InlineKeyboardButton(
            f"{status} {s[1]}", callback_data=f"service_toggle|{s[0]}", style=KBS.PRIMARY)])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_service_manager", style=KBS.PRIMARY)])
    try:
        await query.edit_message_text(
            "🔧 *Select service to toggle active status:*",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode='Markdown')
    except Exception:
        pass

async def service_toggle_execute(query, service_name):
    result = db_fetch_one("SELECT active FROM services WHERE name = ?", (service_name,))
    if result:
        new_status = 0 if result[0] else 1
        db_exec("UPDATE services SET active = ? WHERE name = ?", (new_status, service_name))
        status_text = "activated" if new_status else "deactivated"
        await query.answer(f"✅ Service {status_text}!")
    await service_toggle_select(query)

# ==================== ADMIN CALLBACK ROUTER ====================

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if user_id in ADMIN_IDS and user_id not in admin_mode:
        admin_mode[user_id] = True
        admin_panel_state[user_id] = "main"

    if user_id not in admin_mode:
        await query.answer("❌ Admin mode required!", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data.startswith("admin_del|"):
        parts = data.split('|', 2)
        if len(parts) == 3:
            country = parts[1]
            service = parts[2]
            if delete_country_stock(country, service):
                await query.answer(f"✅ {country} — {service} deleted successfully!")
            else:
                await query.answer(f"❌ Error deleting {country} — {service}!", show_alert=True)
            await show_delete_options(query, user_id)
        return

    action = data[len("admin_"):]

    if action == "stats":
        await show_admin_stats(query, user_id)
    elif action == "upload":
        await request_upload(query, user_id)
    elif action == "delete":
        await show_delete_options(query, user_id)
    elif action == "broadcast":
        await request_broadcast(query, user_id)
    elif action == "giveaway":
        await request_giveaway(query, user_id)
    elif action == "country_manager":
        await country_manager_menu(query, user_id)
    elif action == "service_manager":
        await service_manager_menu(query, user_id)
    elif action == "exit":
        await exit_admin_callback_query(query, user_id, context.bot)
    elif action == "back":
        admin_panel_state[user_id] = "main"
        try:
            await query.edit_message_text(
                "🛡 *ADMIN PANEL*\n\n👨‍💻 *Developer:* ✆  RGX NUMBER BOT \n\nSelect an action below:",
                reply_markup=admin_panel_keyboard(),
                parse_mode='Markdown')
        except Exception:
            pass

# ==================== COUNTRY/SERVICE SPECIFIC CALLBACKS ====================

async def country_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if user_id not in admin_mode:
        await query.answer("❌ Admin mode required!", show_alert=True)
        return

    await query.answer()

    if data == "country_add":
        await country_add_start(query, user_id)
    elif data == "country_list":
        await country_list_show(query)
    elif data == "country_edit_select":
        await country_edit_select(query)
    elif data == "country_delete_select":
        await country_delete_select(query)
    elif data.startswith("country_edit|"):
        parts = data.split('|', 1)
        if len(parts) == 2:
            await country_edit_start(query, user_id, parts[1])
    elif data.startswith("country_delete|"):
        parts = data.split('|', 1)
        if len(parts) == 2:
            await country_delete_direct(query, user_id, parts[1])

async def service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if user_id not in admin_mode:
        await query.answer("❌ Admin mode required!", show_alert=True)
        return

    await query.answer()

    if data == "service_add":
        await service_add_start(query, user_id)
    elif data == "service_list":
        await service_list_show(query)
    elif data == "service_toggle":
        await service_toggle_select(query)
    elif data.startswith("service_toggle|"):
        parts = data.split('|', 1)
        if len(parts) == 2:
            await service_toggle_execute(query, parts[1])

# ==================== TEXT HANDLER FOR ADMIN STATES ====================

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = admin_panel_state.get(user_id)

    if user_id not in admin_mode:
        return False

    text = update.message.text.strip()

    if state == "waiting_broadcast":
        users = db_fetch_all("SELECT user_id FROM users")
        sent = 0
        for user in users:
            try:
                await context.bot.send_message(user[0], text, parse_mode='Markdown')
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                continue
        admin_panel_state[user_id] = "main"
        await update.message.reply_text(f"✅ Broadcast sent to *{sent}* users!")
        await update.message.reply_text(
            "🛡 *ADMIN PANEL*", reply_markup=admin_panel_keyboard(), parse_mode='Markdown')
        return True

    elif state == "waiting_giveaway":
        parts = text.split()
        try:
            target = int(parts[0])
            count = int(parts[1]) if len(parts) > 1 else 1
            if not db_fetch_one("SELECT user_id FROM users WHERE user_id = ?", (target,)):
                await update.message.reply_text("❌ User not found!")
                return True
            # Placeholder for free accounts (removed)
            await update.message.reply_text(f"✅ Given *{count}* free account(s) to `{target}` (feature placeholder).")
            admin_panel_state[user_id] = "main"
            await update.message.reply_text(
                "🛡 *ADMIN PANEL*", reply_markup=admin_panel_keyboard(), parse_mode='Markdown')
        except (ValueError, IndexError):
            await update.message.reply_text("❌ Invalid format! Use: `user_id count`")
        return True

    elif state == "waiting_country_name":
        try:
            parts = [p.strip() for p in text.split('|')]
            if len(parts) == 1:
                country_name = parts[0]
                flag_map = {"USA":"🇺🇸","UK":"🇬🇧","Canada":"🇨🇦","Australia":"🇦🇺","Germany":"🇩🇪","France":"🇫🇷","Spain":"🇪🇸","Italy":"🇮🇹","Japan":"🇯🇵","Korea":"🇰🇷","China":"🇨🇳","Brazil":"🇧🇷","Russia":"🇷🇺","Mexico":"🇲🇽","Indonesia":"🇮🇩","Turkey":"🇹🇷","Egypt":"🇪🇬","Saudi Arabia":"🇸🇦","UAE":"🇦🇪","Bangladesh":"🇧🇩"}
                flag = flag_map.get(country_name, "🏁")
                code = "+" + str(hash(country_name) % 900 + 100)
                iso = country_name[:2].upper()
            elif len(parts) >= 4:
                country_name = parts[0]
                code = parts[1]
                iso = parts[2].upper()
                flag = parts[3]
            else:
                await update.message.reply_text("❌ Invalid format! Use: `Country | Code | ISO | Flag`")
                return True
            COUNTRIES_DATA[country_name] = {"code": code, "iso": iso, "flag": flag}
            save_countries_db(COUNTRIES_DATA)
            admin_panel_state[user_id] = "country_manager"
            await update.message.reply_text(f"✅ Country *{flag} {country_name}* added!")
            await country_manager_menu(update, user_id)
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return True

    elif state == "waiting_country_edit":
        if text == "/skip":
            await update.message.reply_text("✅ No changes made.")
            admin_panel_state[user_id] = "country_manager"
            await country_manager_menu(update, user_id)
            return True
        try:
            parts = [p.strip() for p in text.split('|')]
            if len(parts) >= 3:
                code = parts[0]
                iso = parts[1].upper()
                flag = parts[2]
                country_name = admin_temp_data.get(user_id, {}).get("edit_country")
                if country_name and country_name in COUNTRIES_DATA:
                    COUNTRIES_DATA[country_name] = {"code": code, "iso": iso, "flag": flag}
                    save_countries_db(COUNTRIES_DATA)
                    await update.message.reply_text(f"✅ Country *{flag} {country_name}* updated!")
                else:
                    await update.message.reply_text("❌ Session expired or country not found.")
            else:
                await update.message.reply_text("❌ Invalid format! Use: `Code | ISO | Flag`")
                return True
            admin_panel_state[user_id] = "country_manager"
            await country_manager_menu(update, user_id)
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return True

    elif state == "waiting_service_name":
        service_name = text
        try:
            db_exec("INSERT INTO services (name, display_name, active) VALUES (?, ?, 1)", (service_name, service_name))
            await update.message.reply_text(f"✅ Service *{service_name}* added!")
        except sqlite3.IntegrityError:
            await update.message.reply_text(f"❌ Service *{service_name}* already exists!")
        admin_panel_state[user_id] = "service_manager"
        await service_manager_menu(update, user_id)
        return True

    return False

# ==================== DOCUMENT HANDLER ====================

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in admin_mode or admin_panel_state.get(user_id) != "waiting_file":
        return

    try:
        document = update.message.document
        file_name = document.file_name

        if not file_name.endswith('.txt'):
            await update.message.reply_text("❌ Please upload a `.txt` file!")
            return

        file = await context.bot.get_file(document.file_id)
        os.makedirs("uploads", exist_ok=True)
        file_path = f"uploads/{file_name}"
        await file.download_to_drive(file_path)

        count, result_msg = load_numbers_from_file(file_path, file_name)

        if count > 0:
            country = extract_country_from_filename(file_name)
            service = extract_service_from_filename(file_name)
            if country:
                country_info = get_country_info(country)
                flag = country_info.get("flag", "🏁")
                await notify_users_about_new_numbers(
                    context.bot, country, service, flag, count)

        admin_panel_state[user_id] = "main"
        await update.message.reply_text(result_msg, reply_markup=admin_back_button(), parse_mode='Markdown')

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        admin_panel_state[user_id] = "main"

# ==================== BOTTOM MENU TEXT ROUTERS ====================

async def send_get_number_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name or "User"
    db_exec("UPDATE users SET last_active = ? WHERE user_id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id))

    countries = db_fetch_all(
        "SELECT name, service, stock FROM countries WHERE active = 1 AND stock > 0 ORDER BY name")
    if not countries:
        await update.message.reply_text(
            "❌ *No numbers available at the moment.*",
            reply_markup=back_to_main_keyboard(), parse_mode='Markdown')
        return

    await update.message.reply_text(
        "📲 *Select a Country & Service:*\n\nChoose from the list below to get your virtual number.",
        reply_markup=countries_keyboard(countries), parse_mode='Markdown')

async def send_live_stock_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        stock_text(), reply_markup=stock_keyboard(), parse_mode='Markdown')

async def send_support_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎧 *CONTACT SUPPORT*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "For any issues, questions, or requests — contact admin directly.\n\n"
        "👨‍💻 *Developer:* ✆  RGX NUMBER BOT ",
        reply_markup=support_keyboard(), parse_mode='Markdown')

async def send_admin_panel_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized!")
        return
    admin_mode[user_id] = True
    admin_panel_state[user_id] = "main"
    await update.message.reply_text(
        "🛡 *ADMIN PANEL*\n\n👨‍💻 *Developer:* ✆  RGX NUMBER BOT \n\nSelect an action below:",
        reply_markup=admin_panel_keyboard(), parse_mode='Markdown')

# ==================== GENERIC TEXT HANDLER ====================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    handled = await handle_admin_text(update, context)
    if handled:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()

    # No join verification required anymore – direct dispatch
    if text == BTN_GET_NUMBER:
        await send_get_number_panel(update, context)
    elif text == BTN_LIVE_STOCK:
        await send_live_stock_panel(update, context)
    elif text == BTN_SUPPORT:
        await send_support_panel(update, context)
    elif text == BTN_ADMIN:
        await send_admin_panel_msg(update, context)

# ==================== ERROR HANDLER ====================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"Update caused error: {context.error}")

# ==================== MAIN ====================

def main():
    print("🔥 Developer ✆  RGX NUMBER BOT  Bot STARTING...")
    print("👨‍💻 Developer: ✆  RGX NUMBER BOT ")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("enteradmin", enter_admin_command))
    application.add_handler(CommandHandler("exitadmin", exit_admin_command))

    application.add_handler(CallbackQueryHandler(check_joined_callback, pattern="^check_joined$"))
    application.add_handler(CallbackQueryHandler(refresh_stock_callback, pattern="^refresh_stock$"))
    application.add_handler(CallbackQueryHandler(select_country_callback, pattern=r"^sel\|"))
    application.add_handler(CallbackQueryHandler(next_number_callback, pattern="^next_number$"))
    application.add_handler(CallbackQueryHandler(back_to_countries_callback, pattern="^back_to_countries$"))
    application.add_handler(CallbackQueryHandler(back_to_menu_callback, pattern="^back_to_menu$"))
    application.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu_"))

    application.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin_del\|"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))

    application.add_handler(CallbackQueryHandler(country_callback, pattern="^country_"))
    application.add_handler(CallbackQueryHandler(service_callback, pattern="^service_"))

    application.add_handler(MessageHandler(filters.Document.ALL, handle_file_upload))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    application.add_error_handler(error_handler)

    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(monitor_otp_job, interval=5, first=5)
        job_queue.run_repeating(cleanup_expired_job, interval=60, first=60)

    print(f"✅ Admin IDs: {ADMIN_IDS}")
    print(f"✅ Loaded {len(COUNTRIES_DATA)} countries from JSON file")
    print("✅ Join Check REMOVED – unrestricted access")
    print("✅ Country & Service Manager Integrated")
    print("✅ Referral & Credits Removed")
    print("✅ Keyboard Layout: 2x2 then 1x1 (+ admin 1x1)")
    print("✅ New Number Format Applied")
    print("✅ MODIFIED: NEW NUMBER now uses last used country/service from history")
    print("🔄 Starting polling...")

    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
