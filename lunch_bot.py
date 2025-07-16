"""
LunchPicker LINE Bot – MVP Skeleton (Updated v0.3)

Changelog v0.3
--------------
* **Fixed duplicate / corrupted `reply_best` definition** – now single, correct unpack order.
* `reply_best` shows `(name, rating⭐, address)` with proper newlines.
* Minor docstring tweaks.

Prerequisites
-------------
$ pip install flask line-bot-sdk==2.* requests apscheduler python-dotenv

Environment variables required:
    GOOGLE_API_KEY             # Google Places / Geocoding
    LINE_CHANNEL_SECRET        # LINE Bot channel secret
    LINE_CHANNEL_ACCESS_TOKEN  # LINE Bot channel access token

Optional env vars:
    USER_ID_ADMIN              # LINE user ID for push
    FALLBACK_LAT / FALLBACK_LNG

Run locally:
$ ngrok http 8000
$ python lunch_bot.py
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Tuple

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, abort, request

# --- LINE BOT SDK (v2) -------------------------------------------------------
try:
    from linebot import LineBotApi, WebhookHandler
    from linebot.exceptions import InvalidSignatureError
    from linebot.models import MessageEvent, TextMessage, TextSendMessage
except ImportError:
    raise RuntimeError("Please install line-bot-sdk==2.* for this sample.")

from collections import defaultdict
from linebot.models import QuickReply, QuickReplyButton, MessageAction

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 簡易記憶體 session（程式重啟會清空）
user_session: defaultdict[str, dict] = defaultdict(dict)
budget_map = {"$": 1, "$$": 2, "$$$": 3}


# --------------------------- Config -----------------------------------------
DB_PATH = Path("lunch.db")
RADIUS_METERS = 700  # ≈8‑minute walk
COMPANY_PLUS_CODE = "5JJ8+QQ 福和里 台中市西屯區"

GOOGLE_KEY = os.getenv("GOOGLE_API_KEY")
LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
ADMIN_USER_ID = os.getenv("USER_ID_ADMIN")

FALLBACK_LAT = os.getenv("FALLBACK_LAT")
FALLBACK_LNG = os.getenv("FALLBACK_LNG")

if not all([GOOGLE_KEY, LINE_SECRET, LINE_TOKEN]):
    raise RuntimeError("Missing GOOGLE_API_KEY / LINE creds in environment.")



# -------------------- Flask / LINE init -------------------------------------
app = Flask(__name__)
line_bot_api = LineBotApi(LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)

scheduler = BackgroundScheduler()
scheduler.start()

# --------------------------- DB ---------------------------------------------

def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS places (
                   place_id TEXT PRIMARY KEY,
                   name TEXT,
                   address TEXT,
                   lat REAL,
                   lng REAL,
                   price_level INTEGER,
                   rating REAL,
                   user_ratings_total INTEGER,
                   types TEXT,
                   first_seen TEXT,
                   last_seen TEXT
               )"""
        )
        conn.commit()

# ---------------------- Google API helpers ----------------------------------
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"

def _safe_get(url: str, **params) -> dict[str, Any]:
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()

def geocode_plus_code(plus_code: str) -> Tuple[float, float]:
    for q in (plus_code, f"{plus_code}, Taichung, Taiwan"):
        data = _safe_get(GEOCODE_URL, address=q, key=GOOGLE_KEY, language="zh-TW")
        if data.get("status") == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
    if FALLBACK_LAT and FALLBACK_LNG:
        logging.warning("Using fallback coordinates.")
        return float(FALLBACK_LAT), float(FALLBACK_LNG)
    raise RuntimeError("Geocoding failed and no fallback coordinates provided.")

def fetch_places(lat: float, lng: float) -> List[dict[str, Any]]:
    params = {
        "key": GOOGLE_KEY,
        "location": f"{lat},{lng}",
        "radius": RADIUS_METERS,
        "type": "restaurant|food",
        "language": "zh-TW",
    }
    results: List[dict[str, Any]] = []
    while True:
        payload = _safe_get(PLACES_URL, **params)
        status = payload.get("status")
        if status not in {"OK", "ZERO_RESULTS"}:
            raise RuntimeError(f"Places API error: {status} – {payload.get('error_message')}")
        results.extend(payload.get("results", []))
        token = payload.get("next_page_token")
        if token:
            params = {"pagetoken": token, "key": GOOGLE_KEY}
            time.sleep(2)
        else:
            break
    logging.info("Fetched %d places from Google.", len(results))
    return results

# ---------------------- Data persistence ------------------------------------

