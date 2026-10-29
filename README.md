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
主程序保留控制視窗、Live2D、攝影機、ASR、資料庫與對話排程，另啟動四個獨立 Python 子程序：Twitch、Discord、文字生成（LLM），以及語音生成（Index-TTS）。程序間使用私有 pipe 通訊，不開放網路服務。
控制視窗的「子程序控制」可分別停止、啟動或重啟這四個模組。先停止對應模組，修改並儲存程式或設定檔，再按「啟動」；新子程序會重新匯入程式及載入設定／模型，不必關閉 Live2D。修改主程序的介面、ASR、排程或資料庫程式仍需重啟整個應用程式。
停止會取消該子程序尚未完成的請求；已保存的對話保留，不自動重播或重送失敗請求。停止 Discord 或 Twitch 期間，僅該平台不會接收新訊息；另一平台不受影響。重啟時僅重新連線對應平台，修改 Discord 收音啟用狀態若需要首次載入 ASR，仍需重啟整個應用程式。
攝影機改用 OpenCV DirectShow，不再啟動 FFmpeg process。

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
本機 `setting.yml` 已排除 Git。首次設定可複製 `example.yml` 為 `setting.yml`，先調整模型、參考音檔與攝影機設定，再設定 `twitch.enabled: true`，填寫 `channel`（Twitch 頻道帳號），即可匿名連線 IRC，不需要 Token 或登入帳號。不再讀取 Node 專案的 `.env`。

啟動原本的 `start.bat` 即可。模型載入完成後自動連線，閒置時接受一則聊天室訊息，觀眾名稱與訊息會進入主程式文字對話流程，並保留於原本的對話紀錄。從接受訊息到回覆生成、語音合成及播放完成期間，新聊天室訊息只存檔、不回覆、不排隊、不補讀；手動輸入或錄音處理中也採相同方式。完成後接受下一則新訊息。略過 `twitch.ignored_users` 名單，斷線按 `reconnect_seconds` 自動重連。

聊天室連線在處理期間維持運作並回應 PING，當時的新訊息仍會存檔，但不安排回覆，避免完成後處理積壓的舊訊息。透過 TLS 直接連線 `irc.chat.twitch.tv:6697`，斷線後自動重連。此整合只讀取聊天並在本機回覆，不會傳送訊息到 Twitch，也不包含原 Node 專案的點歌或 OBS 網頁。

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
| `vtuber/twitch_chat.py` | 匿名 IRC 連線與訊息解析 |
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

在本機 `setting.yml` 的 `discord` 區塊填寫 `bot_token`、`guild_id`、`voice_channel_id`，再設定 `enabled: true` 並重新啟動。範本 `example.yml` 的 Token 保持空白；本機設定已被 Git 排除，請勿將 Token 填入共用範本。尚未設定時預設停用，不影響 Twitch 與本機對話。支援普通語音頻道，尚不支援 Stage 頻道。

啟動後，Bot 自動進入指定頻道，依 Discord 使用者 ID 分開收音，忽略其他 Bot 的音訊。成員停頓後，使用既有 SenseVoice 模型辨識成繁體中文，存入原本的觀眾資料庫，再進入優先佇列。語音訊息可在忙碌時排隊，待當輪「回覆生成、語音生成與播放」完成後，先依收到的辨識結果順序處理語音，再處理一般文字；不會中斷當輪。Twitch 訊息仍維持忙碌時只存檔、不安排回覆。Discord、Twitch、手動輸入使用不同的身分鍵，不會只憑相同暱稱合併為同一人。

Discord 語音回覆使用現有角色與人際記憶、情緒分析、IndexTTS 和繁簡轉換。產生的音訊會在記憶體轉為 Discord 所需的 48 kHz 雙聲道 PCM，再由 Opus 傳送；不另啟動 FFmpeg。Live2D 嘴型會隨 Discord 的音訊播放更新。Discord 回覆預設不在本機喇叭重播；`local_playback: true` 可額外在本機播放（在 Discord 播放完成後）。本機與 Twitch 回覆仍播放到原本的音訊輸出。即使關閉麥克風的 `asr.enabled`，啟用 Discord 後仍會載入同一份 ASR 模型供 Discord 使用。

`discord` 分類的參數：

- `enabled`、`bot_token`、`guild_id`、`voice_channel_id`：功能開關與 Bot 連線設定。
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
