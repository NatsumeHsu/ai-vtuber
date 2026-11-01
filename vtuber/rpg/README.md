# Twitch／Discord 共用 Idleology RPG

玩家玩法、賺錢方式與完整指令請看 [RPG 玩家指南](玩家指南.md)。

已以 Idleology 遊戲程式取代原本固定每分鐘發金幣的原型。
上游角色、裝備、採集、夥伴、聚落、戰鬥與進階玩法位於 `idleology/`；
既有 Discord Bot 載入 33 個遊戲 Cog，不啟動另一個 Bot。
共用服務不依賴 LLM、ASR 或 TTS，未啟動 AI 也能玩。

## 設定與啟動

使用原本 `start.bat`／`vtuberAI.py` 啟動 Discord 主服務。
`rpg` 未設定時使用以下預設值，可加入本機 `setting.yml`：

```yaml
rpg:
  enabled: true
  database: data/rpg.sqlite3
  link_ttl_seconds: 300
```

路徑以專案根目錄解析，世界固定為 `discord.guild_id`。同一存檔不可改用另一個伺服器 ID。
設定重新載入時會停止遊戲工作、關閉 SQLite，再以新設定載入。
請勿同時啟動另一份程式操作同一 RPG 存檔。
依賴已加入 `requirements.txt`：`aiosqlite==0.22.1`。

## Discord

- `/rpg help`：共用指令說明。
- `/rpg register name`：快速建角，使用上游起始流程與 200 金幣。
- `/rpg profile`、`/rpg inventory`：角色與背包摘要。
- `/rpg combat`：一般遭遇自動戰鬥，最多 600 回合。
- `/rpg package [option]`：查看升級配點，或選擇 1／2／3。
- `/rpg link`：私密取得一次性的 Twitch 綁定碼。

上游原生互動玩法另有 `/register`（完整外觀／信念註冊）、`/card`、`/stats`、
`/combat`、`/inventory`、`/journey`、`/gather`、`/partner`、`/settlement`、
`/rest`、`/checkin` 等，均使用同一角色與資料庫。
指令限主伺服器；Bot 管理／擁有者指令不載入，`rite_debug` 不發布。
`setup_events` 另有管理員檢查；上游隨機事件排程維持停用。
共用 `/rpg` 回覆為繁體中文且僅自己可見。遊戲訊息、按鈕、選單、輸入視窗、
新手引導、NPC 台詞、夥伴劇情與裝備名稱均套用繁中語系；
Discord 使用繁中介面時，遊戲指令選單也會顯示中文名稱與說明。
原始英文指令名稱仍保留，所以本文件的 `/combat` 等寫法也能使用。

## 繁體中文語系

翻譯在顯示時套用，既有角色、物品名稱、能力數值與資料庫格式不會改寫。
玩家自訂名稱、信念、網址、Discord 識別碼與選單值保持原樣。
遊戲時只讀取本機語系檔，不會連線呼叫翻譯服務，也不會將玩家資料送去翻譯。

`locales/zh_TW.json` 是基礎翻譯，`editorial.json` 是優先採用的潤稿版本。
新手引導、295 句 NPC 台詞、28 段夥伴劇情與裝備詞綴另有人工整理的繁中檔案。
用語採自然的冒險遊戲口吻，操作條件、消耗與機率則維持明確。

調整文案後，執行 `.venv\Scripts\python.exe -X utf8 scripts\apply_rpg_editorial.py`
更新潤稿對照；重新啟動主服務後載入。`scripts/build_rpg_locale.py` 僅供開發時
新增公開上游文字的翻譯底稿使用，不是啟動或遊戲的一部分。

## Twitch

沿用既有 EventSub 授權與回覆通道：

```text
!rpg help
!rpg register 白璃勇者
!rpg profile
!rpg inventory
!rpg combat
!rpg package
!rpg package 1
!rpg link 驗證碼
```

前綴沿用 `twitch.prefix`。指令透過程序通訊送至 Discord 主服務，
兩邊共享遊戲服務及操作鎖，不交給 AI 回覆。
Twitch 提供上述文字操作；裝備管理、採集與進階互動使用 Discord 原生介面。
文字自動戰鬥使用上游一般怪物、回合、掉落、經驗、配點、難度與連勝規則；
選擇式 Boss 門與特殊遭遇使用 Discord `/combat`。

## 帳號與存檔

要共用角色，先在 Discord 建角並使用 `/rpg link`，再由自己的 Twitch 帳號
輸入回傳的 `!rpg link 驗證碼`。預設五分鐘有效，一次使用後刪除，資料庫僅存雜湊。
已在 Twitch 建角或綁定的帳號不能被直接覆蓋，沒有自動合併或刪除資產。
未綁定玩家可各自玩；相同暱稱或相同數字 ID 不會自動視為同一人。
Discord 原生視窗開啟時，Twitch 不能同時修改該角色。

正式存檔使用上游 `users`、裝備、技能等表，另加身分映射及綁定碼表。
偵測到舊原型 `rpg_players` 時，首次初始化前保存 `.prototype.bak` 備份，
舊表也保留；原型金幣與經驗不換算成 Idleology 資產。
備份時應停止主服務後複製 SQLite；執行中的 WAL 存檔請使用 SQLite backup API。

## 驗證

```powershell
.venv\Scripts\python.exe -m unittest scripts.test_rpg_localization scripts.test_rpg scripts.test_discord_commands scripts.test_twitch_eventsub scripts.test_discord_host scripts.test_process_runtime scripts.test_package_structure
uv pip check --python .venv\Scripts\python.exe
```

測試使用暫存資料庫，不登入平台、不修改正式存檔。
版本與本地修正見 [IDLEOLOGY.md](IDLEOLOGY.md)。
