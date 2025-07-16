逐步新增功能的建議節奏

| Sprint | 目標／功能 | 主要工作 & 里程碑 |
|--------|------------|------------------|
| **S0 稳定基線** | ✦ 把現有 `/callback` + 根目錄 `/` 路由穩定<br>✦ 每日排程可正常抓資料、推播 | • 加 health check<br>• **初始化資料庫**：建立 `places`（place_id）、`users`（LINE userId ↔ 使用者）、`user_history`（每日選擇）三張主表；確認 schema 不再變動 |
| **S1 對話式引導（基本）** | ✦ Quick Reply 詢問「想吃什麼？」「預算多少？」<br>✦ 解析自然語言：「我想吃辣的拉麵，200 內」 | • LINE 建兩層 Quick Reply／Postback<br>• 新增 `user_session` 暫存表存使用者本輪條件<br>• 從 `users` 取得偏好與備註並在詢問時帶出三日未重複的隨機推薦 |
| **S2 類別強化** | ✦ Places `types` 細分：`street_food`, `meal_takeaway`, `cafe`, `convenience_store`…<br>✦ 指令：「搜尋 便當」→ 自動對應 `meal_takeaway` | • 抓取時同步存 `types`<br>• `query_places()` 新增 `WHERE types LIKE ?` 條件 |
| **S3 回饋＋黑名單** | ✦ 推薦結果附 👍 / 👎 快捷<br>✦ 👍→ `favorites`；👎→ `blacklist`（可記錄原因） | • 新增 `feedback`, `favorites`, `blacklist` 表<br>• `reply_best()` 排序時為 favorites 加權、blacklist 過濾 |
| **S4 午餐足跡** | ✦ 自動記錄「最終確定店家」並可 `/history` 查詢<br>✦ 兩小時後主動訊息詢問「今天吃哪家？」 | • Webhook 接 `Postback: confirm_choice` 更新 `user_history`／`lunch_log`<br>• 使用 APScheduler 針對當天發過推薦的使用者排 2 h 後提醒 |
| **S5 Gemini AI 加值** | ✦ 1️⃣ 自然語言意圖解析（NLU）<br>✦ 2️⃣ 推薦理由生成 & metacognition | • 伺服器端呼叫 Gemini `gemini-pro`<br>• Prompt：輸入條件 + 歷史喜好 ➜ 產出排序 & 說明<br>• 每日自省：讀取 `feedback` 調整推薦策略 |

⸻

🏁 Sprint S0：穩定基線 & 初始化資料庫

目標＝「每天自動抓店家 ➜ LINE 推播新店」完整跑通

序號	待辦	說明 & LINE 端可見結果
0-1	確認根路徑 / 已回 200	已在 lunch_bot.py 加 index()，LINE Console Verify 應只剩 /callback。
0-2	建立 DB schema	新增 users 與 user_history 兩表（後續 Sprint 會用）：users(user_id PK, display_name TEXT, joined_at TIMESTAMP)user_history(id PK, user_id FK, date DATE, place_id TEXT, chosen_at TIMESTAMP)→ 跑 python init_db.py 或 Alembic migration。
0-3	註冊自己為第一個使用者	在 callback 收到任何訊息時，如果 user_id 不存在就 INSERT；可用 line_bot_api.get_profile() 取顯示名稱。LINE 端：第一次說話不影響體驗，你看不到變化，但 DB 會多一筆 users。
0-4	每日排程測試	手動呼叫 daily_refresh()；如有新店 ➜ Bot Push「🎉 新增店家…」。LINE 端：應收到 push 訊息，點擊 OK。
0-5	健康檢查 webhook	Render / Railway 可設 GET / 健康檢查；瀏覽器開 https://your-app.onrender.com/ 得到 OK。

S0 驗收腳本
	1.	docker-compose up 或本地 python lunch_bot.py + ngrok。
	2.	手機對 Bot 說「ping」→ 回覆正常。
	3.	人為刪除 places 中 2 筆後重新 daily_refresh() → LINE 收到「新增店家」推播。
	4.	伺服器重啟 & Render HealthCheck OK。

⸻

🚀 Sprint S1：對話式引導（Quick Reply x 自然語言）

1. 使用情境

使用者傳「午餐」→ Bot 先送 Quick Reply：
• 想吃什麼？（按鈕：米飯／麵食／咖啡／不挑）
• 預算？（$、$$、$$$）

若使用者直接丟自然語言「我想吃辣的韓式，200 內」，Bot 應自動解析出：

{"keyword":"韓式","price_max":200,"spicy":true}

並立即回傳推薦列表。

2. 技術拆解

