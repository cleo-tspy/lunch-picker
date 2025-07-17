# AI 午餐選擇器 Roadmap (v2)

> 最後更新：2025-07-16  
> 本文件分四層：**總覽 → Sprint 明細 → 進度追蹤 → 歷史紀錄**  
> 展開 / 收合 `<details>` 以保持檔案閱讀清爽

---

## 1. 全局藍圖

| Sprint | 主要目標 | 當前狀態 |
|--------|----------|----------|
| **S0** 穩定基線 | Health check、每日排程、核心資料表 | ✅ 結案 |
| **S1** 對話式引導 | Quick Reply → Flex 列表、Session 管理 | 🟡 進行中 |
| **S2** 類別強化 | `types` 細分、中文→英文對映 | 🔜 |
| **S3** 回饋機制 | 👍 / 👎、Favorites / Blacklist | 🔜 |
| **S4** 午餐足跡 | `/history` + 2h 自動提醒 | 🔜 |
| **S5** Gemini 加值 | NLU、推薦理由、Metacognition | 🔜 |

---

## 2. Sprint 明細

> 每個 Sprint 拆「子項／狀態／說明 & 使用者可見成果」

### 2-1．S0 穩定基線（✅ 已完成）

| 子項 | 狀態 | 說明 |
|------|------|------|
| `/` Health check | ✔ | `index()` 回 200，Render 探活 |
| 每日排程 + 推播 | ✔ | `daily_refresh()`；若新增店家 ➜ LINE Push |
| Database 初始化 | ✔ | `places`, `users`, `user_history` 三主表 |

<details>
<summary>驗收腳本</summary>

1. `python lunch_bot.py` + `ngrok`  
2. 手機對 Bot 說「ping」→ 回覆正常  
3. 刪除 `places` 2 筆 + 手動 `daily_refresh()` ➜ 收到「🎉 新增店家」推播  
4. Render HealthCheck `GET /` 回 `OK`
</details>

---

### 2-2．S1 對話式引導（🟡 進行中）

#### 子項工作表

| 編號 | 子項 | 狀態 | 使用者體驗 |
|------|------|------|------------|
| S1-1 | *Quick Reply*（類型／預算） | ✔ 完成 | 按鈕多步選擇 |
| S1-2 | *Session* 暫存 | ✔ 完成 | 記住類型、預算直到查詢 |
| S1-3 | 篩選查詢串接 | ✔ 完成 | `query_places()` 傳入類型/price |
| S1-4 | **Flex Message Bubble/Carousel** | ⏳ 進行中 | 美觀卡片 + 「導航」按鈕 |
| S1-5 | Session TTL 10 min | 🔜 | 逾時自動清除 |
| S1-6 | README GIF Demo | 🔜 | 示範「午餐 → 類型 → 預算 → Flex 列表」|
| S1-7 | 偏好帶入 + 去重 | 🔜 | 讀取 `users` 備註，推薦時避開三日內已選餐廳 |

#### 近期目標（D-1：2025-07-17）

1. 完成 Flex Bubble 樣板（5 筆 Carousel）  
2. `reply_best()` 切換為 Flex 回覆  
3. 拍攝 GIF 插入 README

---

### 2-3．S2 類別強化（🔜 排程中）

| 子項 | 描述 |
|------|------|
| 中文→英文對映 | `"麵"→"meal_takeaway"`, `"咖啡"→"cafe"` … |
| 抓取 `types` 存表 | DB 加欄 `place_types` |
| 搜尋指令「便當」 | 自動套用對映後過濾 |

---

### 2-4．S3 回饋機制（🔜）

| 子項 | 描述 |
|------|------|
| Flex 卡片加 👍 / 👎 | Postback 回傳 `like` / `dislike` |
| `favorites`, `blacklist` 表 | 排序優先 or 過濾 |
| 推薦權重調整 | favorite +2 分，blacklist 排除 |

---

### 2-5．S4 午餐足跡（🔜）

| 子項 | 描述 |
|------|------|
| `lunch_log` 表 | 記錄最終選擇 |
| `/history` 查詢 | 本週去哪吃 |
| 2 h 後提醒 | APScheduler 個別推「今天吃哪家？」 |

---

### 2-6．S5 Gemini AI 加值（🔜）

| 子項 | 描述 |
|------|------|
| NLU 意圖抽取 | Gemini functions → `{"keyword":"韓式", "price":200}` |
| 推薦理由生成 | 在 Flex 卡片加入「推薦理由」 |
| Metacognition | 每日批次：讀取 `feedback` → 調整排序策略 |

---

## 3. 進度追蹤

| 日期 | 動作 | 備註 |
|------|------|------|
| 2025-07-16 | Quick Reply + 篩選上線 | S1-1 〜 S1-3 完成 |
| 2025-07-16 | Roadmap 改版 v2 | 新結構 / 詳述 |

---

## 4. 里程碑歷史

<details>
<summary>點此展開</summary>

| 日期 | 事件 |
|------|------|
| 2025-07-16 | S1 啟動，確立 Quick Reply & Flex 路徑 |
| 2025-07-16 | Quick Reply & 篩選完成 |
</details>
