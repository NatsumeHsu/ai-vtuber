# AI VTuber 白璃

Windows 本機 Live2D、GGUF 對話、SenseVoice 語音辨識、Index-TTS 2.5 語音生成。

## 環境與重建

需要 uv、NVIDIA 驅動、Microsoft C++ 執行階段及既有本機模型。
雙擊 setup.bat，或執行 `powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -NoLaunch`。
安裝器使用 uv 與 Python 3.11；所有功能共用 `D:\AI\ai-vtuber\.venv`。
主程式與 IndexTTS 2.5 依賴統一鎖定於 requirements.txt，直接依賴來源為 requirements-setup.txt。
共用 NumPy 1.26.4、MediaPipe 0.10.21、OpenCV contrib 4.11、PyTorch 2.8.0 CUDA 12.8。
IndexTTS 上游的 NumPy/OpenCV 版本限制以本專案相容版本取代；
安裝器將未使用的 DAC 量化器改為延遲載入，避免 audiotools 的舊 protobuf 限制與 MediaPipe 衝突。
不要在 index-tts 執行 uv sync。
llama-cpp-python 0.3.36 使用官方 CUDA 13.0 wheel，並安裝獨立 CUDA 13 執行庫；
PyTorch 保持 CUDA 12.8，兩套原生執行庫使用不同 DLL 名稱。
`vtuberAI.py` 以 Discord 機器人為主服務：主程序持有 Discord 連線與指令，啟動桌面子程序（控制介面、Live2D、攝影機、ASR、資料庫及排程），桌面子程序再管理 Twitch、文字生成（LLM）與語音生成（Index-TTS）子程序。程序間使用私有 pipe 通訊，不開放網路服務。
控制視窗可分別停止、啟動或重啟 Twitch、文字生成與語音生成；停止後儲存程式修改，再按啟動即可重新載入。重啟這些模組不會重新登入 Discord。修改 Discord 主服務、桌面介面、ASR 或排程則需整體重啟。
停止會取消該子程序尚未完成的請求；已保存的對話保留，不自動重播或重送失敗請求。停止 Twitch 不影響 Discord 連線。AI 尚未載入完成時收到的 Discord 語音不會排入回覆；關閉桌面視窗會結束整個服務。
攝影機改用 OpenCV DirectShow，不再啟動 FFmpeg process。

### 啟動與頻道控制

雙擊 `start.bat`，或在專案目錄執行 `.\.venv\Scripts\python.exe -X utf8 .\vtuberAI.py`。
控制視窗的「頻道設定」可更新 Discord 語音頻道 ID 與 Twitch 頻道名稱。Discord 頻道僅用於本次執行，不存入設定；Twitch 頻道會寫回本機 `setting.yml`。Discord 頻道必須屬於設定中的 `guild_id`，且為普通語音頻道；切換時會中斷正在播放的 Discord 音訊。Twitch 更新後只重啟 Twitch 子程序，`enabled` 開關保持原設定。

Discord 指令白名單預設為空，沒有任何人可使用切換指令。先在本機以指令加入使用者 ID，再啟動服務：

```powershell
cd D:\AI\ai-vtuber
.\.venv\Scripts\python.exe -X utf8 .\vtuberAI.py --allow-discord-user 你的Discord使用者ID
.\.venv\Scripts\python.exe -X utf8 .\vtuberAI.py
```

`--allow-discord-user` 可重複指定多個 ID，設定儲存後程式會離開。`--clear-discord-users` 可清空白名單；修改名單後需重啟主服務。請替換上述 ID 佔位文字為數字。

