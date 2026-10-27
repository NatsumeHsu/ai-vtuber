"""原生 Live2D 視窗、白名單表情與 Index-TTS 音量嘴型；無 API。"""
from contextlib import closing
import json
import numpy as np
from pathlib import Path
import queue
import re
import sqlite3
import threading
import traceback
import time

class VtuberAI:
    """整合 Live2D、語音辨識、名字校正、對話與語音合成。"""

    def __init__(self, settings_path=None):
        self.root = Path(__file__).resolve().parent
        default_settings = self.root / 'setting.yaml'
        if not default_settings.is_file():
            default_settings = self.root / 'setting.yml'
        self.settings_path = default_settings if settings_path is None else Path(settings_path).resolve()
        self.twitch = None
        self.llm = None
        self.tts = None
        self.recognizer = None
        self.converter = None
        self.history = []
        self.settings = self.load_settings()
        self.llm_settings = self.settings['llm']
        self.chat_settings = self.settings['chat']
        self.tts_settings = self.settings['tts']
        self.output_settings = self.settings['output']
        self.asr_settings = self.settings['asr']
        self.live2d_settings = self.settings['live2d']
        self.gguf_model = self.llm_settings['model_path']
        self.n_gpu_layers = self.llm_settings['n_gpu_layers']
        self.n_ctx = self.llm_settings['n_ctx']
        self.max_tokens = self.llm_settings['max_tokens']
        self.temperature = self.llm_settings['temperature']
        self.system_prompt = self.chat_settings['system_prompt']
        self.history_turns = self.chat_settings['history_turns']
        self.auto_play = self.output_settings['auto_play']
        self.output_path = self.output_settings['path']
        self.speaker_name = self.chat_settings['speaker_name']
        self._init_view(self.project_path(self.live2d_settings['model_path']), size=(800, 900), scale=1.0, mouth_gain=6.0, emotion_map=self.live2d_settings['emotion_map'])
        self.database_path = self.project_path(self.chat_settings['database_path'])
        self.init_database()
        self.control_window = None
        self.control_events = queue.Queue()
        self.conversation_jobs = queue.Queue()
        self.recording_cancelled = threading.Event()
        self.recording_finished = threading.Event()
        self.control_busy = False
        self.camera_stop = threading.Event()
        self.camera_thread = None
        self.gaze_target = (0.0, 0.0)
        self.manual_middle_finger = False

    def init_database(self):
        """每次操作建立獨立連線，供主執行緒與 AI 執行緒使用。時間以 UTC 儲存。"""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS chat_sessions ('
                       'id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT '
                       "(strftime('%Y-%m-%dT%H:%M:%fZ','now')))")
            db.execute('CREATE TABLE IF NOT EXISTS chat_turns ('
                       'id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, '
                       'speaker TEXT NOT NULL, user_text TEXT NOT NULL, assistant_text TEXT NOT NULL, '
                       "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), "
                       'FOREIGN KEY(session_id) REFERENCES chat_sessions(id))')
            db.execute('CREATE INDEX IF NOT EXISTS chat_turns_session ON chat_turns(session_id, id)')
            row = db.execute('SELECT id FROM chat_sessions ORDER BY id DESC LIMIT 1').fetchone()
            self.session_id = row[0] if row else db.execute('INSERT INTO chat_sessions DEFAULT VALUES').lastrowid
            rows = db.execute(
                'SELECT speaker, user_text, assistant_text FROM chat_turns '
                'WHERE session_id = ? ORDER BY id DESC LIMIT ?',
                (self.session_id, max(0, self.history_turns)),
            ).fetchall()
        self.history = []
        for speaker, text, reply in reversed(rows):
            self.history.extend([
                {'role': 'user', 'content': json.dumps({'speaker': speaker, 'text': text}, ensure_ascii=False)},
                {'role': 'assistant', 'content': reply},
            ])

    def save_chat_turn(self, speaker, text, reply):
        """一筆交易儲存完整問答，即使後續 TTS 失敗仍保留對話。"""
        with closing(sqlite3.connect(self.database_path)) as db, db:
            db.execute(
                'INSERT INTO chat_turns(session_id, speaker, user_text, assistant_text) VALUES (?, ?, ?, ?)',
                (self.session_id, speaker, text, reply),
            )

    def clear_history(self):
        """開始新對話，保留資料庫中的舊紀錄；重啟後也不恢復舊對話。"""
        with closing(sqlite3.connect(self.database_path)) as db, db:
            session_id = db.execute('INSERT INTO chat_sessions DEFAULT VALUES').lastrowid
        self.session_id = session_id
        self.history.clear()

    def _init_view(self, model_path, size=(800, 900), scale=1.0, mouth_gain=6.0, background=(0.08, 0.08, 0.1, 1.0), emotion_map=None):
        self.path = Path(model_path).resolve()
        self.size, self.scale = (size, scale)
        self.background, self.mouth_gain = (background, mouth_gain)
        self.emotions = dict(emotion_map or {})
        self.commands = queue.Queue()
        self.stop = threading.Event()
        self.mouth = 0.0
        self.worker_error = None

    def synthesize(self, text):
        """放在 AI 背景執行緒呼叫；保持同步播放，結束後才回到麥克風收音。"""
        import soundfile as sf
        if self.stop.is_set():
            return
        emotion, spoken = self.split_dialogue(text, self.emotions)
        if not spoken:
            return
        output = self.output
        from emotion_middleware import EmotionAnalyzer
        analyzer = EmotionAnalyzer(self.llm, self.tts_settings.get('emotion_analysis', {}))
        self.last_emo_vector = analyzer.analyze(spoken)
        self.last_emotion_error = analyzer.last_error
        print('TTS emo_vector:', self.last_emo_vector, flush=True)
        self.tts.synthesize(spoken, output, emo_vector=self.last_emo_vector)
        audio, sample_rate = sf.read(str(output), dtype='float32')
        if not audio.size:
            raise RuntimeError('Index-TTS 產生空音訊')
        play = self.auto_play
        if play and (not self.stop.is_set()):
            self.commands.put(('emotion', emotion))
            try:
                self.play(audio, sample_rate)
            finally:
                self.mouth = 0.0
                self.commands.put(('emotion', None))

    def play(self, audio, sample_rate):
        import sounddevice as sd
        audio = np.asarray(audio)
        if np.issubdtype(audio.dtype, np.integer):
            audio = audio.astype(np.float32) / (np.iinfo(audio.dtype).max + 1.0)
        else:
            audio = audio.astype(np.float32)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        audio = np.ascontiguousarray(np.clip(audio, -1, 1))
        position = 0
        finished = threading.Event()

        def callback(outdata, frames, timing, status):
            nonlocal position
            outdata.fill(0)
            if self.stop.is_set() or position >= len(audio):
                self.mouth = 0.0
                raise sd.CallbackStop
            segment = audio[position:position + frames]
            outdata[:len(segment), 0] = segment
            self.mouth = min(1.0, float(np.sqrt(np.mean(segment * segment))) * self.mouth_gain)
            position += len(segment)
        try:
            with sd.OutputStream(samplerate=sample_rate, channels=1, dtype='float32', latency='low', blocksize=max(1, int(sample_rate * 0.02)), callback=callback, finished_callback=finished.set):
                while not finished.wait(0.05):
                    if self.stop.is_set():
                        break
        finally:
            self.mouth = 0.0

    def _worker(self, function):
        try:
            function()
        except BaseException as exc:
            self.worker_error = exc
            traceback.print_exc()
        finally:
            self.stop.set()

    def _run_view(self, ai_function):
        """必須由主執行緒呼叫，ai_function 會在背景執行。"""
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('Live2DView.run 必須在主執行緒呼叫')
        if not self.path.is_file():
            raise FileNotFoundError(f'找不到 Live2D 模型：{self.path}')
        settings = json.loads(self.path.read_text(encoding='utf-8-sig'))
        names = {item['Name'] for item in settings.get('FileReferences', {}).get('Expressions', [])}
        lips = next((g['Ids'] for g in settings.get('Groups', []) if g.get('Name') == 'LipSync' and g.get('Ids')), ['ParamMouthOpenY'])
        print('Live2D 表情：', ', '.join(sorted(names)) or '無')
        import pygame
        import live2d
        pygame.display.init()
        model, set_param = (None, None)
        initialized = False
        try:
            pygame.display.set_mode(self.size, pygame.DOUBLEBUF | pygame.OPENGL)
            pygame.display.set_caption('白璃 HAKURI')
            live2d.init()
            initialized = True
            init_gl = getattr(live2d, 'glInit', None) or getattr(live2d, 'glewInit')
            init_gl()
            model = live2d.Model()
            model.LoadModelJson(str(self.path))
            model.Resize(*self.size)
            model.SetScale(self.scale)
            model.ResetExpression()
            set_param = getattr(model, 'SetParameterValue', None) or getattr(model, 'SetParamById')
            tracking = self.live2d_settings.get('camera_tracking', {})
            eye_params = (tracking.get('eye_x_parameter', 'ParamEyeBallX'), tracking.get('eye_y_parameter', 'ParamEyeBallY'))
            param_ids = set(model.GetParamIds())
            if tracking.get('enabled', False):
                if not set(eye_params).issubset(param_ids):
                    self.post_control_event('log', '模型缺少設定中的眼球參數')
                else:
                    self.camera_thread = threading.Thread(target=self.track_camera, daemon=True)
                    self.camera_thread.start()
            threading.Thread(target=self._worker, args=(ai_function,), daemon=True).start()
            clock = pygame.time.Clock()
            smooth = 0.0
            gaze_x = gaze_y = 0.0
            expression_layers = {'emotion': set(), 'gesture': set(), 'manual': set()}
            applied_expressions = set()
            while not self.stop.is_set():
                if self.control_window is not None:
                    self.update_control_window()
                    if self.stop.is_set():
                        break
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.stop.set()
                    elif event.type == pygame.MOUSEMOTION and not tracking.get('enabled', False):
                        model.Drag(*event.pos)
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        self.stop.set()
                while True:
                    try:
                        kind, value = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    if kind in expression_layers:
                        keys = [value] if isinstance(value, str) else (value or [])
                        expression_layers[kind] = {self.emotions[key] for key in keys if key in self.emotions}
                desired = set().union(*expression_layers.values()) & names
                for name in sorted(applied_expressions - desired):
                    model.RemoveExpression(name)
                for name in sorted(desired - applied_expressions):
                    model.AddExpression(name)
                applied_expressions = desired
                live2d.clearBuffer(*self.background)
                model.Update()
                if tracking.get('enabled', False):
                    alpha = min(1.0, max(0.01, float(tracking.get('smoothing', 0.15))))
                    target_x, target_y = self.gaze_target
                    gaze_x += (target_x - gaze_x) * alpha
                    gaze_y += (target_y - gaze_y) * alpha
                    for param, value in zip(eye_params, (gaze_x, gaze_y)):
                        if param in param_ids:
                            set_param(param, value)
                    self.apply_head_tracking(model, set_param, param_ids, gaze_x, gaze_y)
                smooth += (self.mouth - smooth) * 0.5
                for param in lips:
                    set_param(param, smooth)
                model.Draw()
                pygame.display.flip()
                clock.tick(60)
        except KeyboardInterrupt:
            self.stop.set()
        finally:
            self.stop.set()
            set_param = None
            if model is not None:
                del model
            if initialized:
                live2d.dispose()
            pygame.quit()
            tts = self.tts
            if tts is not None:
                tts.close()
            self.camera_stop.set()
            if self.camera_thread is not None:
                self.camera_thread.join(timeout=2)
            if self.control_window is not None:
                self.control_window.destroy()
                self.control_window = None
        if self.worker_error is not None:
            raise RuntimeError('AI 背景工作失敗，請查看上方錯誤') from self.worker_error

    def split_dialogue(self, text, mapping=None):
        mapping = {} if mapping is None else mapping
        text = text.strip()
        prefix = re.match(r'^(?:\s*\[[a-z_]+\])+', text)
        emotion = list(dict.fromkeys(key for key in re.findall(r'\[([a-z_]+)\]', prefix[0]) if key in mapping)) if prefix else []
        text = re.sub('\\[[^\\]]*\\]|【[^】]*】', '', text)
        text = re.sub('\\([^)]*\\)|（[^）]*）|\\*{1,2}[^*]*\\*{1,2}|<[^>]*>', '', text, flags=re.S)
        return (emotion, text.strip())

    def load_settings(self):
        import yaml
        path = self.settings_path
        try:
            settings = yaml.safe_load(path.read_text(encoding='utf-8'))
        except yaml.YAMLError as exc:
            raise ValueError(f'設定檔格式錯誤：{path}\n{exc}') from exc
        if not isinstance(settings, dict):
            raise ValueError('setting.yml 必須包含設定分類')
        for section in ('llm', 'chat', 'output', 'tts', 'asr', 'live2d'):
            if not isinstance(settings.get(section), dict):
                raise ValueError(f'setting.yml 缺少設定分類：{section}')
        return settings

    def project_path(self, path):
        path = Path(path)
        return path if path.is_absolute() else self.root / path

    def init_asr(self):
        import torch
        from funasr import AutoModel
        from opencc import OpenCC
        device = self.asr_settings['device']
        if device.startswith('cuda') and (not torch.cuda.is_available()):
            raise RuntimeError('PyTorch CUDA 不可用')
        model_dir = self.project_path(self.asr_settings['model_dir'])
        for name in ('model.pt', 'config.yaml'):
            path = model_dir / name
            if not path.is_file():
                raise FileNotFoundError(f'缺少 FunASR 模型：{path}')
        print(f'載入 FunASR，裝置：{device}')
        model = AutoModel(model=str(model_dir.resolve()), device=device, ncpu=self.asr_settings['ncpu'], disable_update=True, trust_remote_code=False)
        return (model, OpenCC(self.asr_settings['conversion']))

    def record_utterance(self):
        """依音量偵測開口與停頓；僅在這個函式執行時開啟麥克風。"""
        from collections import deque
        import math
        import numpy as np
        import sounddevice as sd
        mic = self.asr_settings['microphone']
        rate = mic['sample_rate']
        if rate != 16000:
            raise ValueError('asr.microphone.sample_rate 必須為 16000')
        for key in ('block_ms', 'threshold', 'silence_seconds', 'wait_seconds', 'max_seconds'):
            if mic[key] <= 0:
                raise ValueError(f'asr.microphone.{key} 必須大於 0')
        if mic['pre_roll_seconds'] < 0:
            raise ValueError('asr.microphone.pre_roll_seconds 不可小於 0')
        block = max(1, round(rate * mic['block_ms'] / 1000))
        step = block / rate
        pre_roll = deque(maxlen=max(1, math.ceil(mic['pre_roll_seconds'] / step)))
        frames = []
        started = False
        waiting = elapsed = silence = 0.0
        print('請說話……（Ctrl+C 結束程式）')
        self.post_control_event('status', '錄音中，說完停頓後會自動送出')
        with sd.InputStream(samplerate=rate, channels=1, dtype='float32', blocksize=block, device=mic['device']) as stream:
            while True:
                if self.stop.is_set() or self.recording_cancelled.is_set():
                    return np.empty(0, dtype=np.float32)
                if self.recording_finished.is_set():
                    break
                data, overflowed = stream.read(block)
                if overflowed:
                    raise RuntimeError('麥克風錄音溢位，請重新錄音')
                chunk = data[:, 0].copy()
                voiced = float(np.sqrt(np.mean(chunk * chunk))) >= mic['threshold']
                if not started:
                    pre_roll.append(chunk)
                    waiting += step
                    if not voiced:
                        if waiting >= mic['wait_seconds']:
                            return np.empty(0, dtype=np.float32)
                        continue
                    started = True
                    frames.extend(pre_roll)
                else:
                    frames.append(chunk)
                elapsed += step
                silence = 0.0 if voiced else silence + step
                if silence >= mic['silence_seconds'] or elapsed >= mic['max_seconds']:
                    break
        return np.concatenate(frames) if frames else np.empty(0, dtype=np.float32)

    def correct_asr_names(self, text):
        """第一層校正不加入角色提示詞或對話歷史；失敗時保留辨識原文。"""
        import json
        correction = self.asr_settings['llm_correction']
        if not correction['enabled'] or not text:
            return text
        try:
            result = self.llm.create_chat_completion(messages=[{'role': 'system', 'content': correction['system_prompt']}, {'role': 'user', 'content': json.dumps({'text': text}, ensure_ascii=False)}], temperature=correction['temperature'], max_tokens=correction['max_tokens'], response_format={'type': 'json_object'})
            choice = result['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise ValueError('校正輸出超過 token 上限')
            data = json.loads(choice['message']['content'])
            corrected = data.get('text') if isinstance(data, dict) else None
            if not isinstance(corrected, str) or not corrected.strip():
                raise ValueError('校正結果缺少 text')
            return corrected.strip()
        except Exception as exc:
            print(f'名字校正失敗，保留辨識原文：{exc}')
            return text

    def recognize_microphone(self):
        import numpy as np
        samples = self.record_utterance()
        if not samples.size:
            print('未偵測到說話，請再試一次。')
            return ''
        self.post_control_event('status', '辨識語音中……')
        result = self.recognizer.generate(input=np.asarray(samples, dtype=np.float32), cache={}, language=self.asr_settings['language'], use_itn=self.asr_settings['use_itn'], batch_size_s=self.asr_settings['batch_size_s'])
        text = ''.join((item.get('text', '') for item in result))
        return self.converter.convert(re.sub('<\\|.*?\\|>', '', text).strip())

    def generate_reply(self, text, speaker_name=None):
        """保留短期記憶，上下文不足時移除最舊對話。"""
        llm, history = self.llm, self.history
        recent = history[-self.history_turns * 2:] if self.history_turns > 0 else []
        speaker = self.speaker_name if speaker_name is None else speaker_name
        if not isinstance(speaker, str) or not speaker.strip():
            raise ValueError('發言者名稱不可為空')
        user_message = {'role': 'user', 'content': json.dumps({'speaker': speaker.strip(), 'text': text}, ensure_ascii=False)}
        while True:
            messages = [{'role': 'system', 'content': self.system_prompt}, *recent, user_message]
            tokens = llm.tokenize('\n'.join((m['content'] for m in messages)).encode('utf-8'))
            estimated = len(tokens) + len(messages) * 16 + 128 + self.max_tokens
            if estimated <= self.n_ctx:
                break
            if not recent:
                raise ValueError('輸入太長，請縮短文字或增加 N_CTX')
            recent = recent[2:]
        result = llm.create_chat_completion(messages=messages, temperature=self.temperature, max_tokens=self.max_tokens)
        reply = result['choices'][0]['message']['content'] or ''
        reply = re.sub('<think>.*?</think>', '', reply, flags=re.S)
        reply = reply.split('<think>', 1)[0].strip()
        if not reply:
            raise RuntimeError('模型沒有產生回覆，請確認聊天模板或增加 MAX_TOKENS')
        self.save_chat_turn(speaker.strip(), text, reply)
        if self.history_turns > 0:
            history[:] = (recent + [user_message, {'role': 'assistant', 'content': reply}])[-self.history_turns * 2:]
        return reply

    def preprocess_tts_text(self, text):
        """僅處理朗讀文字，保留終端顯示與對話歷史的原始回覆。"""
        import unicodedata
        options = self.tts_settings.get('tts_preprocessor_config', {})
        pairs = {}
        if options.get('ignore_brackets', False):
            pairs.update({'[': ']', '【': '】', '［': '］'})
        if options.get('ignore_parentheses', False):
            pairs.update({'(': ')', '（': '）'})
        if options.get('ignore_angle_brackets', False):
            pairs.update({'<': '>', '＜': '＞', '〈': '〉', '《': '》'})
        stack = []
        ranges = []
        for index, char in enumerate(text):
            if char in pairs:
                stack.append((char, index))
            elif stack and char == pairs[stack[-1][0]]:
                _, start = stack.pop()
                ranges.append((start, index + 1))
        hidden = set()
        for start, end in ranges:
            hidden.update(range(start, end))
        text = ''.join((char for index, char in enumerate(text) if index not in hidden))
        if options.get('ignore_asterisks', False):
            text = re.sub('\\*\\*[^*]+\\*\\*|(?<!\\*)\\*[^*]+\\*(?!\\*)', '', text)
        if options.get('remove_special_char', False):
            text = ''.join((char for char in text if char.isspace() or (unicodedata.category(char)[0] in 'LNMP' and (not 65024 <= ord(char) <= 65039) and (not 917760 <= ord(char) <= 917999) and (ord(char) != 8419))))
        return re.sub('\\s+', ' ', text).strip()

    def initialize_models(self):
        from runtime_support import prepare_runtime
        prepare_runtime()
        from index_tts_client import IndexTTSClient
        from llama_cpp import Llama, llama_supports_gpu_offload
        if self.n_gpu_layers != 0 and not llama_supports_gpu_offload():
            raise RuntimeError('目前的 llama-cpp-python 沒有 GPU 加速支援')
        if not self.project_path(self.gguf_model).is_file():
            raise FileNotFoundError(self.project_path(self.gguf_model))
        self.recognizer, self.converter = self.init_asr() if self.asr_settings['enabled'] else (None, None)
        if self.stop.is_set():
            return
        print('載入 Index-TTS……')
        self.tts = IndexTTSClient(self.root, self.tts_settings)
        if self.stop.is_set():
            self.tts.close()
            return
        print('載入 GGUF……')
        self.llm = Llama(model_path=str(self.project_path(self.gguf_model)), n_gpu_layers=self.n_gpu_layers, n_ctx=self.n_ctx, verbose=self.llm_settings['verbose'])
        self.output = self.project_path(self.output_path)
        self.output.parent.mkdir(parents=True, exist_ok=True)

    def conversation_loop(self):
        if self.control_window is not None:
            self.control_conversation_loop()
            return
        self.speaker_name = self.chat_settings['speaker_name']
        print('準備完成。輸入 /clear 清除記憶，/quit 結束。')
        print(f'目前發言者：{self.speaker_name}；輸入 /name 名字 可切換發言者。')
        if self.recognizer is not None:
            print('按 Enter 開始麥克風錄音，或直接輸入文字對話。')
        try:
            while True:
                text = input(f'\n{self.speaker_name}：').strip()
                if text == '/quit':
                    break
                if text == '/clear':
                    self.clear_history()
                    print('已清除記憶')
                    continue
                if text == '/name' or text.startswith('/name '):
                    name = text[len('/name'):].strip()
                    if name:
                        self.speaker_name = name
                        print(f'發言者已切換為：{self.speaker_name}')
                    else:
                        print('用法：/name 名字')
                    continue
                try:
                    if not text and self.recognizer is not None:
                        text = self.recognize_microphone()
                        if text:
                            print(f'辨識：{text}')
                            text = self.correct_asr_names(text)
                            print(f'校正後：{text}')
                    if not text:
                        continue
                    reply = self.generate_reply(text)
                    print(f'白璃：{reply}')
                    self.synthesize(reply)
                except Exception as exc:
                    print(f'處理失敗：{exc}')
        except (KeyboardInterrupt, EOFError):
            print('\n已結束')

    def close(self):
        self.stop.set()
        if self.twitch is not None:
            self.twitch.close()
            self.twitch = None
        tts, self.tts = self.tts, None
        try:
            if tts is not None:
                tts.close()
        finally:
            if self.llm is not None:
                self.llm.close()
                self.llm = None
            self.recognizer = None
            self.converter = None

    def _ai_worker(self):
        try:
            self.post_control_event('status', '載入模型中……')
            self.initialize_models()
            if self.settings.get('twitch', {}).get('enabled', False) and not self.stop.is_set():
                from twitch_chat import TwitchChat
                try:
                    self.twitch = TwitchChat(self.settings['twitch'], self.enqueue_twitch_message,
                                             lambda text: self.post_control_event('log', text))
                    self.twitch.start()
                except ValueError as exc:
                    self.post_control_event('log', str(exc))
            self.post_control_event('ready', None)
            self.conversation_loop()
        except Exception as exc:
            self.post_control_event('error', str(exc))
            raise
        finally:
            self.close()

    def run(self):
        self.create_control_window()
        try:
            self._run_view(self._ai_worker)
        finally:
            if self.control_window is not None:
                self.control_window.destroy()
                self.control_window = None

    def post_control_event(self, kind, value):
        """背景工作只傳送事件，由主執行緒更新 Tk 元件。"""
        # 控制視窗與原本的終端並存，對話及狀態同步輸出到終端。
        if kind in ('status', 'log', 'error') and value:
            print(value, flush=True)
        self.control_events.put((kind, value))

    def face_to_gaze(self, face, width, height):
        """將人臉中心轉成 Live2D 眼球座標，垂直方向上方為正。"""
        x, y, w, h = face
        config = self.live2d_settings.get('camera_tracking', {})
        gx = 2 * (x + w / 2) / width - 1
        gy = 1 - 2 * (y + h / 2) / height
        if config.get('mirror', True):
            gx = -gx
        gain = float(config.get('gain', 1.5))
        return (max(-1.0, min(1.0, gx * gain)), max(-1.0, min(1.0, gy * gain)))

    def apply_head_tracking(self, model, set_param, param_ids, x, y):
        """以平滑後的人臉位置驅動轉頭，並限制在模型參數範圍內。"""
        config = self.live2d_settings.get('camera_tracking', {})
        if not config.get('head_enabled', False):
            return
        targets = (
            (config.get('head_x_parameter', 'ParamAngleX'), x * float(config.get('head_yaw', 25))),
            (config.get('head_y_parameter', 'ParamAngleY'), y * float(config.get('head_pitch', 15))),
        )
        for param, value in targets:
            if param in param_ids:
                value = max(model.GetParamMinById(param), min(model.GetParamMaxById(param), value))
                set_param(param, value)

    def track_camera(self):
        """影像僅在本機記憶體做人臉定位，不保存或傳送影像。"""
        capture = None
        gestures = None
        gesture_active = False
        gesture_status = None
        try:
            import cv2
            config = self.live2d_settings.get('camera_tracking', {})
            gesture_config = config.get('gestures', {})
            if gesture_config.get('enabled', False):
                try:
                    from hand_gestures import HandGestures
                    gestures = HandGestures(self.project_path(gesture_config['model_path']), gesture_config)
                    self.post_control_event('gesture_status', '手勢：已啟動，請讓整隻手進入鏡頭')
                except Exception as exc:
                    self.post_control_event('log', f'手勢辨識無法啟動：{exc}')
                    self.post_control_event('gesture_status', f'手勢：無法啟動，{exc}')
            detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            if detector.empty():
                raise RuntimeError('無法載入人臉偵測模型')
            device = config.get('device', 0)
            from virtual_camera import VirtualCamera
            def open_camera():
                return VirtualCamera(device, width=int(config.get('width', 640)),
                                     height=int(config.get('height', 360)),
                                     fps=max(1, float(config.get('fps', 30))),
                                     pixel_format=config.get('pixel_format', 'YUY2'))
            capture = open_camera()
            self.post_control_event('camera', '眼睛追蹤：等待人臉')
            last_seen = time.monotonic()
            found_before = False
            interval = 1 / max(1, float(config.get('fps', 15)))
            reconnects = 0
            while not self.stop.is_set() and not self.camera_stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    if self.stop.is_set() or self.camera_stop.is_set():
                        break
                    self.gaze_target = (0.0, 0.0)
                    error = capture.error
                    capture.release()
                    if reconnects < int(config.get('reconnect_attempts', 3)):
                        reconnects += 1
                        self.post_control_event('camera', f'眼睛追蹤：重新連線 {reconnects}，{error}')
                        if self.camera_stop.wait(1) or self.stop.is_set():
                            break
                        capture = open_camera()
                        continue
                    raise RuntimeError(f'攝影機重連失敗：{error}')
                height, width = frame.shape[:2]
                if gestures is not None:
                    try:
                        active = gestures.process(frame)
                        status = (gestures.hand_count, gestures.detected, active)
                        if status != gesture_status:
                            gesture_status = status
                            if active:
                                message = '手勢：已觸發中指'
                            elif gestures.detected:
                                message = '手勢：偵測到中指，保持一下'
                            elif gestures.hand_count:
                                message = f'手勢：看到 {gestures.hand_count} 隻手，尚未符合中指手勢'
                            else:
                                message = '手勢：沒有偵測到手，請讓整隻手進入鏡頭'
                            self.post_control_event('gesture_status', message)
                        if active != gesture_active:
                            gesture_active = active
                            key = gesture_config.get('middle_finger_expression', 'middle_finger')
                            self.commands.put(('gesture', [key] if active else []))
                    except Exception as exc:
                        self.post_control_event('log', f'手勢辨識停止：{exc}')
                        self.post_control_event('gesture_status', f'手勢：辨識停止，{exc}')
                        gestures.close()
                        gestures = None
                        self.commands.put(('gesture', []))
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
                found = len(faces) > 0
                if found:
                    self.gaze_target = self.face_to_gaze(max(faces, key=lambda box: box[2] * box[3]), width, height)
                    last_seen = time.monotonic()
                elif time.monotonic() - last_seen >= float(config.get('lost_seconds', 1.0)):
                    self.gaze_target = (0.0, 0.0)
                if found != found_before:
                    self.post_control_event('camera', '眼睛追蹤：追蹤中' if found else '眼睛追蹤：等待人臉')
                    found_before = found
                self.camera_stop.wait(interval)
        except Exception as exc:
            print(f'攝影機錯誤：{exc}', flush=True)
            self.post_control_event('camera', f'眼睛追蹤停止：{exc}')
        finally:
            self.gaze_target = (0.0, 0.0)
            self.commands.put(('gesture', []))
            if gestures is not None:
                gestures.close()
            if capture is not None:
                capture.release()

    def create_control_window(self):
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText

        window = self.control_window = tk.Tk()
        window.title('白璃｜麥克風對話控制')
        window.geometry('620x560')
        window.minsize(480, 400)
        window.protocol('WM_DELETE_WINDOW', self.stop.set)
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill='both', expand=True)
        self.control_status = tk.StringVar(value='載入模型中……')
        self.control_speaker = tk.StringVar(value=self.speaker_name)
        ttk.Label(frame, text='麥克風對話控制', font=('', 16, 'bold')).pack(anchor='w')
        ttk.Label(frame, textvariable=self.control_status).pack(anchor='w', pady=(8, 12))
        self.camera_status = tk.StringVar(value='眼睛追蹤：啟動中' if self.live2d_settings.get('camera_tracking', {}).get('enabled', False) else '眼睛追蹤：未啟用')
        ttk.Label(frame, textvariable=self.camera_status).pack(anchor='w', pady=(0, 8))
        self.gesture_status = tk.StringVar(value='手勢：等待攝影機啟動')
        ttk.Label(frame, textvariable=self.gesture_status).pack(anchor='w')
        self.gesture_test_button = ttk.Button(frame, text='測試中指：關', command=self.toggle_middle_finger_test)
        self.gesture_test_button.pack(anchor='w', pady=(4, 8))
        row = ttk.Frame(frame)
        row.pack(fill='x')
        ttk.Label(row, text='發言者名稱').pack(side='left')
        self.speaker_entry = ttk.Entry(row, textvariable=self.control_speaker)
        self.speaker_entry.pack(side='left', fill='x', expand=True, padx=(10, 0))
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=12)
        self.record_button = ttk.Button(row, text='開始錄音', command=lambda: self.submit_control_job('microphone'))
        self.record_button.pack(side='left')
        self.finish_button = ttk.Button(row, text='結束錄音並送出', command=self.recording_finished.set, state='disabled')
        self.finish_button.pack(side='left', padx=8)
        self.cancel_button = ttk.Button(row, text='取消錄音', command=self.recording_cancelled.set, state='disabled')
        self.cancel_button.pack(side='left')
        ttk.Label(frame, text='說完停頓後自動送出；播放回覆時不收音。').pack(anchor='w')
        self.control_log = ScrolledText(frame, height=14, wrap='word', state='disabled')
        self.control_log.pack(fill='both', expand=True, pady=12)
        self.control_text = tk.StringVar()
        row = ttk.Frame(frame)
        row.pack(fill='x')
        self.text_entry = ttk.Entry(row, textvariable=self.control_text)
        self.text_entry.pack(side='left', fill='x', expand=True)
        self.text_entry.bind('<Return>', lambda event: self.submit_control_job('text'))
        self.send_button = ttk.Button(row, text='傳送文字', command=lambda: self.submit_control_job('text'))
        self.send_button.pack(side='left', padx=(8, 0))
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(12, 0))
        self.clear_button = ttk.Button(row, text='開始新對話', command=lambda: self.submit_control_job('clear'))
        self.clear_button.pack(side='left')
        ttk.Button(row, text='結束程式', command=self.stop.set).pack(side='right')
        self.set_control_ready(False)

    def toggle_middle_finger_test(self):
        self.manual_middle_finger = not self.manual_middle_finger
        self.commands.put(('manual', ['middle_finger'] if self.manual_middle_finger else []))
        self.gesture_test_button.configure(text='測試中指：開' if self.manual_middle_finger else '測試中指：關')

    def set_control_ready(self, ready):
        self.control_busy = not ready
        for widget in (self.record_button, self.send_button, self.clear_button,
                       self.speaker_entry, self.text_entry):
            widget.configure(state='normal' if ready else 'disabled')
        if not self.asr_settings['enabled']:
            self.record_button.configure(state='disabled')
        self.finish_button.configure(state='disabled')
        self.cancel_button.configure(state='disabled')

    def submit_control_job(self, kind):
        if self.control_busy or self.stop.is_set():
            return
        speaker = self.control_speaker.get().strip()
        if not speaker:
            self.control_status.set('請先填入發言者名稱')
            return
        text = self.control_text.get().strip() if kind == 'text' else ''
        if kind == 'text' and not text:
            return
        self.recording_cancelled.clear()
        self.recording_finished.clear()
        self.set_control_ready(False)
        if kind == 'microphone':
            self.finish_button.configure(state='normal')
            self.cancel_button.configure(state='normal')
        if kind == 'text':
            self.control_text.set('')
        self.control_status.set('處理中……')
        self.conversation_jobs.put((kind, speaker, text))

    def enqueue_twitch_message(self, speaker, text):
        """收訊執行緒只排隊；與手動輸入共用單一推論及播放流程。"""
        if not self.stop.is_set():
            self.conversation_jobs.put(('text', speaker, text))
            self.post_control_event('log', f'Twitch 已排隊：{speaker}：{text}')

    def update_control_window(self):
        while True:
            try:
                kind, value = self.control_events.get_nowait()
            except queue.Empty:
                break
            if kind == 'status':
                self.control_status.set(value)
                if not value.startswith('錄音中'):
                    self.finish_button.configure(state='disabled')
                    self.cancel_button.configure(state='disabled')
            elif kind == 'camera':
                self.camera_status.set(value)
            elif kind == 'gesture_status':
                self.gesture_status.set(value)
            elif kind in ('ready', 'done'):
                self.set_control_ready(True)
                self.control_status.set('準備完成，可以開始錄音或輸入文字')
            elif kind == 'busy':
                self.set_control_ready(False)
            elif kind in ('log', 'error'):
                self.control_log.configure(state='normal')
                self.control_log.insert('end', value + '\n\n')
                self.control_log.see('end')
                self.control_log.configure(state='disabled')
                if kind == 'error':
                    self.control_status.set('處理失敗，請查看下方訊息')
        self.control_window.update_idletasks()
        self.control_window.update()

    def control_conversation_loop(self):
        """所有推論與資料庫寫入依序執行，避免重複錄音或並行操作模型。"""
        while not self.stop.is_set():
            try:
                kind, speaker, text = self.conversation_jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.post_control_event('busy', None)
                self.speaker_name = speaker
                if kind == 'clear':
                    self.clear_history()
                    self.post_control_event('log', '已開始新對話，舊紀錄保留於資料庫。')
                    continue
                if kind == 'microphone':
                    text = self.recognize_microphone()
                    if self.stop.is_set() or self.recording_cancelled.is_set():
                        self.post_control_event('log', '已取消錄音。')
                        continue
                    if not text:
                        self.post_control_event('log', '未偵測到說話，請再試一次。')
                        continue
                    self.post_control_event('log', f'語音辨識：{text}')
                    self.post_control_event('status', '校正名字中……')
                    text = self.correct_asr_names(text)
                if self.stop.is_set():
                    break
                self.post_control_event('log', f'{speaker}：{text}')
                self.post_control_event('status', '白璃正在思考……')
                reply = self.generate_reply(text, speaker)
                self.post_control_event('log', f'白璃：{reply}')
                if not self.stop.is_set():
                    self.post_control_event('status', '合成與播放語音中……')
                    self.synthesize(reply)
            except Exception as exc:
                traceback.print_exc()
                self.post_control_event('error', str(exc))
            finally:
                self.post_control_event('done', None)

if __name__ == '__main__':
    VtuberAI().run()
