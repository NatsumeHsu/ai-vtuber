# AI VTuber：白璃 HAKURI

在 Windows 本機執行的 AI VTuber，整合 Live2D 角色、GGUF 語言模型、GPT-SoVITS 語音合成與 SenseVoice 語音辨識。使用 Tkinter 控制視窗輸入文字或錄音，角色會回覆、播放語音並同步嘴型，也支援攝影機追蹤與手勢表情。

## 功能

- 使用 `llama-cpp-python` 載入本機 GGUF 模型，角色設定與回覆參數集中於 `setting.yml`。
- GPT-SoVITS 使用角色權重與參考音檔合成語音，透過音量控制 Live2D 嘴型。
- SenseVoice 辨識麥克風輸入，轉為台灣繁體中文，可使用 LLM 校正角色名字。
- SQLite 儲存對話紀錄，重啟後載入最近對話。
- 透過 FFmpeg DirectShow 讀取攝影機，支援視線、頭部追蹤及 MediaPipe 中指手勢表情。
- 提供無終端啟動器，日誌寫入 `logs/vtuber.log`。

## 環境準備

### 一鍵安裝與執行（建議）

在 Windows 10/11 x64 雙擊 `setup.bat`。安裝器會自動尋找或安裝 Python 3.12、建立 `.venv`、安裝執行依賴、偵測 NVIDIA GPU、安裝 PyTorch 與 llama 預編譯套件、下載 FFmpeg/FFprobe 及公開基礎模型，檢查成功後直接啟動程式。首次執行需要網路與數 GB 以上的可用磁碟空間，下載時間依網速而定。

**白璃 Live2D、GGUF 對話模型、角色 GPT/SoVITS 權重及參考音檔仍需自行提供**；沒有這些檔案時，安裝器會列出缺項並停止啟動，清單寫入 `logs/setup-missing.txt`。放好檔案、修改 `setting.yml` 後重新執行即可。缺少 Microsoft C++ 執行階段時會自動安裝，Windows 可能要求管理員授權；若要求重開機，重開後再執行。攝影機或虛擬攝影機裝置及 NVIDIA 驅動仍需依硬體安裝，安裝器不會替換驅動。

可在終端指定選項：

```powershell
.\setup.bat -Device cpu        # 強制 CPU
.\setup.bat -Device cuda       # 要求 CUDA；不可用時回報失敗
.\setup.bat -NoLaunch          # 安裝及檢查，不啟動
.\setup.bat -SkipModels        # 使用已備妥的模型，略過公開模型下載
.\setup.bat -CheckOnly         # 僅檢查檔案與匯入，不安裝或修改設定
```

自動模式會依實際 GPU 可用性調整 LLM、ASR 與 TTS 裝置；關閉非必要的 FlashAttention，避免要求額外編譯環境。首次修改設定前會保留 `*.setup.bak` 備份；YAML 寫回時會保留設定值，但不保留原有註解。完整安裝日誌位於 `logs/setup.log`。安裝依賴使用 `requirements-setup.txt`，原始 `requirements.txt` 保留作為開發環境紀錄。

