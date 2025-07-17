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
import copy
from datetime import datetime, timedelta
from urllib.parse import quote_plus

# --- LINE BOT SDK -------------------------------------------------------
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.models import FlexSendMessage, CarouselContainer, BubbleContainer, PostbackEvent

from collections import defaultdict
from linebot.models import QuickReply, QuickReplyButton, MessageAction

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

# 簡易記憶體 session（程式重啟會清空）
user_session: defaultdict[str, dict] = defaultdict(dict)
TTL = timedelta(minutes=10)


budget_map = {"$": 1, "$$": 2, "$$$": 3}

# 中文餐廳類型 → Google Places `types` 對映
category_map = {
    "飯": "restaurant",          # 泛指有飯類主食
    "麵": "meal_takeaway",       # 便當/麵食
    "咖啡": "cafe",
    "小吃": "street_food",
    "便當": "meal_takeaway",
    "不限": None,                # 不設定過濾
}


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
                open_now INTEGER,
                opening_hours TEXT,
                photo_ref TEXT,
                first_seen TEXT,
                last_seen TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                place_id TEXT,
                chosen_at TEXT
            )"""
        )
        conn.commit()

# ---------------------- Google API helpers ----------------------------------
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"

# 針對多個餐飲相關類型輪詢，避免單次 API 只回 restaurant 導致遺漏
TYPES_OF_INTEREST = [
    "restaurant",
    "meal_takeaway",
    "cafe",
    "street_food",
]

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
    """
    Fetch places within radius for all TYPES_OF_INTEREST, handling up to 3 pages
    per type. Deduplicate by place_id so the same店家不會重複。
    """
    seen: dict[str, dict[str, Any]] = {}
    base_params = {
        "key": GOOGLE_KEY,
        "location": f"{lat},{lng}",
        "radius": RADIUS_METERS,
        "language": "zh-TW",
    }

    for t in TYPES_OF_INTEREST:
        params = base_params | {"type": t}
        page = 1
        while True:
            payload = _safe_get(PLACES_URL, **params)
            status = payload.get("status")
            if status not in {"OK", "ZERO_RESULTS"}:
                raise RuntimeError(f"Places API error: {status} – {payload.get('error_message')}")

            for place in payload.get("results", []):
                # keep only places that match at least one food-related type
                if any(tt in TYPES_OF_INTEREST for tt in place.get("types", [])):
                    seen.setdefault(place["place_id"], place)
                else:
                    logging.debug("Skip non-food place: %s (%s)",
                                  place.get("name"), place.get("types"))

            token = payload.get("next_page_token")
            if token and page < 3:  # Google API 最多 3 頁
                params = {"pagetoken": token, "key": GOOGLE_KEY}
                page += 1
                time.sleep(2)      # token 需要 2s 才可用
            else:
                break
        logging.info("Type %-15s ⇒ %3d results (page %d)", t, len(seen), page)

    logging.info("Fetched %d unique places (all types).", len(seen))
    return list(seen.values())

# ---------------------- Data persistence ------------------------------------

def upsert_places(places: List[dict[str, Any]]) -> List[str]:
    now = datetime.utcnow().isoformat()
    new_names: List[str] = []
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        for p in places:
            opening = p.get("opening_hours", {})
            open_now   = opening.get("open_now")           # bool
            weekday    = opening.get("weekday_text")       # list
            opening_txt = "; ".join(weekday) if weekday else None

            photo_ref = None
            if "photos" in p and p["photos"]:
                photo_ref = p["photos"][0]["photo_reference"]

            data = (
                p["place_id"],
                p["name"],
                p.get("vicinity"),
                p["geometry"]["location"]["lat"],
                p["geometry"]["location"]["lng"],
                p.get("price_level"),
                p.get("rating"),
                p.get("user_ratings_total"),
                ",".join(p.get("types", [])),
                int(open_now) if open_now is not None else None,
                opening_txt,
                photo_ref,
            )
            try:
                cur.execute(
                    """INSERT INTO places
                       (place_id,name,address,lat,lng,price_level,rating,
                        user_ratings_total,types,open_now,opening_hours,photo_ref,
                        first_seen,last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (*data, now, now),
                )
                new_names.append(p["name"])
            except sqlite3.IntegrityError:
                cur.execute(
                    """UPDATE places SET
                       last_seen=?, open_now=?, opening_hours=?, photo_ref=?
                       WHERE place_id=?""",
                    (now, int(open_now) if open_now is not None else None,
                     opening_txt, photo_ref, p["place_id"])
                )
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