儲存 `setting.yml` 後，可按控制視窗的「重新載入設定」，或由白名單使用者執行 `/reload`。程式先檢查設定格式，再重啟桌面與所有功能子程序、重新建立 Discord 連線，讓模型、ASR、Live2D、Twitch、歡迎訊息、文字回覆與白名單等設定套用。進行中的工作會中斷，模型需要重新載入。伺服器未改變時會保留本次語音目標並重新連線。
重新載入固定保留目前使用的 `bot_token`，不會將它寫回或覆蓋設定檔內修改的 token；若要改用新 token，請結束整個程式後重新啟動。訊息「子程序正在重新載入模型」只表示已開始重新啟動，不代表模型已載入完成。
白名單使用者可先加入普通語音頻道，再於已設定的 Discord 伺服器使用 `/join`，機器人會加入該使用者所在的頻道，不需提供參數。非白名單使用者、其他伺服器與私訊皆無切換權限。機器人邀請需包含 `applications.commands` scope，並具備目標頻道的檢視頻道、連線及說話權限。

### Discord 基本伺服器指令

| 指令 | 功能 | 權限 |
| --- | --- | --- |
| `/help` | 查看指令說明 | 已設定伺服器的成員 |
| `/ping` | Discord 心跳延遲 | 已設定伺服器的成員 |
| `/status` | 服務運行時間、語音連線、自動加入與待辨識片段數 | 已設定伺服器的成員 |
| `/serverinfo` | 伺服器名稱、ID、成員數與頻道數 | 已設定伺服器的成員 |
| `/userinfo [member]` | 自己或指定成員的名稱、ID、加入時間 | 已設定伺服器的成員 |
| `/roll [dice]` | 擲骰，預設 `1d6`，例如 `2d20` | 已設定伺服器的成員 |
| `/choose options` | 隨機抽一個選項，例如 `咖啡｜茶｜果汁` | 已設定伺服器的成員 |
| `/poll question options [hours] [multiple]` | Discord 原生投票，選項用 `｜` 分隔，預設 24 小時、單選 | 成員與機器人皆需建立投票權限 |
| `/avatar [member]` | 查看自己或指定成員的頭像 | 已設定伺服器的成員 |
| `/join` | 加入下指令者所在的語音頻道，開啟自動加入 | 本機 ID 白名單 |
| `/leave` | 離開語音、清除未辨識音訊並關閉自動加入 | 本機 ID 白名單 |
| `/reload` | 重新讀取設定，保留目前登入的 bot_token | 本機 ID 白名單 |

`/roll`、`/choose` 的結果與 `/poll` 投票公開顯示在下指令的頻道，其餘指令及錯誤回覆只對使用者本人顯示。擲骰限制 1～20 顆、每顆 2～1000 面；抽籤限制 2～20 個不同選項；投票限制 2～10 個不同選項、1～168 小時，`multiple: true` 可複選。投票由 Discord 保存，機器人重啟後仍可使用。
`/leave` 會將 `discord.auto_join: false` 寫回設定，重開程式也不會自動進入語音；`/join` 或介面更新 Discord 頻道時會重新開啟。此設定不影響機器人登入或一般指令。`/status` 顯示 Discord 服務狀態，不代表 AI 模型已完成載入。
更新指令程式後需重啟整個服務，登入後會註冊全域指令，再清除設定伺服器內舊的限定指令以避免重複顯示；完成後重新連線不會重複同步。全域註冊不改變既有權限：仍只允許在設定的伺服器執行，語音控制仍需 ID 白名單。

### 新成員歡迎訊息

在本機 `setting.yml` 的 `discord.member_services` 設定：

```yaml
  member_services:
    welcome_channel_id: 0
    ignore_bots: true
    welcome_message: '歡迎 {mention} 加入 {server}！目前有 {member_count} 位成員。'
```

`welcome_channel_id` 填入設定伺服器內的文字頻道 ID；`0` 表示關閉歡迎訊息。訊息可使用 `{user}`、`{mention}`、`{user_id}`、`{server}`、`{member_count}`；通知停用實際提及，因此不會發出 @everyone、身分組或使用者提示。

設定歡迎頻道後，程式會啟用 Members intent；請先到 Discord Developer Portal → Bot → Privileged Gateway Intents 開啟 **Server Members Intent**，再重啟服務。歡迎頻道需要檢視及傳送訊息權限。

預設略過其他機器人，只歡迎新加入的成員，不回溯補發訊息。傳送失敗會記錄於本機控制視窗。

### Discord 文字頻道隨機回覆

