# 資料庫

使用 SQLAlchemy 2 ORM 管理 SQLite。`models.py` 宣告資料表，`engine.py` 管理 WAL、連線與獨立交易，`storage.py` 管理對話及 Discord 快照，`audience_memory.py` 管理觀眾紀錄與摘要，`songs.py` 管理 YouTube 歌單、播放狀態與去重歷史。點歌使用 `song_videos`、`song_requests`、`song_playback` 三張新增資料表，待播編號使用 SQLite 遞增整數。

沿用 `setting.yml` 的 `chat.database_path`，不搬動或清空既有資料檔。`create_all` 只建立缺少的資料表；表名、欄位、主鍵及 UTC 時間格式維持原格式。舊對話匯入以 `legacy_turn_id` 去重並保留完成標記。

每次操作使用獨立 ORM Session，成功提交，例外回滾並關閉連線；不跨執行緒共用 Session。應用程式存取透過 ORM。`Database.connect()` 僅保留供既有診斷測試直接查詢 SQLite。

驗證：`.venv/Scripts/python.exe -B -m unittest database.tests`。