def upsert_places(places: List[dict[str, Any]]) -> List[str]:
    now = datetime.utcnow().isoformat()
    new_names: List[str] = []
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        for p in places:
            data = (
                p["place_id"], p["name"], p.get("vicinity"),
                p["geometry"]["location"]["lat"], p["geometry"]["location"]["lng"],
                p.get("price_level"), p.get("rating"), p.get("user_ratings_total"),
                ",".join(p.get("types", [])),
            )
            try:
                cur.execute(
                    """INSERT INTO places
                       (place_id,name,address,lat,lng,price_level,rating,user_ratings_total,types,first_seen,last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (*data, now, now),
                )
                new_names.append(p["name"])
            except sqlite3.IntegrityError:
                cur.execute("UPDATE places SET last_seen=? WHERE place_id=?", (now, p["place_id"]))
        conn.commit()
    return new_names

# ---------------------- Scheduler job ---------------------------------------

def daily_refresh() -> None:
    try:
        lat, lng = geocode_plus_code(COMPANY_PLUS_CODE)
        places = fetch_places(lat, lng)
        new_names = upsert_places(places)
    except Exception as exc:
        logging.error("Refresh failed: %s", exc)
        return

    if new_names:
        msg = "🎉 新增店家！\n" + "\n".join(new_names)
        if ADMIN_USER_ID:
            line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(text=msg))
        logging.info(msg)
    else:
        logging.info("No new restaurants today.")

scheduler.add_job(daily_refresh, "cron", hour=10, minute=0, id="daily_refresh")

# -------------------- LINE webhook handlers ---------------------------------

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@app.route("/", methods=["GET", "POST"])
def index():
    # Health check endpoint; avoids 404 spam from probes or old webhook URLs
    logging.debug(f"Root hit: headers={dict(request.headers)}")
    return "OK", 200


@handler.add(MessageEvent, message=TextMessage)
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event: MessageEvent):
    user_id = event.source.user_id
    text = event.message.text.strip()

    # --- A. 啟動流程 ---
    if text in {"午餐", "午餐?", "午餐？"}:
        # <第一階段> 只給「類型」選擇
        q_category = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label=l, text=f"類型:{l}"))
            for l in ("飯", "麵", "咖啡", "不限")
        ])
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="想吃什麼？", quick_reply=q_category)
        )
        return

    # --- B. 使用者選了類型 ---
    if text.startswith("類型:"):
        category = text.split(":", 1)[1]
        user_session[user_id]["category"] = category

        # 接著詢問預算
        q_budget = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label=l, text=f"預算:{l}"))
            for l in ("$", "$$", "$$$")
        ])
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"已選「{category}」，預算多少？", quick_reply=q_budget)
        )
        return

    # --- C. 使用者選了預算 ---
    if text.startswith("預算:"):
        budget = text.split(":", 1)[1]
        user_session[user_id]["budget"] = budget

        # 兩欄都齊全 → 立即推薦
        reply_best(event)
        return

    # --- D. 仍支援舊指令 ---
    if text.startswith("搜尋 "):
        keyword = text[3:].strip()
        reply_best(event, keyword=keyword)
    elif text == "找午餐":
        reply_best(event)
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="輸入『午餐』開始選，或『搜尋 關鍵字』直接找！")
        )
# ------------------ Query / Reply helpers -----------------------------------

def query_places(keyword: str | None = None,
                 category: str | None = None,
                 price_max: int | None = None):
    sql = "SELECT name, rating, address FROM places"
    cond, params = [], []

    # 關鍵字
    if keyword:
        cond.append("(name LIKE ? OR address LIKE ?)")
        params += [f"%{keyword}%"] * 2

    # 類型（中文關鍵字）
    if category and category != "不限":
        cond.append("(name LIKE ? OR address LIKE ?)")
        params += [f"%{category}%"] * 2

    # 預算
    if price_max:
        cond.append("price_level<=?")
        params.append(price_max)

    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY rating DESC NULLS LAST, user_ratings_total DESC LIMIT 5"

    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(sql, params).fetchall()

def reply_best(event: MessageEvent, keyword: str | None = None):
    user_id = event.source.user_id
    sess = user_session.get(user_id, {})
    category = sess.get("category")
    budget   = sess.get("budget")
    price_max = budget_map.get(budget) if budget else None

    rows = query_places(keyword, category, price_max)

    # 查完就清掉 session，避免下次殘留
    user_session.pop(user_id, None)

    if not rows:
        msg = "找不到符合條件的餐廳 🥲"
    else:
        msg = "\n\n".join(
            f"{name} ({rating if rating else 'N/A'}⭐)\n{addr}"
            for name, rating, addr in rows
        )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

# --------------------------- Main -------------------------------------------

if __name__ == "__main__":
    init_db()
    try:
        daily_refresh()
    except Exception as exc:
        logging.warning("First refresh skipped: %s", exc)

    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