# -------------------- LINE build_bubble --------------------------------- 
# ---------- Star icon URLs ----------
GOLD_STAR = "https://developers-resource.landpress.line.me/fx/img/review_gold_star_28.png"
GRAY_STAR = "https://developers-resource.landpress.line.me/fx/img/review_gray_star_28.png"
PLACEHOLDER_URL = "https://raw.githubusercontent.com/cleo-tspy/lunch-picker/main/static/lunch_placeholder.jpg"
# ---------- Base Bubble template ----------
BASE_BUBBLE = {
    "type": "bubble",
    "hero": {
        "type": "image",
        # 會在 build_bubble 時覆寫
        "url": PLACEHOLDER_URL,
        "size": "full",
        "aspectRatio": "20:13",
        "aspectMode": "cover"
    },
    "body": {
        "type": "box",
        "layout": "vertical",
        "contents": [
            {  # 店名
                "type": "text",
                "text": "店名",
                "weight": "bold",
                "size": "xl"
            },
            {  # 星星列 & 評分
                "type": "box",
                "layout": "baseline",
                "margin": "md",
                "contents": [
                    # 五顆 star icon，稍後依評分改 URL
                    *(
                        {"type": "icon", "size": "sm", "url": GOLD_STAR}
                        for _ in range(5)
                    ),
                    {
                        "type": "text",
                        "text": "★ 4.8",
                        "size": "sm",
                        "color": "#999999",
                        "margin": "md",
                        "flex": 0
                    }
                ]
            },
            {  # 地址
                "type": "box",
                "layout": "baseline",
                "margin": "lg",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": "地址", "color": "#aaaaaa", "size": "sm", "flex": 1},
                    {"type": "text", "text": "台中市西屯區...", "wrap": True, "color": "#666666", "size": "sm", "flex": 5}
                ]
            },
            {  # 營業狀態
                "type": "box",
                "layout": "baseline",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": "營業", "color": "#aaaaaa", "size": "sm", "flex": 1},
                    {"type": "text", "text": "營業中 11:00–22:00", "wrap": True, "color": "#666666", "size": "sm", "flex": 5}
                ]
            }
        ]
    },
    "footer": {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "contents": [
            {
                "type": "button",
                "style": "primary",
                "height": "sm",
                "action": {
                    "type": "uri",
                    "label": "GOOGLE MAP",
                    "uri": "https://www.google.com/maps"
                }
            },
            {
                "type": "button",
                "style": "secondary",
                "height": "sm",
                "action": {
                    "type": "postback",
                    "label": "就吃這家",
                    "data": "CHOSEN_PLACE_ID"
                }
            }
        ],
        "flex": 0
    }
}

def build_bubble(
    place_id: str,
    name: str,
    rating: float | None,
    address: str,
    lat: float,
    lng: float,
    open_now: bool | None = None,
    opening_hours: str | None = None,
    photo_url: str | None = None,
):
    """
    Return Flex BubbleContainer with dynamic data.
    - open_now: True/False/None  → 營業中 / 已打烊 / 未提供
    - opening_hours: e.g. '11:00–22:00'
    """

    bubble = copy.deepcopy(BASE_BUBBLE)

    # 1. 圖片
    bubble["hero"]["url"] = photo_url or PLACEHOLDER_URL

    # 2. 店名
    bubble["body"]["contents"][0]["text"] = name

    # 3. 星星 icon + 評分文字
    gold = min(int(round(rating or 0)), 5)
    for i in range(5):
        icon_url = GOLD_STAR if i < gold else GRAY_STAR
        bubble["body"]["contents"][1]["contents"][i]["url"] = icon_url
    bubble["body"]["contents"][1]["contents"][-1]["text"] = f"★ {rating:.1f}" if rating else "★ N/A"

    # 4. 地址
    bubble["body"]["contents"][2]["contents"][1]["text"] = address

    # 5. 營業狀態 & 時間
    status_text = "未提供"
    if open_now is True:
        status_text = "營業中"
    elif open_now is False:
        status_text = "已打烊"
    if opening_hours:
        status_text += f" {opening_hours}"
    bubble["body"]["contents"][3]["contents"][1]["text"] = status_text

    # 6. Google Maps 導航
    maps_uri = (
                "https://www.google.com/maps/search/?api=1"
                f"&query={quote_plus(name)}"
                f"&query_place_id={place_id}"
            )
    bubble["footer"]["contents"][0]["action"]["uri"] = maps_uri

    # Replace placeholder postback data in embedded button
    bubble["footer"]["contents"][1]["action"]["data"] = f"chosen:{place_id}"

    return BubbleContainer.new_from_json_dict(bubble)

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


def purge_expired_sessions():
    now = datetime.utcnow()
    for uid in list(user_session.keys()):
        if now - user_session[uid].get("ts", now) > TTL:
            user_session.pop(uid, None)

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event: MessageEvent):
    purge_expired_sessions()
    user_id = event.source.user_id
    text = event.message.text.strip()
    logging.debug("USER_ID=%s", event.source.user_id)
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
        zh_cat = text.split(":", 1)[1]
        type_key = category_map.get(zh_cat)
        user_session[user_id].update(
            {"category": zh_cat, "type_key": type_key, "ts": datetime.utcnow()}
        )


        # 接著詢問預算
        q_budget = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label=l, text=f"預算:{l}"))
            for l in ("$", "$$", "$$$")
        ])
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"已選「{zh_cat}」，預算多少？", quick_reply=q_budget)
        )
        return

    # --- C. 使用者選了預算 ---
    if text.startswith("預算:"):
        budget = text.split(":", 1)[1]
        user_session[user_id].update({"budget": budget, "ts": datetime.utcnow()})

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