`discord.text_replies` 控制指定 `guild_id` 伺服器的文字頻道回覆：

```yaml
  text_replies:
    enabled: true
    probability: 0.025
    context_messages: 10
```

每則非機器人、非 Webhook、非空白且不以 `/` 開頭的文字訊息，獨立以 2.5% 機率觸發。只處理普通文字頻道，不處理私訊、討論串或其他伺服器。文字生成子程序未就緒或已停止時不擷取上下文、不回覆；忙碌時存檔並標記 `skipped_busy`，不排隊，Discord 語音仍保有原本的優先順序。

觸發後擷取當則訊息之前最近 10 則頻道訊息（不足則取現有訊息），依舊到新排列並連同當則訊息存入原本 SQLite 的 `discord_text_contexts` 表；保留訊息與發言者 ID、名稱、文字、時間，以及回覆與傳送狀態。相同觸發訊息 ID 不會重複處理。模型只使用該頻道上下文，不混入其他頻道或語音的短期對話；模型容量不足時從最舊的上下文開始移除，資料庫仍保留完整擷取內容。

生成結果以文字回覆原訊息，不合成語音、不觸發提及通知。請在 Developer Portal 開啟 **Message Content Intent**，並給予機器人檢視頻道、讀取訊息歷史與傳送訊息權限，再重啟服務。關閉 `text_replies.enabled` 可停用此功能及 Message Content intent。

`setup.bat -CheckOnly` 可驗證環境、GPU 與模型檔案。
既有模型和 FFmpeg 必須保留；安裝器不再下載 GPT-SoVITS 模型。
遷移暫存與舊環境備份已清理；所有功能僅使用專案根目錄的 `.venv`。

## 語音設定

編輯 setting.yml 的 tts：

- root: index-tts
- config_path: checkpoints/config.yaml（相對於 Index-TTS 目錄）
- model_dir: checkpoints（相對於 Index-TTS 目錄）
- reference_audio: 參考音檔路徑，支援絕對路徑與專案相對路徑
- generation.lang: ZH
- generation.duration_factor: 時長倍率，數字越大越慢
- generation.num_beams: 1 較快，3 可增加搜尋數

不再使用 GPT/SoVITS 權重或參考逐字稿；舊 GPT_SoVITS 引擎已移除。
現用 IndexTTS 模型位於 `index-tts/checkpoints`。未使用的舊 GPT-SoVITS 角色權重 `hakuri.ckpt`、`hakuri.pth` 已移出 `models`，封存於本機 `data/unused-models/gpt-sovits-tts`；封存可復原，尚未釋放磁碟空間。
情感中介層使用現有 GGUF LLM，以獨立 JSON 請求分析朗讀內容，不加入對話歷史。
`tts.emotion_analysis` 可調整開關、提示詞、溫度、輸出長度、輸入上限及備援向量。
向量順序固定為：高興、憤怒、悲傷、恐懼、反感、低落、驚訝、自然。
每個數值限制於 max_component，總和限制於 max_total（最多 0.8）。
無效 JSON、非數字或分析失敗會記錄警告並使用 fallback_vector；關閉分析則沿用參考音訊情緒。
`tts.generation.use_random: false` 固定情感樣本選擇，取樣仍由 generation 的其他參數控制。
Live2D 表情標記與情感向量各自運作，原有 LLM、ASR、Live2D 設定保留。

## 啟動與操作

雙擊 start.bat，或執行 `.venv/Scripts/python.exe -X utf8 vtuberAI.py`。
等待模型載入後，可輸入文字或錄音對話；回覆會寫入 output.wav、播放並帶動嘴型。
全部執行日誌統一寫入 logs/vtuber.log。
關閉控制視窗會停止工作執行緒並釋放模型。
模型載入與推論不提供強制逾時中止，舊 tts.startup_timeout、tts.inference_timeout 設定已移除。

setting.yml 保留原本的 live2d、llm、chat、asr 與 output 設定。
攝影機裝置可在 live2d.camera_tracking.device 修改；關閉追蹤設 enabled: false。

