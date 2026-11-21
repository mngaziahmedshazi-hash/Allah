# -*- coding: utf-8 -*-
import atexit
from datetime import datetime, timedelta
import hashlib
import hmac
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
from threading import Thread
import time
import zipfile
from flask import Flask, render_template_string, request
import psutil
import requests
import telebot
from telebot import types

USDT_BDT_RATE = 120.0
MONETAG_ZONE_ID = "12284632"

TOKEN = "9023993338:AAH04Lbx9nRvlwUou0NaYH82RRsh8m912Z8"
OWNER_ID = 8452462370
ADMIN_ID = 8452462370
YOUR_USERNAME = "@freelancertradernahid"
UPDATE_CHANNEL = "hostifyofficialsbot"

BINANCE_API_KEY = "UvcIF65qTs28Fs0MnYMIXknLWg5G8oy08N8pRmm0NyOk3S1q2SN9CqKG6zScROL1"
BINANCE_SECRET_KEY = "z3YRERyDwnt7CVRT20m5j7d6AaiDgg29TOIhqocarPOoqZB0MaqRpyZKMHAkpySN"
BINANCE_PAY_ID = "1246752417"

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, "upload_bots")
IROTECH_DIR = os.path.join(BASE_DIR, "inf")
DATABASE_PATH = os.path.join(IROTECH_DIR, "bot_data.db")

FREE_USER_LIMIT = 0
SUBSCRIBED_USER_LIMIT = 15
ADMIN_LIMIT = 999
OWNER_LIMIT = float("inf")

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)

bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
banned_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
maintenance_mode = False
user_last_msg = {}
pending_bot_tokens = {}
notified_expiries = set()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_LOCK = threading.Lock()


