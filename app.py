import os, sqlite3, requests, json, time
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
HTTPS_URL = os.getenv("HTTPS_URL")  # e.g. https://your-domain.com/webhook
DEFAULT_LOCALE = os.getenv("DEFAULT_LOCALE", "vi_VN")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = Flask(__name__)
DB_PATH = os.getenv("DB_PATH", "bot.db")

# --------- DB helpers ---------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        locale TEXT,
        consent INTEGER DEFAULT 1,
        source TEXT,
        frequency TEXT DEFAULT 'normal',
        leagues TEXT,
        created_at INTEGER,
        updated_at INTEGER
    )""")
    conn.commit(); conn.close()

# 在首次请求前确保数据库已初始化（容器化/WSGI 下不会触发 __main__）
@app.before_first_request
def _init_db_once():
    try:
        init_db()
    except Exception as e:
        app.logger.error(f"DB init failed: {e}")

def upsert_user(chat_id, **kwargs):
    conn = db()
    now = int(time.time())
    # read
    cur = conn.execute("SELECT chat_id FROM users WHERE chat_id=?", (chat_id,))
    exists = cur.fetchone() is not None
    if exists:
        fields = []
        vals = []
        for k, v in kwargs.items():
            fields.append(f"{k}=?")
            vals.append(v)
        fields.append("updated_at=?"); vals.append(now)
        vals.append(chat_id)
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE chat_id=?", vals)
    else:
        cols = ["chat_id","created_at","updated_at"]
        vals = [chat_id, now, now]
        for k, v in kwargs.items():
            cols.append(k); vals.append(v)
        qs = ",".join(["?"]*len(vals))
        conn.execute(f"INSERT INTO users ({', '.join(cols)}) VALUES ({qs})", vals)
    conn.commit(); conn.close()

def delete_user(chat_id):
    conn = db()
    conn.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
    conn.commit(); conn.close()

# --------- Telegram helpers ---------
def tg(method, payload):
    try:
        r = requests.post(f"{TG_API}/{method}", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        app.logger.error(f"TG error: {e}")
        return None

def send_msg(chat_id, text, keyboard=None, disable_web_preview=True):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": disable_web_preview}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    return tg("sendMessage", payload)

def answer_cbq(cbq_id):
    return tg("answerCallbackQuery", {"callback_query_id": cbq_id})

# --------- i18n snippets (简化示例，按需扩展) ---------
def t(key, locale=DEFAULT_LOCALE):
    vn = {
        "welcome": ("Chào mừng! Bạn sẽ nhận được:\n"
                    "• 3 yếu tố trước trận\n• Nhiệt kế chấn thương\n• Biến động kèo (giáo dục)\n\n"
                    "[18+] Nội dung giáo dục · Có thể /stop bất cứ lúc nào."),
        "choose": "Chọn tuỳ chọn dưới đây:",
        "set_low": "Đã设置为低频推送。",
        "stopped": "Đã停止推送。/start 可重新开启。",
        "reply_league": "请回复想关注的联赛（例如：V.League, EPL）"
    }
    zh = {
        "welcome": ("欢迎！你将收到：\n"
                    "• 赛前三要素\n• 伤停温度计\n• 赔率变动（教育向）\n\n"
                    "[18+] 可随时 /stop 退订。"),
        "choose": "请选择：",
        "set_low": "已设置为低频推送。",
        "stopped": "已停止推送。可随时 /start 重新开启。",
        "reply_league": "请回复想关注的联赛（例如：中超, EPL）"
    }
    pack = vn if locale.startswith("vi") else zh
    return pack.get(key, key)

def welcome_keyboard(locale):
    return [
        [{"text": "Chọn giải đấu" if locale.startswith("vi") else "选择联赛",
          "callback_data": "pref_leagues"}],
        [{"text": "Giảm频率" if not locale.startswith("vi") else "Giảm tần suất",
          "callback_data": "pref_less"}],
        [{"text": "退订 /stop" if not locale.startswith("vi") else "Hủy đăng ký /stop",
          "callback_data": "pref_stop"}]
    ]

# --------- webhook ----------
@app.route("/webhook", methods=["POST"])
def webhook():
    upd = request.get_json(force=True, silent=True) or {}
    # 1) /start /stop 文本消息
    if "message" in upd:
        msg = upd["message"]
        chat = msg["chat"]; chat_id = chat["id"]
        text = msg.get("text", "") or ""
        user = msg.get("from", {})
        locale = user.get("language_code", DEFAULT_LOCALE) or DEFAULT_LOCALE

        if text.startswith("/start"):
            payload = ""
            parts = text.split(maxsplit=1)
            if len(parts) == 2: payload = parts[1].strip()[:64]
            upsert_user(
                chat_id,
                username=user.get("username"),
                first_name=user.get("first_name"),
                locale=locale,
                consent=1,
                source=payload
            )
            send_msg(chat_id, t("welcome", locale), keyboard=welcome_keyboard(locale))
            return "ok"

        if text.startswith("/stop"):
            delete_user(chat_id)
            send_msg(chat_id, t("stopped", locale))
            return "ok"

        # 处理用户回复联赛偏好（简化：直接把文本写入）
        if text and chat_id:
            conn = db()
            conn.execute("UPDATE users SET leagues=?, updated_at=? WHERE chat_id=?",
                         (text[:100], int(time.time()), chat_id))
            conn.commit(); conn.close()
            send_msg(chat_id, f"已记录偏好：{text}")
            return "ok"

    # 2) 按钮点击（callback_query）
    if "callback_query" in upd:
        cq = upd["callback_query"]
        chat_id = cq["message"]["chat"]["id"]
        data = cq.get("data", "")
        # 获取语言
        conn = db()
        cur = conn.execute("SELECT locale FROM users WHERE chat_id=?", (chat_id,))
        row = cur.fetchone(); conn.close()
        locale = row["locale"] if row and row["locale"] else DEFAULT_LOCALE

        if data == "pref_stop":
            delete_user(chat_id)
            send_msg(chat_id, t("stopped", locale))
        elif data == "pref_less":
            conn = db()
            conn.execute("UPDATE users SET frequency='low', updated_at=? WHERE chat_id=?",
                         (int(time.time()), chat_id))
            conn.commit(); conn.close()
            send_msg(chat_id, t("set_low", locale))
        elif data == "pref_leagues":
            send_msg(chat_id, t("reply_league", locale))
        answer_cbq(cq["id"])
        return "ok"

    return "ok"

# --------- 辅助路由 ----------
@app.get("/health")
def health():
    return jsonify(ok=True)

@app.post("/set_webhook")
def set_webhook():
    """一键把 webhook 指到 HTTPS_URL（也可用 curl 调 Telegram 的 setWebhook）"""
    if not HTTPS_URL:
        return jsonify(ok=False, error="Set HTTPS_URL in .env"), 400
    r = requests.post(f"{TG_API}/setWebhook", data={"url": HTTPS_URL}, timeout=10).json()
    return jsonify(r)

@app.post("/delete_webhook")
def delete_webhook():
    r = requests.post(f"{TG_API}/deleteWebhook", timeout=10).json()
    return jsonify(r)

# --------- 启动 ----------
if __name__ == "__main__":
    assert BOT_TOKEN, "请在 .env 里设置 BOT_TOKEN"
    init_db()
    # 本地开发：配合 ngrok 暴露外网，如 ngrok http 5000
    app.run(host="0.0.0.0", port=5000, debug=True)