攝影機擷取格式可設定 camera_tracking.width、height、pixel_format、fps，預設 YUY2 640×360 / 30 FPS。
# Twitch 聊天輸入

主程式讀取根目錄的本機 `setting.yml`。
`vtuber/twitch_chat.py` 統一管理 Twitch 服務的啟動、停止、專案回呼、雙帳號 OAuth、EventSub 收訊、指令與關鍵字回覆。由原本 Twitch 服務啟動，不需要 Node.js 或 `.env`，不再使用 IRC 或 `mode` 設定。

在本機 `setting.yml` 的 `twitch` 分類填入 `enabled: true`、`channel`、`client_id`、`client_secret`、`redirect_uri: http://localhost:3000/callback`。Twitch Developer Console 必須登記相同回呼網址。也可透過環境變數 `TWITCH_CLIENT_ID`、`TWITCH_CLIENT_SECRET` 提供憑證。請勿將密鑰放入公開範本。

照常執行 `start.bat`，依控制視窗日誌的網址先以 Bot 帳號授權，再以實況主帳號授權，各限時五分鐘。兩者必須不同，實況主必須符合 `channel`。授權保存在 `data/tokens.json`（已排除 Git），相容原 TypeScript 的毫秒到期時間格式，支援自動更新。換帳號時先停止 Twitch，再移除授權檔中該角色的項目並重新啟動。

`data/commands.json` 每則訊息重新讀取，支援 `commands` 的 `response`、`role`（`everyone`、`moderator`、`broadcaster`）與 `keywords`。`prefix` 預設 `!`；回覆支援 `{user}`、`{args}`，以 Bot 帳號引用原訊息送出。`tokens_file`、`commands_file` 可指定相對專案根目錄的路徑。忽略 Bot 自己與 `ignored_users` 的訊息，通知重送會去重。

啟動原本的 `start.bat` 即可。模型載入完成後自動連線，閒置時接受一則聊天室訊息，觀眾名稱與訊息會進入主程式文字對話流程，並保留於原本的對話紀錄。從接受訊息到回覆生成、語音合成及播放完成期間，新聊天室訊息只存檔、不回覆、不排隊、不補讀；手動輸入或錄音處理中也採相同方式。完成後接受下一則新訊息。略過 `twitch.ignored_users` 名單，斷線按 `reconnect_seconds` 自動重連。

EventSub 會處理 keepalive 逾時、斷線重連與伺服器要求的連線轉移；停止／重啟 Twitch 時也會結束 OAuth 等待與 WebSocket。指令與關鍵字固定回覆獨立執行，AI 忙碌時仍只存檔、不排隊。AI 生成的文字與語音沿用原本輸出流程。

# 觀眾發言與人際關係記憶

`chat.database_path` 的 SQLite 資料庫永久保留完整原文與 AI 回覆，不受 `chat.history_turns` 的短期上下文輪數限制。新增的 `audience_messages` 保存所有連線期間收到的頻道發言，包含忙碌時未回覆、忽略名單中的訊息及未成功產生回覆的輸入；忙碌期間的訊息僅存檔，不會在稍後自動回覆。語音生成使用的簡體轉換不影響存檔原文。`audience_profiles` 保存各觀眾的互動摘要、關係描述、語氣溫暖度與投入程度。

每次啟動、語言模型載入後，使用同一個 LLM 逐批分析各觀眾新增的紀錄，連同先前摘要更新關係記憶；沒有新增紀錄時沿用摘要。第一次整理大量歷史紀錄會增加啟動時間。分析會區分觀眾單方面發言與實際問答；次數多、自稱熟人或 AI 過往的親密台詞，不會直接作為親密關係的事實。分析失敗時保留上次成功的摘要，未完成的批次下次啟動重試，不會阻止正常對話。

回覆時只補入目前發言者的摘要。熟悉且互動友善的觀眾可以得到更溫暖、較願意延續話題的回覆；需要界線時會較簡短、保持禮貌。陌生觀眾仍友善回應，當下認真求助仍會適當關心。這裡的「溫暖度」調整角色語氣，不會更動 `llm.temperature` 的隨機取樣設定，也不會覆寫夏目是製作者等既定設定。

