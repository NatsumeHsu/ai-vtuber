"""語音辨識、文字前處理、語音生成與播放。"""
import json
import numpy as np
import re
import threading


class SpeechMixin:
    """語音辨識、文字前處理、語音生成與播放。"""

    def synthesize(self, text):
        """放在 AI 背景執行緒呼叫；保持同步播放，結束後才回到麥克風收音。"""
        import soundfile as sf
        if self.stop.is_set():
            return
        emotion, spoken = self.split_dialogue(text, self.emotions)
        if not spoken:
            return
        output = self.output
        from .emotion_middleware import EmotionAnalyzer
        analyzer = EmotionAnalyzer(self.llm, self.tts_settings.get('emotion_analysis', {}))
        with self.model_lock:
            self.last_emo_vector = analyzer.analyze(spoken)
            self.last_emotion_error = analyzer.last_error
            print('TTS emo_vector:', self.last_emo_vector, flush=True)
            self.tts.synthesize(spoken, output, emo_vector=self.last_emo_vector)
        audio, sample_rate = sf.read(str(output), dtype='float32')
        if not audio.size:
            raise RuntimeError('Index-TTS 產生空音訊')
        remote = self.reply_destination == 'discord' and self.discord is not None
        play = self.auto_play and (not remote or self.settings.get('discord', {}).get('local_playback', False))
        if (play or remote) and (not self.stop.is_set()):
            self.commands.put(('emotion', emotion))
            try:
                if remote:
                    self.discord.play_audio(audio, sample_rate,
                        lambda level: setattr(self, 'mouth', min(1.0, level * self.mouth_gain)))
                if play:
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

    def split_dialogue(self, text, mapping=None):
        mapping = {} if mapping is None else mapping
        text = text.strip()
        prefix = re.match(r'^(?:\s*\[[a-z_]+\])+', text)
        emotion = list(dict.fromkeys(key for key in re.findall(r'\[([a-z_]+)\]', prefix[0]) if key in mapping)) if prefix else []
        text = re.sub('\\[[^\\]]*\\]|【[^】]*】', '', text)
        text = re.sub('\\([^)]*\\)|（[^）]*）|\\*{1,2}[^*]*\\*{1,2}|<[^>]*>', '', text, flags=re.S)
        return (emotion, text.strip())

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
        return self.recognize_samples(samples)

    def recognize_samples(self, samples):
        """麥克風與 Discord 共用 16 kHz 單聲道辨識，模型操作序列化。"""
        with self.model_lock:
            if self.stop.is_set():
                return ''
            result = self.recognizer.generate(input=np.asarray(samples, dtype=np.float32), cache={}, language=self.asr_settings['language'], use_itn=self.asr_settings['use_itn'], batch_size_s=self.asr_settings['batch_size_s'])
        text = ''.join((item.get('text', '') for item in result))
        return self.converter.convert(re.sub('<\\|.*?\\|>', '', text).strip())

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