# --- 處理 PostbackEvent，寫入 user_history ---
@handler.add(PostbackEvent)
def handle_postback(event: PostbackEvent):
    data = event.postback.data
    user_id = event.source.user_id
    if data.startswith("chosen:"):
        place_id = data.split(":", 1)[1]
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            # Check whether today already has a record for this user
            cur.execute(
                "SELECT place_id FROM user_history "
                "WHERE user_id=? AND date(chosen_at)=date('now','localtime')",
                (user_id,)
            )
            row = cur.fetchone()
            if row and row[0] == place_id:
                # Same place already recorded today; ignore duplicate
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="記錄過了！")
                )
                return
            # Otherwise, replace today's previous choice (if any) with the new one
            cur.execute(
                "DELETE FROM user_history WHERE user_id=? AND date(chosen_at)=date('now','localtime')",
                (user_id,)
            )
            cur.execute(
                "INSERT INTO user_history (user_id, place_id, chosen_at) VALUES (?,?,?)",
                (user_id, place_id, datetime.utcnow().isoformat())
            )
            conn.commit()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="已記錄！祝用餐愉快 😋")
        )
        return

def query_places(keyword: str | None = None,
                 zh_category: str | None = None,
                 price_max: int | None = None,
                 exclude_ids: set[str] | None = None,
                 type_key: str | None = None):
    sql = """SELECT place_id, name, rating, address, lat, lng, open_now, opening_hours, photo_ref
             FROM places"""
    cond, params = [], []

    # 關鍵字
    if keyword:
        cond.append("(name LIKE ? OR address LIKE ?)")
        params += [f"%{keyword}%"] * 2

    # 類型過濾：若有 type_key (英文) 則用 types LIKE，否則退回中文關鍵字比對
    if type_key:
        cond.append("types LIKE ?")
        params.append(f"%{type_key}%")
    elif zh_category and zh_category != "不限":
        cond.append("(name LIKE ? OR address LIKE ?)")
        params += [f"%{zh_category}%"] * 2

    # 預算
    if price_max:
        cond.append("price_level<=?")
        params.append(price_max)

    if exclude_ids:
        placeholders = ",".join("?" for _ in exclude_ids)
        cond.append(f"place_id NOT IN ({placeholders})")
        params.extend(exclude_ids)

    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY rating DESC NULLS LAST, user_ratings_total DESC LIMIT 5"

    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(sql, params).fetchall()

# Helper: fetch recent choices
def recent_place_ids(user_id: str, days: int = 3) -> set[str]:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    sql = "SELECT place_id FROM user_history WHERE user_id=? AND chosen_at>=?"
    with sqlite3.connect(DB_PATH) as conn:
        return {row[0] for row in conn.execute(sql, (user_id, cutoff))}

def reply_best(event: MessageEvent, keyword: str | None = None):
    user_id = event.source.user_id
    exclude_ids = recent_place_ids(user_id)
    sess = user_session.get(user_id, {})
    category = sess.get("category")
    type_key = sess.get("type_key")
    budget   = sess.get("budget")
    price_max = budget_map.get(budget) if budget else None

    rows = query_places(keyword, category, price_max,
                        exclude_ids=exclude_ids, type_key=type_key)

    # 用完就清 session
    user_session.pop(user_id, None)

    if not rows:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="找不到符合條件的餐廳 🥲")
        )
        return

    # --- 1) 把每一筆資料轉成 Bubble ---
    bubbles: list[BubbleContainer] = []
    for row in rows:
        (place_id, name, rating, address,
        lat, lng,
        open_now, opening_hours, photo_ref) = row

        photo_url = (f"https://maps.googleapis.com/maps/api/place/photo"
                    f"?maxwidth=240&photoreference={photo_ref}&key={GOOGLE_KEY}"
                    if photo_ref else PLACEHOLDER_URL)

        # TODO: When user explicitly selects a restaurant, insert into user_history.

        bubbles.append(
            build_bubble(
                place_id=place_id,
                name=name,
                rating=rating,
                address=address,
                lat=lat,
                lng=lng,
                open_now=bool(open_now) if open_now is not None else None,
                opening_hours=opening_hours,
                photo_url=photo_url
            )
        )

    # --- 2) 組成 Carousel & 發送 Flex ---
    carousel = CarouselContainer(contents=bubbles)
    flex_msg = FlexSendMessage(alt_text="午餐推薦", contents=carousel)
    line_bot_api.reply_message(event.reply_token, flex_msg)

# --------------------------- Main -------------------------------------------

if __name__ == "__main__":
    init_db()
    try:
        daily_refresh()
    except Exception as exc:
        logging.warning("First refresh skipped: %s", exc)

    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