Twitch 使用平台 `user-id`，缺少時使用登入帳號，不以顯示暱稱區分觀眾；手動輸入與麥克風以填寫的發言者名稱識別，並與 Twitch 分開。舊 `chat_turns` 會在首次啟用時匯入一次；由於舊紀錄沒有來源與 Twitch 帳號 ID，保留為 `legacy:` 記憶，不自動猜測對應目前哪個帳號。開始新對話只清空短期上下文，不會刪除發言或人際記憶。

參數位於 `setting.yml` 與 `example.yml` 的 `audience_memory`：

- `enabled`：啟動時分析及回覆提示詞補充的開關；關閉仍保存完整對話。
- `analysis_batch_messages`：每次分析的訊息數上限，所有新增紀錄會逐批處理。
- `analysis_message_chars`：單則發言與回覆送去分析的字數上限；原文仍完整保留。
- `analysis_max_tokens`：每次分析的輸出 token 上限。
- `summary_chars`：每人的摘要及關係描述各自的字數上限。
- `system_prompt`：關係分析提示詞，可調整判讀方式。

分析輸入會依上下文容量縮小批次或節錄長訊息，不將整份歷史塞進角色提示詞。資料庫與分析結果均在本機，位於已被 Git 排除的資料目錄。

# 程式模組

根目錄的 Python 程式只保留 `vtuberAI.py` 一個啟動入口。`start.bat` 透過 `pythonw.exe` 啟動它，並將輸出及錯誤寫入 `logs/vtuber.log`；使用 `python.exe` 啟動時，輸出保留在命令列。`setup.bat`、設定檔及說明文件維持原本用途。既有的 `from vtuberAI import VtuberAI` 仍可使用；其他程式建議改由 `from vtuber import VtuberAI` 匯入。

| 模組 | 負責功能 |
| --- | --- |
| `vtuber/app.py` | `VtuberAI` 組裝、共用狀態、模型載入與關閉 |
| `vtuber/config.py` | 設定讀取與以專案根目錄為準的路徑解析 |
| `vtuber/storage.py` | 對話工作階段、歷史讀取及 SQLite 存檔 |
| `vtuber/conversation.py` | LLM 回覆、Twitch 訊息接入與忙碌控制 |
| `vtuber/speech.py` | 語音辨識、文字前處理、合成及播放 |
| `vtuber/controls.py` | 控制視窗、按鈕及跨執行緒介面事件 |
| `vtuber/avatar.py` | Live2D 繪製、表情分層與嘴型 |
| `vtuber/tracking.py` | 攝影機、視線、頭部與手勢追蹤 |
| `vtuber/audience_memory.py` | 觀眾發言封存及人際關係摘要 |
| `vtuber/emotion_middleware.py` | 語音情緒分析 |
| `vtuber/index_tts_client.py` | IndexTTS 介接及語音輸入繁簡轉換 |
| `vtuber/twitch_chat.py` | Twitch 生命週期、OAuth、EventSub、指令與關鍵字回覆 |
| `vtuber/discord_voice.py` | Discord 雙向語音、分段辨識、音訊轉換與播放 |
| `vtuber/hand_gestures.py` | 手部關節與手勢判定 |
| `vtuber/virtual_camera.py` | DirectShow 攝影機擷取 |
| `vtuber/runtime_support.py` | Windows CUDA 動態函式庫路徑準備 |

主程式由各功能的 mixin 組成，介面與 Live2D 在主執行緒更新。對話回覆依序處理；社群子程序透過 IPC 將收音送到主程序的 ASR，並將文字訊息交給既有排程。LLM（含名字校正、人際記憶與情緒分析）由文字生成子程序持有，Index-TTS 由語音生成子程序持有。匯入套件不會啟動攝影機、開視窗或載入模型；子程序啟動時才載入對應模型。