下載來源：[Python](https://www.python.org/downloads/release/python-31210/)、[PyTorch](https://pytorch.org/get-started/previous-versions/)、[llama-cpp-python](https://github.com/abetlen/llama-cpp-python)、[FFmpeg Windows 建置](https://www.gyan.dev/ffmpeg/builds/)、[GPT-SoVITS 基礎模型](https://huggingface.co/lj1995/GPT-SoVITS)、[G2PW](https://www.modelscope.cn/models/kamiorinn/g2pw)、[SenseVoice](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)。

### 手動安裝

目前本機環境使用 **Windows、Python 3.12**；`requirements.txt` 包含 CUDA 12.8 版本的 PyTorch。預設設定啟用 GPU，需準備相容的 NVIDIA GPU、驅動及具 GPU 支援的 `llama-cpp-python`。模型大小與上下文長度會影響記憶體和顯示記憶體需求。

在專案根目錄執行 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install torch==2.7.1+cu128 torchaudio==2.7.1+cu128 torchvision==0.22.1+cu128 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 是現有環境的版本清單，尚未驗證全新環境安裝；套件來源、Python 版本與 GPU 編譯選項可能需要依環境調整。一般安裝的 `llama-cpp-python` 不一定支援 GPU，預設 `llm.n_gpu_layers: -1` 會檢查 GPU offload 是否可用。

攝影機追蹤會直接使用專案根目錄的 `ffmpeg.exe`，請自行準備；其他音訊處理若需要 `ffprobe.exe`，也請一併準備。這些執行檔不納入 Git。

## 準備模型與參考音檔

**模型、角色素材與音檔皆不隨原始碼提供，需自行取得有使用權的檔案。** 預設路徑如下，也可修改 `setting.yml`：

| 用途 | 預設路徑／設定 |
| --- | --- |
| Live2D | `models/l2d/白璃/白璃.model3.json`，包含引用的貼圖、動作與表情 |
| 語言模型 | `models/llm/openher_4b_q4.gguf` |
| GPT 權重 | `models/tts/hakuri.ckpt` |
| SoVITS 權重 | `models/tts/hakuri.pth` |
| 語音辨識 | `models/asr/SenseVoiceSmall/`，需包含 `model.pt`、`config.yaml` 及模型所需其他檔案 |
| 手勢辨識 | `models/vision/hand_landmarker.task` |
| GPT-SoVITS 基礎模型 | `GPT_SoVITS/pretrained_models/`，依 `GPT_SoVITS/configs/tts_infer.yaml` 及所用模型版本準備 |
| 參考音檔 | `tts.reference.ref_audio_path`，必須改成自己的檔案路徑 |

GPT-SoVITS 可能另需文字處理模型，例如 `GPT_SoVITS/text/G2PWModel/`；請依載入設定與錯誤訊息補齊。保留 `GPT_SoVITS/` 內的 Python 原始碼與設定檔，僅模型資產排除於 Git。

請將 `tts.reference.prompt_text` 改成參考音檔的實際逐字稿。專案相對路徑會以專案根目錄解析；也可使用絕對路徑。設定檔使用 UTF-8。

## 設定

編輯 `setting.yml` 後重新啟動程式：

| 區塊 | 主要設定 |
| --- | --- |
| `live2d` | 模型路徑、表情名稱對應、攝影機與手勢追蹤 |
| `llm` | GGUF 路徑、GPU 層數、上下文長度與生成參數 |
| `chat` | 角色提示詞、發言者名稱、歷史回合數及資料庫路徑 |
| `asr` | 語音辨識模型、裝置、名字校正及麥克風收音參數 |
| `tts` | 推論設定、角色權重、參考音檔、語速與加速選項 |
| `output` | 輸出音檔路徑與自動播放 |

攝影機名稱設於 `live2d.camera_tracking.device`，需與 DirectShow 裝置名稱一致；可執行以下指令列出裝置（FFmpeg 可能回傳非零結束碼）：

```powershell
.\ffmpeg.exe -hide_banner -list_devices true -f dshow -i dummy
```

不使用攝影機時，將 `live2d.camera_tracking.enabled` 設為 `false`；只關閉手勢則調整其下的 `gestures.enabled`。不使用語音輸入時，將 `asr.enabled` 設為 `false`。

若改用 CPU，需同時檢查 `llm.n_gpu_layers: 0`、`asr.device: cpu`、`tts.device: cpu` 與 TTS 加速選項；執行速度及模型支援情況依環境而定。

## 啟動與操作

開發與排錯時執行：

```powershell
.\.venv\Scripts\python.exe -X utf8 .\vtuberAI.py
```

日常使用可雙擊 `start.bat`。啟動器使用 `.venv\Scripts\pythonw.exe`，不顯示終端；載入失敗時查看 `logs/vtuber.log`。

1. 等待模型載入完成，開啟 Live2D 與控制視窗。
2. 在文字欄輸入內容，按 Enter 或「傳送文字」。
3. 使用「開始錄音」進行語音對話；可手動「結束錄音並送出」或「取消錄音」。
4. 「開始新對話」會切換至新對話階段，舊紀錄仍保留在資料庫。
5. 使用「測試中指」確認表情對應；按「結束程式」離開。

## 專案結構

```text
ai-vtuber/
├── vtuberAI.py         # 主程式、控制視窗、模型與對話流程
├── hand_gestures.py    # MediaPipe 手勢判斷
├── virtual_camera.py   # FFmpeg DirectShow 攝影機讀取
├── start_vtuber.pyw    # 無終端啟動與日誌
├── start.bat           # Windows 啟動入口
├── setting.yml         # 共用設定
├── requirements.txt    # Python 套件版本
├── GPT_SoVITS/         # 語音合成原始碼與設定
├── models/             # 本機模型與 Live2D 素材（忽略）
├── data/               # 對話資料庫（忽略）
├── logs/               # 日誌（忽略）
└── output.wav          # 最新合成語音，同名覆寫（忽略）
```

## Git 上傳範圍

`.gitignore` 排除模型資料夾與常見權重格式、Live2D 模型、音訊與影片、對話資料庫、訓練及推論產物、暫存與下載快取、虛擬環境、日誌、執行檔及本機機密設定。`GPT_SoVITS/AR/models/` 等程式碼目錄仍可正常提交。

`setting.yml` 與推論設定檔保留於版本控制；提交前請確認沒有私人內容或不適合公開的路徑。若自行變更輸出位置或增加新格式，需同步更新 `.gitignore`；Git 忽略規則無法自動判定所有檔案用途。

初始化 Git 後，提交前可檢查：

```powershell
git status --short --ignored
git check-ignore -v models/llm/openher_4b_q4.gguf output.wav data/chat_history.sqlite3 logs/vtuber.log
git ls-files -ci --exclude-standard
git diff --cached --stat
```

`git ls-files -ci --exclude-standard` 若有輸出，表示有已追蹤檔案符合忽略規則；需逐一確認後以 `git rm --cached -- <檔案路徑>` 移出索引，本機檔案會保留。`.gitignore` 不會移除既有提交歷史中的檔案。

## 常見問題

- **找不到模型或音檔**：核對 `setting.yml`、TTS 推論設定與實際路徑，確認已準備所有引用資產。
- **沒有 GPU offload 支援**：安裝具 GPU 支援的 `llama-cpp-python`，或使用 `llm.n_gpu_layers: 0`。
- **攝影機無畫面**：確認根目錄有 `ffmpeg.exe`、裝置名稱正確，並檢查裝置是否支援目前讀取程式要求的 YUYV422、640×360、30 FPS。
- **錄不到聲音**：確認 Windows 預設麥克風或 `asr.microphone.device`，再調整 `threshold` 與靜音判定時間。
- **表情未觸發**：確認 `emotion_map` 對應到 Live2D 模型實際宣告的表情名稱。

第三方程式碼、模型、角色與聲音素材的使用及散布，請遵守各自授權。