步驟	動作	Code Hint
S1-1	Quick Reply 樣板	linebot.models.QuickReplyItem + MessageAction(text="類型:米飯")
S1-2	Session 暫存	新增 user_session dict (in-mem) 或 redis：{user_id: {"keyword":..., "price":...}}；有效期 15 min。
S1-3	NLU with RegEx fallback	① 先用簡單 RegEx 抓 (\d{2,3}) ?內、辣、韓式…② 後續可切換 Gemini NLU（S5）。
S1-4	組合查詢	把 session + NLU 結果餵進 query_places(price_max, keyword)
S1-5	LINE Flex 回覆	改用 Flex Bubble 列店名 / 星數 /「導航」按鈕："uri": "https://www.google.com/maps/search/?api=1&query=<lat>,<lng>"
S1-6	收尾 & 清 Session	使用者點店家後（或 2 h 無操作）del user_session[user_id]

3. 流程圖（簡）

User → "午餐"
        ↓
Bot → Quick Reply
        ↓
User → 點"麵食"  (Message Action)
        ↓
Bot → Quick Reply 詢問預算
        ↓
User → 點"$$"
        ↓
Bot → Flex 推薦列表 (reply_best)
        ↓
User → 👍 / 👎 (postback)


⸻

✨ Gemini 整合（先規劃，Sprint S5 開工）

功能	最簡 MVP	所需
NLU 意圖抽取	RegEx → Gemini functions 要求回 JSON	gemini-pro，prompt 模板
推薦理由生成	取前 3 家 + 使用者偏好 → Gemini 生成 50 字理由	temperature=0.7
Metacognition	讀 feedback 表 → Gemini 反思「推薦排序還可改進？」	批次 job（日結）


⸻
我會建議先做 「輸入有 Quick Reply，輸出用 Flex Message」 這一組，因為——

考量	Quick Reply + Flex Message	其他（DB 回饋、NLU、Gemini…）
使用者體感	一上線就能「按鈕選 → 漂亮卡片看餐廳」，介面煥然一新	多屬幕後功能，短期看不到差異
開發成本	只改前端 JSON，後端查詢邏輯幾乎不動	需調 schema、排程或串第三方 API
失敗風險	格式錯誤最多回 400，但不影響本體	Schema/排程若寫壞，整隻 bot 會斷
可迭代性	做好之後，再把 👍👎、NLU、Gemini 套進同一卡片或按鈕即可	先改底層，再回頭改 UI 會重工


⸻

建議的「第一輪」改善內容

模組	目標	具體做法	可見成果
Quick Reply	讓使用者免打字選「類型」「預算」	① 在使用者輸入「午餐」時回 2 行 Quick Reply：　• 類型：米飯／麵食／咖啡／不挑　• 預算：$／$$／$$$	使用者一點就送出 類型:麵食 ，流程更快
Flex Message 列表	把店家資訊排版成卡片	用 Flex Bubble：店名 + ⭐️ + 地址 + 「導航」按鈕；5 間用 Carousel	一眼看星等、地址；點「導航」直開 Google Maps
輕量 session	記得「上一個未填欄位」	比如只點了類型還沒選預算，就暫存到 user_session dict	對使用者是無感的，但避免多餘提問

這三件事都不需要改 DB schema；只要在現有 lunch_bot.py 加幾段 JSON 即可。完成後，你就能在 LINE 看見：
	1.	傳「午餐」→ 出現可點選按鈕
	2.	點完立即收到帶圖片/按鈕的推薦卡片

⸻

具體落地順序（約 1–2 晚就能跑通）
	1.	把 Quick Reply JSON 寫死在程式
	•	測試：輸入「午餐」收到按鈕
	2.	臨時 user_session dict（用 defaultdict(dict)）
	•	存 {user_id: {"category":"麵食"}}
	3.	Flex Bubble 模板（拿 LINE 官方 Playground 產生）
	•	先用假資料 preview → OK 再串 query_places() 資料
	4.	將 reply_best() 改 push Flex Carousel
	5.	Full flow 手動測：類型→預算→得結果
	6.	push 到雲端 / ngrok，與同事試用

⸻

等你完成 Quick Reply + Flex Message 之後，再來做：
	•	Sprint S2 的 types 細分（邏輯已用到類型）
	•	Sprint S3 的 👍👎/黑名單 —— 可直接掛在 Flex Bubble 的 Postback 按鈕
	•	Sprint S5 把 RegEx 意圖抽取換成 Gemini NLU

⸻

下一步

如果你決定照這路徑：
	1.	告訴我你要先動 Quick Reply 還是 Flex Message，
	2.	我能直接用 oboe.edit_file 幫你在 lunch_bot.py 插入範例程式片段，讓你 Copy-Paste 即測。

隨時回覆，咱們就開工！

## 🚦 進度追蹤（更新：2025‑07‑16）

| Sprint | 狀態 | 備註 |
|--------|------|------|
| **S0** | ✅ 已完成 | 根目錄 `/` Health check、每日排程推播、新增 `users` & `user_history` 表已落地 |
| **S1** | ⏳ 進行中 | Quick Reply 需求已確認，開始實作 `午餐` → 類型 / 預算 按鈕；Flex Message 尚未開始 |
| **S2** | 🔜 排程中 | 等 S1 完成後啟動 |
| **S3** | 🔜 排程中 | — |
| **S4** | 🔜 排程中 | — |
| **S5** | 🔜 規劃中 | Gemini NLU / 推薦理由 / Metacognition |