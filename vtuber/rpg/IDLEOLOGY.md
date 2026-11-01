# 上游來源與本地整合

來源：https://github.com/davidgao93/Idleology

作者：davidgao93 與上游貢獻者。
固定版本：`9cafeb412a5c6fc1548860737160249afccfc836`，2026-11-01 取得。
上游說明保留於 `idleology/UPSTREAM_README.md`。根目錄未提供標準 LICENSE；
上游 README 提及個人／自架用途，未替上游程式與素材新增授權。

## 納入內容

`core/`、`database/`、`cogs/`、`assets/` 納入 `vtuber/rpg/idleology/`。
不執行 `bot.py`，不載入其 Token、`.env` 或 Bot 管理入口。
遊戲 Cog 清單見 `discord_adapter.GAME_COGS`，排除 `admin`、`owner`、`general`。
上游 schema 與初始化遷移由 `bootstrap.py` 執行。

## 本地修正

- 匯入改為 `vtuber.rpg.idleology.*`，不修改 `sys.path` 或全域模組別名。
- 素材使用 `paths.asset_path`，不依賴啟動目錄。
- SQLite 一般操作使用 autocommit，managed transaction 明確執行
  `BEGIN IMMEDIATE`，避免不同非同步工作的寫入混入同一個 rollback。
- Alchemy 欄位遷移快取改為 repository instance 範圍，避免切換存檔時漏遷移。
- 遊戲指令加上主伺服器檢查，事件頻道設定加上管理員檢查，免消耗
  `rite_debug` 不發布；關閉時停止遊戲排程並釋放資料庫。
- 跨平台文字操作、帳號綁定及程序通訊在上游以外實作。
- 繁中語系僅處理輸出文字與指令的本地化欄位，保留原始識別碼和存檔。
  `localization.py` 分別處理一般訊息與互動 webhook；模態視窗改為繼承
  `LocalizedModal`，以保留從遊戲畫面開啟時的語系範圍。
- Discord 使用原生 ID；未綁定 Twitch 使用負數字串作為遊戲內部 ID，
  維持上游 `int(user_id)` 相容性且不與 Discord ID 衝突。

## 重新取得來源

`scripts/vendor_idleology.py` 接受已 checkout 至固定 commit 的上游目錄，
重建隔離套件、素材路徑與 DB bootstrap；不下載、登入或啟動上游 Bot。
套件外的共用服務、Discord 轉接器與 Twitch 接入為本地實作。

完整進階玩法保留上游實作，離線測試涵蓋模組載入與核心共用流程，
並非已逐一實玩所有 Boss、副本與 UI；真實平台回覆、按鈕及權限仍需線上驗證。