def init_db():
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, plan_name TEXT, expiry TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS user_files (user_id INTEGER, file_name TEXT, file_type TEXT, bot_token TEXT, PRIMARY KEY (user_id, file_name))""")
        c.execute("""CREATE TABLE IF NOT EXISTS active_users (user_id INTEGER PRIMARY KEY)""")
        c.execute("""CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY)""")
        c.execute("""CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)""")
        c.execute("""CREATE TABLE IF NOT EXISTS plans (plan_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, file_limit INTEGER, price TEXT, duration INTEGER, buy_link TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS referrals (user_id INTEGER PRIMARY KEY, referred_by INTEGER, ref_count INTEGER DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS used_txids (tx_id TEXT PRIMARY KEY)""")
        c.execute("""CREATE TABLE IF NOT EXISTS vouchers (code TEXT PRIMARY KEY, days INTEGER, file_limit INTEGER, is_used INTEGER DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS ad_sessions (token TEXT PRIMARY KEY, user_id INTEGER, verified INTEGER DEFAULT 0, created_at TEXT, claimed INTEGER DEFAULT 0)""")

        try:
            c.execute("ALTER TABLE user_files ADD COLUMN bot_token TEXT")
        except Exception:
            pass

        c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (ADMIN_ID,))
        conn.commit()
        conn.close()


def load_data():
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT user_id, plan_name, expiry FROM subscriptions")
        for row in c.fetchall():
            try:
                user_subscriptions[row[0]] = {"plan_name": row[1], "expiry": datetime.fromisoformat(row[2])}
            except Exception:
                pass

        c.execute("SELECT user_id, file_name, file_type FROM user_files")
        for uid, fname, ftype in c.fetchall():
            user_files.setdefault(uid, [])
            if (fname, ftype) not in user_files[uid]:
                user_files[uid].append((fname, ftype))

        c.execute("SELECT user_id FROM active_users")
        active_users.update(u[0] for u in c.fetchall())
        c.execute("SELECT user_id FROM banned_users")
        banned_users.update(u[0] for u in c.fetchall())
        c.execute("SELECT user_id FROM admins")
        admin_ids.update(u[0] for u in c.fetchall())
        conn.close()
    except Exception as e:
        logger.error(f"Load Data Error: {e}")


init_db()
load_data()


# --- Flask Server with Strict Ad Verification ---
app = Flask("")


def get_base_url():
    render_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBAPP_URL")
    if render_url:
        return render_url.rstrip("/") if render_url.startswith("http") else f"https://{render_url.rstrip('/')}"
    port = int(os.environ.get("PORT", 8080))
    return f"http://127.0.0.1:{port}"


def create_ad_session(uid):
    token = secrets.token_urlsafe(16)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute(
            "INSERT INTO ad_sessions (token, user_id, verified, created_at, claimed) VALUES (?, ?, 0, ?, 0)",
            (token, uid, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    return token


@app.route("/ad/<token>")
def ad_page(token):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT user_id, verified, claimed FROM ad_sessions WHERE token = ?", (token,))
        row = c.fetchone()
        conn.close()

    if not row:
        return "<h3>Invalid or expired ad session. Please request a new link from the bot.</h3>", 404

    html_template = f"""
    <!DOCTYPE html>
    <html lang="bn">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>HOSTIFY ⚡ Ad Verification</title>
        <style>
            body {{ background: #090d16; color: #f8fafc; font-family: Arial, sans-serif; text-align: center; padding: 40px 15px; }}
            .card {{ background: #111827; padding: 30px; border-radius: 18px; max-width: 440px; margin: auto; border: 1px solid #1f2937; }}
            h2 {{ color: #38bdf8; }}
            p {{ color: #cbd5e1; font-size: 14px; line-height: 1.6; }}
            .btn {{ background: #2563eb; color: #fff; padding: 13px 22px; border: none; border-radius: 10px; font-size: 15px; font-weight: bold; cursor: pointer; margin-top: 15px; }}
            .status {{ margin-top: 18px; font-weight: bold; color: #22c55e; }}
        </style>
        <script src='//libtl.com/sdk.js' data-zone='{MONETAG_ZONE_ID}' data-sdk='show_{MONETAG_ZONE_ID}'></script>
    </head>
    <body>
        <div class="card">
            <h2>🚀 HOSTIFY AD VERIFICATION</h2>
            <p>VIP ক্লেইম করতে নিচের বাটনে ক্লিক করে অ্যাডটি সম্পন্ন করুন। ভেরিফাই হলে বোটে ফিরে গিয়ে <b>Verify & Claim VIP</b> চাপুন।</p>
            <button id="adBtn" class="btn" onclick="startAd()">👀 Watch Ad Now</button>
            <div id="statusMsg" class="status"></div>
        </div>
        <script>
            let secondsLeft = 6;
            function confirmVerified() {{
                fetch('/api/verify-ad', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ token: '{token}' }})
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.ok) {{
                        document.getElementById('statusMsg').innerText = '✅ অ্যাড ভেরিফাই হয়েছে! এখন বোটে গিয়ে Claim করুন।';
                    }} else {{
                        document.getElementById('statusMsg').innerText = '⚠️ ভেরিফাই করা যায়নি।';
                    }}
                }});
            }}

            function startAd() {{
                const btn = document.getElementById('adBtn');
                btn.disabled = true;
                if (typeof show_{MONETAG_ZONE_ID} === 'function') {{
                    show_{MONETAG_ZONE_ID}({{ ytag: true }}).then(() => {{
                        confirmVerified();
                    }}).catch(() => {{
                        const timer = setInterval(() => {{
                            secondsLeft--;
                            btn.innerText = '⏳ Verifying (' + secondsLeft + 's)...';
                            if (secondsLeft <= 0) {{
                                clearInterval(timer);
                                confirmVerified();
                            }}
                        }}, 1000);
                    }});
                }} else {{
                    const timer = setInterval(() => {{
                        secondsLeft--;
                        btn.innerText = '⏳ Verifying (' + secondsLeft + 's)...';
                        if (secondsLeft <= 0) {{
                            clearInterval(timer);
                            confirmVerified();
                        }}
                    }}, 1000);
                }}
            }}
        </script>
    </body>
    </html>
    """
    return render_template_string(html_template)


@app.route("/api/verify-ad", methods=["POST"])
def api_verify_ad():
    data = request.get_json(silent=True) or {}
    token = data.get("token", "").strip()
    if not token:
        return {"ok": False}, 400
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("UPDATE ad_sessions SET verified = 1 WHERE token = ?", (token,))
        updated = c.rowcount
        conn.commit()
        conn.close()
    return {"ok": updated > 0}


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()


def is_blocked_for_user(uid):
    if uid in banned_users:
        return True, "❌ আপনি এই বোট থেকে ব্যানড হয়েছেন।"
    if maintenance_mode and uid not in admin_ids:
        return True, "🛠️ বোট বর্তমানে Maintenance Mode-এ আছে। কিছুক্ষণ পর আবার চেষ্টা করুন।"
    return False, None


def is_anti_flood(uid):
    if uid in admin_ids:
        return False
    now = time.time()
    last = user_last_msg.get(uid, 0)
    if now - last < 0.5:
        return True
    user_last_msg[uid] = now
    return False


def parse_price_to_usdt(price_str):
    price_clean = str(price_str).upper().strip()
    numbers = re.findall(r"[-+]?\d*\.\d+|\d+", price_clean)
    if not numbers:
        return 0.0, price_str
    val = float(numbers[0])
    if "BDT" in price_clean or "TK" in price_clean:
        return round(val / USDT_BDT_RATE, 2), f"{price_str} (~{round(val / USDT_BDT_RATE, 2)} USDT)"
    return round(val, 2), f"{val} USDT"


def safe_extract_zip(zip_path, dest_dir):
    dest_real = os.path.realpath(dest_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            target_path = os.path.realpath(os.path.join(dest_real, member.filename))
            if not target_path.startswith(dest_real + os.sep) and target_path != dest_real:
                raise ValueError("Unsafe zip path detected.")
        zf.extractall(dest_real)


def add_plan_db(name, file_limit, price, duration, buy_link):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute(
            "INSERT INTO plans (name, file_limit, price, duration, buy_link) VALUES (?, ?, ?, ?, ?)",
            (name, int(file_limit), price, int(duration), buy_link),
        )
        conn.commit()
        conn.close()


def get_all_plans():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT plan_id, name, file_limit, price, duration, buy_link FROM plans ORDER BY plan_id ASC")
    plans = c.fetchall()
    conn.close()
    return plans


def get_plan_by_id(plan_id):
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT plan_id, name, file_limit, price, duration, buy_link FROM plans WHERE plan_id = ?", (int(plan_id),))
    plan = c.fetchone()
    conn.close()
    return plan


def delete_plan_db(plan_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("DELETE FROM plans WHERE plan_id = ?", (int(plan_id),))
        deleted = c.rowcount
        conn.commit()
        conn.close()
    return deleted > 0


def save_subscription(user_id, plan_name, expiry):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO subscriptions (user_id, plan_name, expiry) VALUES (?, ?, ?)",
            (user_id, plan_name, expiry.isoformat()),
        )
        conn.commit()
        conn.close()
    user_subscriptions[user_id] = {"plan_name": plan_name, "expiry": expiry}
    notified_expiries.discard(user_id)


def remove_subscription_db(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    user_subscriptions.pop(user_id, None)


def add_admin_db(new_admin_id):
    admin_ids.add(int(new_admin_id))
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (int(new_admin_id),))
        conn.commit()
        conn.close()


def create_voucher_db(code, days, file_limit=15):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO vouchers (code, days, file_limit, is_used) VALUES (?, ?, ?, 0)",
            (code.strip().upper(), int(days), int(file_limit)),
        )
        conn.commit()
        conn.close()


def is_txid_used(tx_id):
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT tx_id FROM used_txids WHERE tx_id=?", (str(tx_id).strip(),))
    row = c.fetchone()
    conn.close()
    return row is not None


def add_used_txid(tx_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO used_txids (tx_id) VALUES (?)", (str(tx_id).strip(),))
        conn.commit()
        conn.close()


def check_binance_payment(pay_order_id):
    if not BINANCE_API_KEY or BINANCE_API_KEY == "YOUR_NEW_BINANCE_API_KEY_HERE":
        return False, 0.0, "API Key কনফিগার করা নেই।"
    endpoint = "https://api.binance.com/sapi/v1/pay/transactions"
    timestamp = int(time.time() * 1000)
    query_string = f"timestamp={timestamp}"
    signature = hmac.new(BINANCE_SECRET_KEY.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
    url = f"{endpoint}?{query_string}&signature={signature}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            transactions = data.get("data", []) if isinstance(data, dict) else data
            for item in transactions:
                if str(item.get("orderId", "")).strip() == str(pay_order_id).strip():
                    amount = float(item.get("amount", 0.0))
                    return True, amount, f"{amount} USDT"
            return False, 0.0, "TxID Binance এ পাওয়া যায়নি।"
        return False, 0.0, "Binance API Error"
    except Exception as e:
        return False, 0.0, f"Error: {e}"


def check_force_sub(user_id):
    if user_id in admin_ids:
        return True
    try:
        channel_target = f"@{UPDATE_CHANNEL}" if not str(UPDATE_CHANNEL).startswith(("@", "-100")) else str(UPDATE_CHANNEL)
        member = bot.get_chat_member(channel_target, user_id)
        return member.status in ["member", "administrator", "creator", "restricted"]
    except Exception as e:
        logger.error(f"Force Sub Check Error for {user_id}: {e}")
        return False


def get_user_folder(user_id):
    folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(folder, exist_ok=True)
    return folder


def get_user_file_limit(user_id):
    if user_id == OWNER_ID:
        return OWNER_LIMIT
    if user_id in admin_ids:
        return ADMIN_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]["expiry"] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT


def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))


def is_bot_running(script_owner_id, file_name):
    skey = f"{script_owner_id}_{file_name}"
    sinfo = bot_scripts.get(skey)
    if sinfo and sinfo.get("process"):
        try:
            proc = psutil.Process(sinfo["process"].pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False
    return False


def kill_process_tree(sinfo):
    try:
        proc = sinfo.get("process")
        if proc and hasattr(proc, "pid"):
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                child.terminate()
            parent.terminate()
    except Exception:
        pass
    try:
        lf = sinfo.get("log_file")
        if lf and not lf.closed:
            lf.close()
    except Exception:
        pass


def cleanup_all_scripts():
    for skey in list(bot_scripts.keys()):
        kill_process_tree(bot_scripts[skey])
        bot_scripts.pop(skey, None)


atexit.register(cleanup_all_scripts)


def run_script(script_path, owner_id, folder, fname, msg=None, bot_api_token=""):
    skey = f"{owner_id}_{fname}"
    if skey in bot_scripts:
        kill_process_tree(bot_scripts[skey])
    try:
        log_path = os.path.join(folder, f"{os.path.splitext(fname)[0]}.log")
        log_file = open(log_path, "a", encoding="utf-8", errors="ignore")
        env = os.environ.copy()
        if bot_api_token:
            env["BOT_TOKEN"] = bot_api_token
            env["TELEGRAM_BOT_TOKEN"] = bot_api_token
        proc = subprocess.Popen(
            [sys.executable, script_path],
            cwd=folder,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.PIPE,
            env=env,
        )
        bot_scripts[skey] = {
            "process": proc,
            "log_file": log_file,
            "file_name": fname,
            "file_path": script_path,
            "owner_id": owner_id,
            "start_time": datetime.now(),
            "folder": folder,
            "type": "py",
            "bot_token": bot_api_token,
            "auto_restart": True,
        }
        if msg:
            bot.reply_to(msg, f"🚀 **PYTHON BOT STARTED!**\n📄 File: `{fname}`\n🆔 PID: `{proc.pid}`", parse_mode="Markdown")
    except Exception as e:
        if msg:
            bot.reply_to(msg, f"❌ Start Error: {e}")


def run_js_script(script_path, owner_id, folder, fname, msg=None, bot_api_token=""):
    skey = f"{owner_id}_{fname}"
    if skey in bot_scripts:
        kill_process_tree(bot_scripts[skey])
    try:
        log_path = os.path.join(folder, f"{os.path.splitext(fname)[0]}.log")
        log_file = open(log_path, "a", encoding="utf-8", errors="ignore")
        env = os.environ.copy()
        if bot_api_token:
            env["BOT_TOKEN"] = bot_api_token
            env["TELEGRAM_BOT_TOKEN"] = bot_api_token
        proc = subprocess.Popen(
            ["node", script_path],
            cwd=folder,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.PIPE,
            env=env,
        )
        bot_scripts[skey] = {
            "process": proc,
            "log_file": log_file,
            "file_name": fname,
            "file_path": script_path,
            "owner_id": owner_id,
            "start_time": datetime.now(),
            "folder": folder,
            "type": "js",
            "bot_token": bot_api_token,
            "auto_restart": True,
        }
        if msg:
            bot.reply_to(msg, f"🚀 **NODE.JS BOT STARTED!**\n📄 File: `{fname}`\n🆔 PID: `{proc.pid}`", parse_mode="Markdown")
    except Exception as e:
        if msg:
            bot.reply_to(msg, f"❌ JS Start Error: {e}")


def auto_crash_recovery_loop():
    while True:
        time.sleep(20)
        try:
            for skey, sinfo in list(bot_scripts.items()):
                if not sinfo.get("auto_restart", True):
                    continue
                oid = sinfo.get("owner_id")
                fname = sinfo.get("file_name")
                fpath = sinfo.get("file_path")
                folder = sinfo.get("folder")
                stype = sinfo.get("type")
                btoken = sinfo.get("bot_token", "")
                if oid and fname and fpath and os.path.exists(fpath) and not is_bot_running(oid, fname):
                    logger.info(f"Auto-restarting crashed bot: {skey}")
                    if stype == "js":
                        run_js_script(fpath, oid, folder, fname, None, btoken)
                    else:
                        run_script(fpath, oid, folder, fname, None, btoken)

            now = datetime.now()
            for uid, sub in list(user_subscriptions.items()):
                if sub["expiry"] <= now and uid not in notified_expiries:
                    notified_expiries.add(uid)
                    try:
                        bot.send_message(uid, "⏳ আপনার VIP সাবস্ক্রিপশনের মেয়াদ শেষ হয়ে গেছে। পুনরায় **Earn VIP** বা **View Plans** থেকে সচল করুন।", parse_mode="Markdown")
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Background monitor error: {e}")


def run_all_user_scripts():
    started_count = 0
    for uid, flist in list(user_files.items()):
        folder = get_user_folder(uid)
        token_file = os.path.join(folder, "bot_token.txt")
        saved_token = ""
        if os.path.exists(token_file):
            with open(token_file, "r", encoding="utf-8", errors="ignore") as tf:
                saved_token = tf.read().strip()

        for fname, ftype in flist:
            fpath = os.path.join(folder, fname)
            if os.path.exists(fpath) and not is_bot_running(uid, fname):
                if ftype == "js" or fname.endswith(".js"):
                    run_js_script(fpath, uid, folder, fname, None, saved_token)
                    started_count += 1
                elif ftype == "py" or fname.endswith(".py"):
                    run_script(fpath, uid, folder, fname, None, saved_token)
                    started_count += 1
    return started_count


def save_user_file(uid, fname, ftype, bot_api_token=""):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO user_files (user_id, file_name, file_type, bot_token) VALUES (?, ?, ?, ?)",
            (uid, fname, ftype, bot_api_token),
        )
        conn.commit()
        conn.close()
    user_files.setdefault(uid, [])
    user_files[uid] = [f for f in user_files[uid] if f[0] != fname]
    user_files[uid].append((fname, ftype))


def remove_user_file_db(uid, fname):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("DELETE FROM user_files WHERE user_id = ? AND file_name = ?", (uid, fname))
        conn.commit()
        conn.close()
    if uid in user_files:
        user_files[uid] = [f for f in user_files[uid] if f[0] != fname]


def main_menu_markup(uid):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    rows = [
        ["📢 𝗨𝗽𝗱𝗮𝘁𝗲𝘀 𝗖𝗵𝗮𝗻𝗻𝗲𝗹", "⚡ 𝗦𝗽𝗲𝗲𝗱 & 𝗣𝗶𝗻𝗴"],
        ["🚀 𝗨𝗽𝗹𝗼𝗮𝗱 𝗕𝗼𝘁", "📁 𝗠𝗮𝗻𝗮𝗴𝗲 𝗕𝗼𝘁𝘀"],
        ["💳 𝗩𝗶𝗲𝘄 𝗣𝗹𝗮𝗻𝘀", "🎁 𝗘𝗮𝗿𝗻 𝗩𝗜𝗣"],
        ["🎟️ 𝗥𝗲𝗱𝗲𝗲𝗺 𝗖𝗼𝗱𝗲", "🔗 𝗥𝗲𝗳𝗲𝗿𝗿𝗮𝗹"],
        ["👤 𝗠𝘆 𝗣𝗿𝗼𝗳𝗶𝗹𝗲", "💬 𝗦𝘂𝗽𝗽𝗼𝗿𝘁"],
        ["👑 𝗖𝗼𝗻𝘁𝗮𝗰𝘁 𝗢𝘄𝗻𝗲𝗿"],
    ]
    if uid in admin_ids:
        rows.insert(4, ["🛡️ 𝗔𝗱𝗺𝗶𝗻 𝗣𝗮𝗻𝗲𝗹", "📊 𝗦𝘆𝘀𝘁𝗲𝗺 𝗦𝘁𝗮𝘁𝘀"])
    for r in rows:
        markup.add(*[types.KeyboardButton(t) for t in r])
    return markup


def admin_panel_markup():
    maint_label = "🛠️ Maint: ON (Click OFF)" if maintenance_mode else "🛠️ Maint: OFF (Click ON)"
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("➕ Add Plan", callback_data="add_plan_init"),
        types.InlineKeyboardButton("🗑️ Manage Plans", callback_data="manage_plans"),
    )
    m.add(
        types.InlineKeyboardButton("💎 Add Sub", callback_data="add_subscription"),
        types.InlineKeyboardButton("❌ Remove Sub", callback_data="remove_subscription"),
    )
    m.add(
        types.InlineKeyboardButton("🎟️ Create Voucher", callback_data="create_voucher"),
        types.InlineKeyboardButton(maint_label, callback_data="toggle_maint"),
    )
    m.add(
        types.InlineKeyboardButton("🚫 Ban", callback_data="ban_user"),
        types.InlineKeyboardButton("✅ Unban", callback_data="unban_user"),
    )
    m.add(
        types.InlineKeyboardButton("📣 Broadcast", callback_data="broadcast"),
        types.InlineKeyboardButton("⚙️ Run All", callback_data="run_all_scripts"),
    )
    m.add(
        types.InlineKeyboardButton("🛑 Stop All", callback_data="stop_all_scripts"),
        types.InlineKeyboardButton("➕ Add Admin", callback_data="add_admin"),
    )
    m.add(types.InlineKeyboardButton("📊 System Stats", callback_data="stats"))
    return m


@bot.message_handler(commands=["start"])
def start_cmd(message):
    uid = message.from_user.id
    if is_anti_flood(uid):
        return
    blocked, reason = is_blocked_for_user(uid)
    if blocked:
        bot.reply_to(message, reason)
        return
    if not check_force_sub(uid):
        m = types.InlineKeyboardMarkup()
        m.add(types.InlineKeyboardButton("📢 Join Official Channel", url=f"https://t.me/{UPDATE_CHANNEL}"))
        m.add(types.InlineKeyboardButton("🔄 Verify Membership", callback_data="check_join"))
        bot.send_message(message.chat.id, "⚠️ আগে অফিশিয়াল চ্যানেলে জয়েন করুন, তারপর Verify Membership চাপুন।", reply_markup=m)
        return

    args = message.text.split()
    if len(args) > 1 and uid not in active_users:
        try:
            ref_id = int(args[1])
            if ref_id != uid:
                with DB_LOCK:
                    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
                    c = conn.cursor()
                    c.execute("INSERT OR IGNORE INTO referrals (user_id, referred_by) VALUES (?, ?)", (uid, ref_id))
                    c.execute("INSERT OR IGNORE INTO referrals (user_id, ref_count) VALUES (?, 0)", (ref_id,))
                    c.execute("UPDATE referrals SET ref_count = ref_count + 1 WHERE user_id = ?", (ref_id,))
                    conn.commit()
                    conn.close()
        except Exception:
            pass

    if uid not in active_users:
        active_users.add(uid)
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO active_users (user_id) VALUES (?)", (uid,))
            conn.commit()
            conn.close()

    bot.send_message(
        message.chat.id,
        f"✨ **WELCOME TO HOSTIFY CLOUD** ✨\n👋 Hello, **{message.from_user.first_name}**!\n⚡ 24/7 Python & Node.js Bot Hosting.",
        reply_markup=main_menu_markup(uid),
        parse_mode="Markdown",
    )


@bot.callback_query_handler(func=lambda call: call.data == "check_join")
def check_join_cb(call):
    uid = call.from_user.id
    blocked, reason = is_blocked_for_user(uid)
    if blocked:
        bot.answer_callback_query(call.id, reason, show_alert=True)
        return
    if check_force_sub(uid):
        bot.answer_callback_query(call.id, "✅ Verified!")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.send_message(call.message.chat.id, "✅ চ্যানেল মেম্বারশিপ ভেরিফাইড হয়েছে!", reply_markup=main_menu_markup(uid))
    else:
        bot.answer_callback_query(call.id, "❌ আপনি এখনো চ্যানেলে জয়েন করেননি!", show_alert=True)


def _logic_my_profile(message):
    uid = message.from_user.id
    status = (
        "👑 System Owner"
        if uid == OWNER_ID
        else (
            "🛡️ Administrator"
            if uid in admin_ids
            else (
                "💎 VIP Enterprise Active"
                if uid in user_subscriptions and user_subscriptions[uid]["expiry"] > datetime.now()
                else "🆓 Standard Free Tier"
            )
        )
    )
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT ref_count FROM referrals WHERE user_id = ?", (uid,))
    row = c.fetchone()
    ref_count = row[0] if row else 0
    conn.close()

    expiry_str = "Lifetime" if uid in admin_ids else "None"
    if uid not in admin_ids and uid in user_subscriptions and user_subscriptions[uid]["expiry"] > datetime.now():
        expiry_str = user_subscriptions[uid]["expiry"].strftime("%Y-%m-%d %H:%M")

    text = (
        f"👤 **USER ACCOUNT PROFILE**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Telegram ID: `{uid}`\n"
        f"🔰 Account Tier: `{status}`\n"
        f"⏳ Expiry: `{expiry_str}`\n"
        f"📁 Deployed Bots: `{get_user_file_count(uid)}` / `{get_user_file_limit(uid)}`\n"
        f"🎁 Successful Referrals: `{ref_count} users`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    bot.reply_to(message, text, parse_mode="Markdown")


def _logic_referral(message):
    uid = message.from_user.id
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT ref_count FROM referrals WHERE user_id = ?", (uid,))
    row = c.fetchone()
    ref_count = row[0] if row else 0
    conn.close()
    bot_user = bot.get_me().username
    bot.reply_to(
        message,
        f"🎁 **YOUR REFERRAL LINK**\n━━━━━━━━━━━━━━━━━━━\n`https://t.me/{bot_user}?start={uid}`\n\n👥 Total Invited: `{ref_count}`",
        parse_mode="Markdown",
    )


def _logic_redeem_prompt(message):
    msg = bot.reply_to(message, "🎟️ আপনার ভাউচার বা প্রোমো কোডটি লিখুন:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_redeem_voucher)


def process_redeem_voucher(message):
    code = message.text.strip().upper()
    uid = message.from_user.id
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT days, file_limit, is_used FROM vouchers WHERE code = ?", (code,))
        row = c.fetchone()
        if not row:
            conn.close()
            bot.reply_to(message, "❌ ভুল অথবা অকার্যকর ভাউচার কোড!")
            return
        days, limit, is_used = row
        if is_used == 1:
            conn.close()
            bot.reply_to(message, "❌ এই ভাউচার কোডটি ইতিমধ্যে ব্যবহার করা হয়েছে!")
            return
        c.execute("UPDATE vouchers SET is_used = 1 WHERE code = ?", (code,))
        conn.commit()
        conn.close()
    expiry = datetime.now() + timedelta(days=days)
    save_subscription(uid, f"Voucher VIP ({days}d)", expiry)
    bot.reply_to(
        message,
        f"🎉 **ভাউচার কোড সফলভাবে রিডিম হয়েছে!**\nআপনার অ্যাকাউন্টে `{days}` দিনের VIP প্ল্যান সচল করা হয়েছে।",
        parse_mode="Markdown",
    )


def _logic_earn_vip(message):
    uid = message.from_user.id
    session_token = create_ad_session(uid)
    ad_url = f"{get_base_url()}/ad/{session_token}"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("👀 Watch Ad (Required)", url=ad_url))
    markup.add(types.InlineKeyboardButton("✅ Verify Ad & Claim VIP", callback_data=f"verify_claim_{session_token}"))

    bot.reply_to(
        message,
        "🎁 **EARN FREE VIP (AD VERIFICATION REQUIRED)**\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "১. **Watch Ad** বাটনে ক্লিক করে অ্যাডটি সম্পন্ন করুন।\n"
        "২. অ্যাড ভেরিফাই হওয়ার পর **Verify Ad & Claim VIP** চাপুন।\n"
        "*অ্যাড না দেখলে VIP একটিভ বা কোড দেওয়া হবে না।*",
        reply_markup=markup,
        parse_mode="Markdown",
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("verify_claim_"))
def handle_verify_claim(call):
    uid = call.from_user.id
    blocked, reason = is_blocked_for_user(uid)
    if blocked:
        bot.answer_callback_query(call.id, reason, show_alert=True)
        return
    token = call.data.replace("verify_claim_", "", 1)

    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT user_id, verified, claimed FROM ad_sessions WHERE token = ?", (token,))
        row = c.fetchone()
        if not row:
            conn.close()
            bot.answer_callback_query(call.id, "❌ সেশন পাওয়া যায়নি। আবার Earn VIP চাপুন।", show_alert=True)
            return

        sess_uid, verified, claimed = row
        if sess_uid != uid:
            conn.close()
            bot.answer_callback_query(call.id, "❌ এটি আপনার সেশন নয়!", show_alert=True)
            return
        if claimed == 1:
            conn.close()
            bot.answer_callback_query(call.id, "⚠️ এই অ্যাড সেশন দিয়ে ইতিমধ্যে ক্লেইম করা হয়েছে!", show_alert=True)
            return
        if verified != 1:
            conn.close()
            bot.answer_callback_query(call.id, "❌ আপনি এখনো অ্যাড দেখেননি/ভেরিফাই হয়নি! আগে Watch Ad চাপুন।", show_alert=True)
            return

        c.execute("UPDATE ad_sessions SET claimed = 1 WHERE token = ?", (token,))
        conn.commit()
        conn.close()

    expiry = datetime.now() + timedelta(days=7)
    save_subscription(uid, "Verified Ad VIP", expiry)
    bot.answer_callback_query(call.id, "✅ অ্যাড ভেরিফাইড! ৭ দিনের VIP একটিভ হয়েছে।", show_alert=True)
    bot.send_message(
        call.message.chat.id,
        "🎉 **AD VERIFIED & VIP ACTIVATED!**\n"
        "💎 Plan: `7 Days VIP` (Limit: `15 Files`)\n"
        f"⏳ Expiry: `{expiry.strftime('%Y-%m-%d %H:%M')}`",
        parse_mode="Markdown",
    )


def _logic_view_plans(m_or_c):
    chat_id = m_or_c.chat.id if isinstance(m_or_c, telebot.types.Message) else m_or_c.message.chat.id
    plans = get_all_plans()
    if not plans:
        bot.send_message(chat_id, "ℹ️ বর্তমানে কোনো প্ল্যান যুক্ত করা নেই।")
        return
    for p in plans:
        pid, name, limit, price, duration, _ = p
        usdt, fprice = parse_price_to_usdt(price)
        markup = types.InlineKeyboardMarkup()
        if usdt == 0 or str(price).lower() in ["free", "0"]:
            markup.add(types.InlineKeyboardButton(f"🎁 Claim {name} (Free)", callback_data=f"claim_free_{pid}"))
        else:
            markup.add(types.InlineKeyboardButton(f"🛒 Purchase {name} ({usdt} USDT)", callback_data=f"buy_binance_{pid}"))
        bot.send_message(
            chat_id,
            f"📦 **PLAN:** `{name}`\n📁 Limit: `{limit}`\n⏱️ Duration: `{duration} Days`\n💰 Price: `{fprice}`",
            reply_markup=markup,
            parse_mode="Markdown",
        )


def _logic_upload_prompt(message):
    uid = message.from_user.id
    if not (uid in admin_ids or uid == OWNER_ID or (uid in user_subscriptions and user_subscriptions[uid]["expiry"] > datetime.now())):
        bot.reply_to(message, "❌ ফাইল হোস্ট করতে প্রথমে VIP প্ল্যান একটিভ করুন (**🎁 Earn VIP** বা **💳 View Plans** থেকে)।", parse_mode="Markdown")
        return
    bot.reply_to(
        message,
        "🚀 **বট আপলোড করুন**\nআপনার `.py`, `.js` অথবা `.zip` ফাইলটি এখন সেন্ড করুন।\n*(Zip ফাইল দিলে ভেতরের `.py` / `.js` ফাইল অটোমেটিক লিস্টে যুক্ত হবে)*",
        parse_mode="Markdown",
    )


@bot.message_handler(content_types=["document"])
def handle_docs(message):
    uid = message.from_user.id
    blocked, reason = is_blocked_for_user(uid)
    if blocked:
        bot.reply_to(message, reason)
        return
    if not (uid in admin_ids or uid == OWNER_ID or (uid in user_subscriptions and user_subscriptions[uid]["expiry"] > datetime.now())):
        bot.reply_to(message, "❌ ফাইল আপলোড করতে প্রথমে VIP প্ল্যান একটিভ করুন।")
        return

    limit = get_user_file_limit(uid)
    if get_user_file_count(uid) >= limit:
        bot.reply_to(message, f"⚠️ আপনার ফাইল লিমিট (`{limit}`) পূর্ণ হয়ে গেছে।", parse_mode="Markdown")
        return

    doc = message.document
    fname = os.path.basename(doc.file_name)
    fext = os.path.splitext(fname)[1].lower()
    if fext not in [".py", ".js", ".zip"]:
        bot.reply_to(message, "⚠️ শুধুমাত্র `.py`, `.js` এবং `.zip` ফাইল সাপোর্টেড!")
        return

    try:
        wait_msg = bot.reply_to(message, f"⏳ `{fname}` ডাউনলোড হচ্ছে...", parse_mode="Markdown")
        finfo = bot.get_file(doc.file_id)
        content = bot.download_file(finfo.file_path)
        folder = get_user_folder(uid)
        fpath = os.path.join(folder, fname)
        with open(fpath, "wb") as f:
            f.write(content)

        if fext == ".zip":
            safe_extract_zip(fpath, folder)
            os.remove(fpath)
            extracted_scripts = []
            for root, _, files in os.walk(folder):
                for file in files:
                    if file.endswith((".py", ".js")):
                        rel_path = os.path.relpath(os.path.join(root, file), folder)
                        ftype = "py" if file.endswith(".py") else "js"
                        save_user_file(uid, rel_path, ftype)
                        extracted_scripts.append(rel_path)

            if not extracted_scripts:
                bot.edit_message_text("⚠️ Zip এক্সট্রাক্ট হয়েছে, কিন্তু ভেতরে কোনো `.py` বা `.js` ফাইল পাওয়া যায়নি।", message.chat.id, wait_msg.message_id, parse_mode="Markdown")
                return

            target_script = extracted_scripts[0]
            target_full_path = os.path.join(folder, target_script)
            pending_bot_tokens[uid] = {
                "folder": folder,
                "fname": target_script,
                "fpath": target_full_path,
                "ftype": "py" if target_script.endswith(".py") else "js",
            }
            bot.edit_message_text(f"✅ Zip এক্সট্রাক্ট হয়েছে! পাওয়া ফাইল: `{target_script}`", message.chat.id, wait_msg.message_id, parse_mode="Markdown")
            msg = bot.send_message(
                message.chat.id,
                "🔑 যে বটটি হোস্ট করবেন, সেটির **Bot API Token** পাঠান (অথবা কোডে টোকেন বসানো থাকলে `skip` লিখুন):",
                parse_mode="Markdown",
            )
            bot.register_next_step_handler(msg, process_bot_token_and_start)
            return

        bot.edit_message_text(f"✅ `{fname}` আপলোড হয়েছে!", message.chat.id, wait_msg.message_id, parse_mode="Markdown")
        pending_bot_tokens[uid] = {
            "folder": folder,
            "fname": fname,
            "fpath": fpath,
            "ftype": fext[1:],
        }
        msg = bot.send_message(
            message.chat.id,
            "🔑 যে বটটি হোস্ট করবেন, সেটির **Bot API Token** পাঠান (অথবা কোডে টোকেন বসানো থাকলে `skip` লিখুন):",
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(msg, process_bot_token_and_start)
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")


def process_bot_token_and_start(message):
    uid = message.from_user.id
    info = pending_bot_tokens.pop(uid, None)
    if not info:
        return
    raw_token = message.text.strip()
    bot_api_token = "" if raw_token.lower() == "skip" else raw_token

    if bot_api_token:
        with open(os.path.join(info["folder"], "bot_token.txt"), "w", encoding="utf-8") as tf:
            tf.write(bot_api_token)
        try:
            with open(info["fpath"], "r", encoding="utf-8", errors="ignore") as sf:
                code_txt = sf.read()
            if "YOUR_BOT_TOKEN_HERE" in code_txt:
                code_txt = code_txt.replace("YOUR_BOT_TOKEN_HERE", bot_api_token)
                with open(info["fpath"], "w", encoding="utf-8") as sf:
                    sf.write(code_txt)
        except Exception:
            pass

    save_user_file(uid, info["fname"], info["ftype"], bot_api_token)
    if info["ftype"] == "js":
        run_js_script(info["fpath"], uid, info["folder"], info["fname"], message, bot_api_token)
    else:
        run_script(info["fpath"], uid, info["folder"], info["fname"], message, bot_api_token)


def _logic_check_files(message):
    uid = message.from_user.id
    flist = user_files.get(uid, [])
    if not flist:
        bot.reply_to(message, "📂 আপনার কোনো ফাইল হোস্ট করা নেই।")
        return
    m = types.InlineKeyboardMarkup(row_width=1)
    for fname, ftype in flist:
        status = "🟢 Running" if is_bot_running(uid, fname) else "🔴 Stopped"
        m.add(types.InlineKeyboardButton(f"📄 {fname} [{ftype.upper()}] — {status}", callback_data=f"file_{uid}_{fname}"))
    bot.reply_to(message, "📁 **Your Hosted Bots:**", reply_markup=m, parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    uid = call.from_user.id
    data = call.data

    if data.startswith("verify_claim_"):
        handle_verify_claim(call)
        return

    blocked, reason = is_blocked_for_user(uid)
    if blocked:
        bot.answer_callback_query(call.id, reason, show_alert=True)
        return

    if data == "view_plans_cb":
        _logic_view_plans(call)
    elif data.startswith("claim_free_"):
        pid = int(data.split("_")[2])
        plan = get_plan_by_id(pid)
        if plan:
            save_subscription(uid, plan[1], datetime.now() + timedelta(days=plan[4]))
            bot.answer_callback_query(call.id, "🎉 Claimed!")
            bot.send_message(call.message.chat.id, f"🎉 `{plan[1]}` সচল হয়েছে।", parse_mode="Markdown")

    elif data.startswith("buy_binance_"):
        pid = int(data.split("_")[2])
        plan = get_plan_by_id(pid)
        if plan:
            usdt, _ = parse_price_to_usdt(plan[3])
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("🔍 Submit TxID", callback_data=f"sub_tx_{pid}"))
            bot.send_message(call.message.chat.id, f"💛 Binance Pay ID: `{BINANCE_PAY_ID}`\nAmount: `{usdt} USDT`", reply_markup=m, parse_mode="Markdown")

    elif data.startswith("sub_tx_"):
        pid = int(data.split("_")[2])
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "📩 TxID পাঠান:")
        bot.register_next_step_handler(msg, lambda m: process_txid(m, pid))

    elif data.startswith("file_"):
        _, owner_id, fname = data.split("_", 2)
        running = is_bot_running(int(owner_id), fname)
        m = types.InlineKeyboardMarkup(row_width=2)
        m.add(
            types.InlineKeyboardButton("🛑 Stop" if running else "▶️ Start", callback_data=f"{'stop' if running else 'start'}_{owner_id}_{fname}"),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f"restart_{owner_id}_{fname}"),
        )
        m.add(
            types.InlineKeyboardButton("📜 Logs", callback_data=f"log_{owner_id}_{fname}"),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f"del_{owner_id}_{fname}"),
        )
        bot.send_message(call.message.chat.id, f"📄 File: `{fname}`\nStatus: `{'Running' if running else 'Stopped'}`", reply_markup=m, parse_mode="Markdown")

    elif data.startswith(("stop_", "start_", "restart_", "log_", "del_")):
        action, owner_id, fname = data.split("_", 2)
        oid = int(owner_id)
        skey = f"{oid}_{fname}"
        folder = get_user_folder(oid)
        fpath = os.path.join(folder, fname)

        if action == "stop":
            if skey in bot_scripts:
                bot_scripts[skey]["auto_restart"] = False
                kill_process_tree(bot_scripts[skey])
                del bot_scripts[skey]
            bot.answer_callback_query(call.id, "Stopped!")
            bot.send_message(call.message.chat.id, f"🛑 `{fname}` বন্ধ করা হয়েছে।", parse_mode="Markdown")
        elif action in ("start", "restart"):
            if os.path.exists(fpath):
                token_file = os.path.join(folder, "bot_token.txt")
                saved_token = ""
                if os.path.exists(token_file):
                    with open(token_file, "r", encoding="utf-8", errors="ignore") as tf:
                        saved_token = tf.read().strip()
                if fname.endswith(".js"):
                    run_js_script(fpath, oid, folder, fname, call.message, saved_token)
                else:
                    run_script(fpath, oid, folder, fname, call.message, saved_token)
                bot.answer_callback_query(call.id, "Done!")
            else:
                bot.answer_callback_query(call.id, "File not found!", show_alert=True)
        elif action == "log":
            log_path = os.path.join(folder, f"{os.path.splitext(fname)[0]}.log")
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    logs = f.read()[-1500:]
                bot.send_message(call.message.chat.id, f"📜 **Logs (`{fname}`):**\n```\n{logs or 'Empty'}\n```", parse_mode="Markdown")
            else:
                bot.answer_callback_query(call.id, "No logs yet!", show_alert=True)
        elif action == "del":
            if skey in bot_scripts:
                bot_scripts[skey]["auto_restart"] = False
                kill_process_tree(bot_scripts[skey])
                del bot_scripts[skey]
            remove_user_file_db(oid, fname)
            if os.path.exists(fpath):
                os.remove(fpath)
            bot.answer_callback_query(call.id, "Deleted!")
            bot.send_message(call.message.chat.id, f"🗑️ `{fname}` ডিলিট করা হয়েছে।", parse_mode="Markdown")

    # --- Admin actions ---
    elif data == "add_plan_init" and uid in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            call.message.chat.id,
            "📝 **Format:** `Name | Limit | Price | Duration | Link`\nউদাহরণ: `VIP | 15 | 5 USDT | 30 | https://t.me/hostifyofficialsbot`",
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(msg, handle_add_plan_step)

    elif data == "manage_plans" and uid in admin_ids:
        plans = get_all_plans()
        if not plans:
            bot.answer_callback_query(call.id, "কোনো প্ল্যান নেই!", show_alert=True)
            return
        m = types.InlineKeyboardMarkup(row_width=1)
        for p in plans:
            pid, name, limit, price, duration, _ = p
            m.add(types.InlineKeyboardButton(f"🗑️ Delete: {name} ({price} / {duration}d)", callback_data=f"del_plan_{pid}"))
        bot.answer_callback_query(call.id)
        bot.edit_message_text("🗑️ যে প্ল্যানটি ডিলিট করতে চান সেটিতে ক্লিক করুন:", call.message.chat.id, call.message.message_id, reply_markup=m)

    elif data.startswith("del_plan_") and uid in admin_ids:
        try:
            pid = int(data.replace("del_plan_", "", 1))
        except ValueError:
            bot.answer_callback_query(call.id, "Invalid Plan ID!", show_alert=True)
            return
        if delete_plan_db(pid):
            bot.answer_callback_query(call.id, "✅ প্ল্যান ডিলিট হয়েছে!", show_alert=True)
            plans = get_all_plans()
            if plans:
                m = types.InlineKeyboardMarkup(row_width=1)
                for p in plans:
                    m.add(types.InlineKeyboardButton(f"🗑️ Delete: {p[1]} ({p[3]} / {p[4]}d)", callback_data=f"del_plan_{p[0]}"))
                bot.edit_message_text("🗑️ যে প্ল্যানটি ডিলিট করতে চান সেটিতে ক্লিক করুন:", call.message.chat.id, call.message.message_id, reply_markup=m)
            else:
                bot.edit_message_text("✅ সব প্ল্যান ডিলিট করা হয়েছে। আর কোনো প্ল্যান বাকি নেই।", call.message.chat.id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "❌ প্ল্যানটি পাওয়া যায়নি!", show_alert=True)

    elif data == "add_subscription" and uid in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "💎 **Format:** `UserID PlanName Days`\nউদাহরণ: `8452462370 VIP 30`", parse_mode="Markdown")
        bot.register_next_step_handler(msg, handle_add_sub_step)

    elif data == "remove_subscription" and uid in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "❌ সাবস্ক্রিপশন রিমুভ করতে User ID পাঠান:")
        bot.register_next_step_handler(msg, handle_remove_sub_step)

    elif data == "create_voucher" and uid in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "🎟️ **Format:** `CODE DAYS LIMIT`\nউদাহরণ: `HOSTIFY7 7 15`", parse_mode="Markdown")
        bot.register_next_step_handler(msg, handle_create_voucher_step)

    elif data == "add_admin" and uid in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "➕ নতুন Admin-এর Telegram User ID পাঠান:")
        bot.register_next_step_handler(msg, handle_add_admin_step)

    elif data == "toggle_maint" and uid in admin_ids:
        global maintenance_mode
        maintenance_mode = not maintenance_mode
        state_text = "ON (Users Blocked)" if maintenance_mode else "OFF (Users Allowed)"
        bot.answer_callback_query(call.id, f"Maintenance: {state_text}", show_alert=True)
        bot.edit_message_text(
            f"🛡️ **ADMIN CONTROL CONSOLE**\n🛠️ Maintenance Mode: `{state_text}`",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=admin_panel_markup(),
            parse_mode="Markdown",
        )

    elif data == "ban_user" and uid in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "🚫 ব্যান করার জন্য User ID পাঠান:")
        bot.register_next_step_handler(msg, handle_ban_step)

    elif data == "unban_user" and uid in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "✅ আনব্যান করার জন্য User ID পাঠান:")
        bot.register_next_step_handler(msg, handle_unban_step)

    elif data == "broadcast" and uid in admin_ids:
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "📢 সমস্ত ইউজারের কাছে পাঠানোর জন্য ব্রডকাস্ট মেসেজটি সেন্ড করুন:")
        bot.register_next_step_handler(msg, process_broadcast)

    elif data == "run_all_scripts" and uid in admin_ids:
        count = run_all_user_scripts()
        bot.answer_callback_query(call.id, f"Started {count} bots!", show_alert=True)
        bot.send_message(call.message.chat.id, f"⚙️ মোট `{count}`টি বন্ধ থাকা বট চালু করা হয়েছে।", parse_mode="Markdown")

    elif data == "stop_all_scripts" and uid in admin_ids:
        for skey in list(bot_scripts.keys()):
            bot_scripts[skey]["auto_restart"] = False
            kill_process_tree(bot_scripts[skey])
            del bot_scripts[skey]
        bot.answer_callback_query(call.id, "All bots terminated!", show_alert=True)
        bot.send_message(call.message.chat.id, "🛑 সব রানিং বট বন্ধ করা হয়েছে।")

    elif data == "stats" and uid in admin_ids:
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"📊 **ENTERPRISE SYSTEM METRICS**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Total Unique Users: `{len(active_users)}`\n"
            f"💎 Active Subscriptions: `{len(user_subscriptions)}`\n"
            f"🚀 Running Bot Instances: `{len(bot_scripts)}`\n"
            f"🚫 Blacklisted Users: `{len(banned_users)}`\n"
            f"🛠️ Maintenance Mode: `{'ON' if maintenance_mode else 'OFF'}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode="Markdown",
        )


def handle_add_plan_step(m):
    try:
        parts = [x.strip() for x in m.text.split("|")]
        if len(parts) < 4:
            bot.reply_to(m, "❌ সঠিক ফরম্যাট দিন: `Name | Limit | Price | Duration | Link`", parse_mode="Markdown")
            return
        name, limit, price, duration = parts[0], int(parts[1]), parts[2], int(parts[3])
        link = parts[4] if len(parts) > 4 else ""
        add_plan_db(name, limit, price, duration, link)
        bot.reply_to(m, f"✅ প্ল্যান `{name}` সফলভাবে যুক্ত হয়েছে!", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(m, f"❌ Error: {e}")


def handle_add_sub_step(m):
    try:
        parts = m.text.split()
        target_uid, plan_name, days = int(parts[0]), parts[1], int(parts[2])
        save_subscription(target_uid, plan_name, datetime.now() + timedelta(days=days))
        bot.reply_to(m, f"✅ ইউজার `{target_uid}` এর জন্য `{days}` দিনের `{plan_name}` সাবস্ক্রিপশন যুক্ত হয়েছে।", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(m, f"❌ Error: {e}")


def handle_remove_sub_step(m):
    try:
        target_uid = int(m.text.strip())
        remove_subscription_db(target_uid)
        bot.reply_to(m, f"✅ ইউজার `{target_uid}` এর সাবস্ক্রিপশন রিমুভ করা হয়েছে।", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(m, f"❌ Error: {e}")


def handle_add_admin_step(m):
    try:
        new_uid = int(m.text.strip())
        add_admin_db(new_uid)
        bot.reply_to(m, f"✅ `{new_uid}` কে নতুন Admin যুক্ত করা হয়েছে।", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(m, f"❌ Error: {e}")


def handle_create_voucher_step(m):
    try:
        parts = m.text.split()
        code, days = parts[0], int(parts[1])
        limit = int(parts[2]) if len(parts) > 2 else 15
        create_voucher_db(code, days, limit)
        bot.reply_to(m, f"🎟️ ভাউচার কোড `{code.upper()}` (`{days}` দিন, Limit `{limit}`) তৈরি হয়েছে!", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(m, f"❌ Error: {e}")


def handle_ban_step(m):
    try:
        target_uid = int(m.text.strip())
        banned_users.add(target_uid)
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)", (target_uid,))
            conn.commit()
            conn.close()
        bot.reply_to(m, f"✅ ইউজার `{target_uid}` ব্যান করা হয়েছে।", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(m, f"❌ Error: {e}")


def handle_unban_step(m):
    try:
        target_uid = int(m.text.strip())
        banned_users.discard(target_uid)
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute("DELETE FROM banned_users WHERE user_id = ?", (target_uid,))
            conn.commit()
            conn.close()
        bot.reply_to(m, f"✅ ইউজার `{target_uid}` আনব্যান করা হয়েছে।", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(m, f"❌ Error: {e}")


def process_txid(message, pid):
    tx = message.text.strip()
    uid = message.from_user.id
    plan = get_plan_by_id(pid)
    if not plan:
        bot.reply_to(message, "❌ প্ল্যানটি পাওয়া যায়নি।")
        return
    if is_txid_used(tx):
        bot.reply_to(message, "❌ এই TxID টি ইতিপূর্বেই ব্যবহার করা হয়েছে!")
        return
    valid, _, _ = check_binance_payment(tx)
    nums = re.findall(r"\d+", str(plan[3]))
    is_free = (float(nums[0]) == 0) if nums else False
    if valid or is_free:
        add_used_txid(tx)
        save_subscription(uid, plan[1], datetime.now() + timedelta(days=plan[4]))
        bot.reply_to(message, f"🎉 Payment Verified! `{plan[1]}` সচল হয়েছে।", parse_mode="Markdown")
    else:
        bot.reply_to(message, "❌ Binance Pay হিস্টোরিতে এই TxID পাওয়া যায়নি।")


def process_broadcast(message):
    success, fail = 0, 0
    status_msg = bot.reply_to(message, "📢 ব্রডকাস্ট পাঠানো হচ্ছে...")
    for target_uid in list(active_users):
        try:
            bot.copy_message(target_uid, message.chat.id, message.message_id)
            success += 1
            time.sleep(0.03)
        except Exception:
            fail += 1
    bot.edit_message_text(f"✅ Broadcast Done!\nSuccess: `{success}` | Failed: `{fail}`", message.chat.id, status_msg.message_id, parse_mode="Markdown")


BUTTON_MAPPING = {
    "📢 𝗨𝗽𝗱𝗮𝘁𝗲𝘀 𝗖𝗵𝗮𝗻𝗻𝗲𝗹": lambda m: bot.reply_to(m, f"📢 Channel: https://t.me/{UPDATE_CHANNEL}"),
    "⚡ 𝗦𝗽𝗲𝗲𝗱 & 𝗣𝗶𝗻𝗴": lambda m: bot.reply_to(m, f"⚡ CPU: `{psutil.cpu_percent()}%` | RAM: `{psutil.virtual_memory().percent}%`", parse_mode="Markdown"),
    "🚀 𝗨𝗽𝗹𝗼𝗮𝗱 𝗕𝗼𝘁": _logic_upload_prompt,
    "📁 𝗠𝗮𝗻𝗮𝗴𝗲 𝗕𝗼𝘁𝘀": _logic_check_files,
    "💳 𝗩𝗶𝗲𝘄 𝗣𝗹𝗮𝗻𝘀": _logic_view_plans,
    "🎁 𝗘𝗮𝗿𝗻 𝗩𝗜𝗣": _logic_earn_vip,
    "🎟️ 𝗥𝗲𝗱𝗲𝗲𝗺 𝗖𝗼𝗱𝗲": _logic_redeem_prompt,
    "🔗 𝗥𝗲𝗳𝗲𝗿𝗿𝗮𝗹": _logic_referral,
    "👤 𝗠𝘆 𝗣𝗿𝗼𝗳𝗶𝗹𝗲": _logic_my_profile,
    "💬 𝗦𝘂𝗽𝗽𝗼𝗿𝘁": lambda m: bot.reply_to(m, f"💬 Support: {YOUR_USERNAME}"),
    "👑 𝗖𝗼𝗻𝘁𝗮𝗰𝘁 𝗢𝘄𝗻𝗲𝗿": lambda m: bot.reply_to(m, f"👑 Owner: {YOUR_USERNAME}"),
    "🛡️ 𝗔𝗱𝗺𝗶𝗻 𝗣𝗮𝗻𝗲𝗹": lambda m: bot.reply_to(
        m,
        f"🛡️ **ADMIN CONTROL CONSOLE**\n🛠️ Maintenance Mode: `{'ON' if maintenance_mode else 'OFF'}`",
        reply_markup=admin_panel_markup(),
        parse_mode="Markdown",
    ) if m.from_user.id in admin_ids else None,
    "📊 𝗦𝘆𝘀𝘁𝗲𝗺 𝗦𝘁𝗮𝘁𝘀": lambda m: bot.reply_to(
        m,
        f"📊 Active Users: `{len(active_users)}` | Running Bots: `{len(bot_scripts)}` | Maint: `{'ON' if maintenance_mode else 'OFF'}`",
        parse_mode="Markdown",
    ) if m.from_user.id in admin_ids else None,
}


@bot.message_handler(func=lambda m: not is_anti_flood(m.from_user.id) and m.text in BUTTON_MAPPING and BUTTON_MAPPING[m.text] is not None)
def main_btns(m):
    uid = m.from_user.id
    blocked, reason = is_blocked_for_user(uid)
    if blocked:
        bot.reply_to(m, reason)
        return
    if not check_force_sub(uid):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📢 Join Official Channel", url=f"https://t.me/{UPDATE_CHANNEL}"))
        markup.add(types.InlineKeyboardButton("🔄 Verify Membership", callback_data="check_join"))
        bot.send_message(m.chat.id, "⚠️ আগে চ্যানেলে জয়েন করে Verify Membership চাপুন!", reply_markup=markup)
        return
    BUTTON_MAPPING[m.text](m)


if __name__ == "__main__":
    logger.info("🤖 HOSTIFY Bot Started...")
    keep_alive()
    Thread(target=auto_crash_recovery_loop, daemon=True).start()
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
