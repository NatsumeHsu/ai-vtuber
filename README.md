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
LLM、ASR、Index-TTS、Live2D 與攝影機均在同一個 Python process 執行，使用執行緒維持介面回應，TTS 模型只載入一次。
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
`models/tts` 內原有角色權重保留，現用 IndexTTS 模型位於 `index-tts/checkpoints`。
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

主程式優先讀取本機 `setting.yml`，不存在時讀取 `setting.yml`。兩者為完整設定檔，不會互相合併；後續請修改正在使用的設定檔。
本機 `setting.yml` 已排除 Git。首次設定可複製 `example.yml` 為 `setting.yml`，先調整模型、參考音檔與攝影機設定，再設定 `twitch.enabled: true`，填寫 `channel`（Twitch 頻道帳號），即可匿名連線 IRC，不需要 Token 或登入帳號。不再讀取 Node 專案的 `.env`。

啟動原本的 `start.bat` 即可。模型載入完成後自動連線，閒置時接受一則聊天室訊息，觀眾名稱與訊息會進入主程式文字對話流程，並保留於原本的對話紀錄。從接受訊息到回覆生成、語音合成及播放完成期間，新聊天室訊息直接略過，不排隊、不補讀；手動輸入或錄音處理中也會略過聊天室訊息。完成後接受下一則新訊息。略過 `twitch.ignored_users` 名單，斷線按 `reconnect_seconds` 自動重連。

聊天室連線在處理期間維持運作並回應 PING，但略過當時的新訊息，避免完成後收到積壓的舊訊息。透過 TLS 直接連線 `irc.chat.twitch.tv:6697`，斷線後自動重連。此整合只讀取聊天並在本機回覆，不會傳送訊息到 Twitch，也不包含原 Node 專案的點歌或 OBS 網頁。