不使用硬體的功能測試：`.venv/Scripts/python.exe -X utf8 scripts/test_modular_app.py`。
語音情緒與繁簡轉換測試：`.venv/Scripts/python.exe -X utf8 scripts/test_emotion_middleware.py`。
實際模型整合檢查：`.venv/Scripts/python.exe -X utf8 scripts/check_integration.py`（會載入本機模型並產生測試音檔，不播放音訊）。

# Discord 雙向語音

在 Discord Developer Portal 建立應用程式與 Bot，將 Bot 邀請到伺服器，授予指定語音頻道的「查看頻道」「連線」「說話」權限。開啟 Discord 的開發者模式，複製伺服器與普通語音頻道 ID。此功能使用 Bot 帳號，不能像 Twitch 匿名登入，也不要填入一般使用者 Token；不需要 Message Content 或 Members 特權 Intent。

Discord 是固定啟用的主服務，不再使用 `discord.enabled`。在本機 `setting.yml` 的 `discord` 區塊填寫 `bot_token`、`guild_id` 後啟動；缺少登入設定會報錯。啟動後等待白名單使用者以 `/join` 或介面選擇語音頻道，不保存上次的語音頻道。範本 `example.yml` 的 Token 保持空白；本機設定已被 Git 排除，請勿將 Token 填入共用範本。支援普通語音頻道，尚不支援 Stage 頻道。

使用 /join 或介面加入語音頻道後，依 Discord 使用者 ID 分開收音，忽略其他 Bot 的音訊。成員停頓後，使用既有 SenseVoice 模型辨識成繁體中文，存入原本的觀眾資料庫，再進入優先佇列。語音訊息可在忙碌時排隊，待當輪「回覆生成、語音生成與播放」完成後，先依收到的辨識結果順序處理語音，再處理一般文字；不會中斷當輪。Twitch 訊息仍維持忙碌時只存檔、不安排回覆。Discord、Twitch、手動輸入使用不同的身分鍵，不會只憑相同暱稱合併為同一人。

Discord 語音回覆使用現有角色與人際記憶、情緒分析、IndexTTS 和繁簡轉換。產生的音訊會在記憶體轉為 Discord 所需的 48 kHz 雙聲道 PCM，再由 Opus 傳送；不另啟動 FFmpeg。Live2D 嘴型會隨 Discord 的音訊播放更新。Discord 回覆預設不在本機喇叭重播；`local_playback: true` 可額外在本機播放（在 Discord 播放完成後）。本機與 Twitch 回覆仍播放到原本的音訊輸出。即使關閉麥克風的 `asr.enabled`，啟用 Discord 後仍會載入同一份 ASR 模型供 Discord 使用。

`discord` 分類的參數：

- `enabled`、`bot_token`、`guild_id`：功能開關與 Bot 連線設定。
- `local_playback`：是否額外在本機播放 Discord 回覆，預設 false。
- `speech_threshold`：判定說話的音量門檻。
- `silence_seconds`：停頓多久後送去辨識。
- `min_speech_seconds`：略過比此值短的聲音片段。
- `max_utterance_seconds`：單段最長秒數；連續說話超過時分段辨識。
- `max_speakers`：同時收音的成員數上限。
- `reconnect_seconds`：語音斷線後重新加入的等待秒數。

辨識結果與回覆會永久留存，供下次啟動更新觀眾關係；原始音訊與待回覆佇列僅保存在記憶體，關閉程式不會自動補播。若有大量持續發言，語音佇列會累積等待時間。

收音依賴 discord.py 2.7.1、davey 0.1.7，以及鎖定 commit 的 DAVE 接收修正版（上游尚未合併的 [PR #65](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/65)，commit `4da8413355893ed93798be9b4f3a07bc083875f6`）。一般已發布版本的接收套件不包含本專案驗證的修正，請由 requirements.txt 安裝。依賴已加入安裝器；真實頻道的收音、權限與重連仍需填入自己的 Bot 設定後驗證。

離線測試：`.venv/Scripts/python.exe -X utf8 scripts/test_discord_voice.py`，驗證音訊格式、Opus 編解碼、分人停頓分段、轉錄入列、語音優先及模型鎖。
