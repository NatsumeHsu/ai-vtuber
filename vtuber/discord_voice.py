"""Discord 雙向語音：接收、分段、辨識與回覆播放。"""
import asyncio
from dataclasses import dataclass, field
import math
import queue
import threading
import time

import numpy as np
from scipy.signal import resample_poly


def to_asr_samples(pcm):
    stereo = np.frombuffer(pcm, dtype='<i2').reshape(-1, 2)
    mono = stereo.astype(np.float32).mean(axis=1) / 32768.0
    return np.asarray(resample_poly(mono, 1, 3), dtype=np.float32)


def to_discord_pcm(audio, sample_rate):
    if sample_rate <= 0:
        raise ValueError('語音取樣率必須大於零')
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        audio = np.repeat(audio[:, None], 2, axis=1)
    if audio.ndim != 2 or audio.shape[1] != 2:
        raise ValueError('Discord 輸出必須為單聲道或雙聲道')
    divisor = math.gcd(int(sample_rate), 48000)
    if sample_rate != 48000:
        audio = resample_poly(audio, 48000 // divisor, int(sample_rate) // divisor, axis=0)
    audio = np.clip(np.nan_to_num(audio), -1, 1)
    return (audio * 32767).astype('<i2').tobytes()


@dataclass
class VoiceTurn:
    user_id: int
    speaker: str
    last_voice: float
    voiced_seconds: float = 0
    byte_count: int = 0
    chunks: list = field(default_factory=list)


class VoiceTurnBuffer:
    """依成員分開保存 48 kHz stereo PCM，停頓後才送出一段。"""
    def __init__(self, settings):
        self.threshold = float(settings.get('speech_threshold', 0.015))
        self.silence = float(settings.get('silence_seconds', 0.8))
        self.minimum = float(settings.get('min_speech_seconds', 0.25))
        self.maximum_bytes = int(float(settings.get('max_utterance_seconds', 20)) * 48000 * 4)
        self.max_speakers = int(settings.get('max_speakers', 16))
        if min(self.threshold, self.silence, self.minimum, self.maximum_bytes, self.max_speakers) <= 0:
            raise ValueError('Discord 語音分段參數必須大於零')
        self.turns = {}
        self.lock = threading.Lock()

    def feed(self, user_id, speaker, pcm, now=None):
        if not pcm or len(pcm) % 4:
            return
        now = time.monotonic() if now is None else now
        values = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
        voiced = float(np.sqrt(np.mean(values * values))) >= self.threshold
        with self.lock:
            turn = self.turns.get(user_id)
            if turn is None:
                if not voiced or len(self.turns) >= self.max_speakers:
                    return
                turn = self.turns[user_id] = VoiceTurn(user_id, speaker, now)
            if turn.byte_count >= self.maximum_bytes:
                return
            turn.chunks.append(bytes(pcm))
            turn.byte_count += len(pcm)
            if voiced:
                turn.last_voice = now
                turn.voiced_seconds += len(pcm) / (48000 * 4)

    def flush_due(self, now=None):
        now = time.monotonic() if now is None else now
        ready = []
        with self.lock:
            for key, turn in list(self.turns.items()):
                if now - turn.last_voice >= self.silence or turn.byte_count >= self.maximum_bytes:
                    del self.turns[key]
                    if turn.voiced_seconds >= self.minimum:
                        ready.append((turn.user_id, turn.speaker, b''.join(turn.chunks)))
        return ready


class DiscordVoiceService:
    def __init__(self, settings, transcribe, on_transcript, on_status):
        self.settings = settings
        self.token = str(settings.get('bot_token', '')).strip()
        self.guild_id = int(settings.get('guild_id', 0))
        self.channel_id = int(settings.get('voice_channel_id', 0))
        if not self.token or min(self.guild_id, self.channel_id) <= 0:
            raise ValueError('請在 setting.yml 的 discord 填入 bot_token、guild_id 與 voice_channel_id')
        self.transcribe = transcribe
        self.on_transcript = on_transcript
        self.on_status = on_status
        self.buffer = VoiceTurnBuffer(settings)
        self.audio_jobs = queue.Queue()
        self.stop_event = threading.Event()
        self.loop = self.shutdown = self.voice = self.client = None
        self.thread = self.asr_thread = None

    def start(self):
        # Fail before creating threads if the pinned voice receive dependency is missing.
        from discord.ext.voice_recv.opus import PacketDecoder
        if not hasattr(PacketDecoder, '_dave_decrypt'):
            raise ImportError('Discord 收音套件缺少 DAVE 支援，請依 requirements.txt 安裝指定版本')
        self.asr_thread = threading.Thread(target=self._transcribe_loop, name='discord-asr', daemon=True)
        self.thread = threading.Thread(target=self._run, name='discord-voice', daemon=True)
        self.asr_thread.start()
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.loop and self.shutdown and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self.shutdown.set)
            except RuntimeError:
                pass  # The connection thread may have just closed its loop.
        for thread in (self.thread, self.asr_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=5)

    def _transcribe_loop(self):
        while not self.stop_event.is_set():
            try:
                user_id, speaker, pcm = self.audio_jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                text = self.transcribe(to_asr_samples(pcm)).strip()
                if text and not self.stop_event.is_set():
                    self.on_transcript(user_id, speaker, text)
            except Exception:
                self.on_status('Discord 語音辨識失敗，此段未加入回覆佇列')
            finally:
                self.audio_jobs.task_done()

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception as exc:
            # Never include exception text or authentication headers in the UI.
            self.on_status(f'Discord 連線結束：{type(exc).__name__}，請檢查 Bot 設定、權限及網路')
        finally:
            self.stop_event.set()

    async def _serve(self):
        import discord
        from discord.ext import voice_recv
        self.loop = asyncio.get_running_loop()
        self.shutdown = asyncio.Event()
        intents = discord.Intents.none()
        intents.guilds = intents.voice_states = True
        self.client = discord.Client(intents=intents)
        service = self

        class ReceiveSink(voice_recv.AudioSink):
            def wants_opus(self):
                return False

            def write(self, user, data):
                if user is not None and not getattr(user, 'bot', False) and not service.stop_event.is_set():
                    service.buffer.feed(user.id, getattr(user, 'display_name', user.name), data.pcm)

            def cleanup(self):
                pass

        async def maintain():
            await self.client.wait_until_ready()
            next_join = 0
            next_listen = 0
            while not self.stop_event.is_set():
                if (self.voice is None or not self.voice.is_connected()) and time.monotonic() >= next_join:
                    next_join = time.monotonic() + max(1, float(self.settings.get('reconnect_seconds', 5)))
                    try:
                        if self.voice is not None:
                            await self.voice.disconnect(force=True)
                        channel = self.client.get_channel(self.channel_id) or await self.client.fetch_channel(self.channel_id)
                        if not isinstance(channel, discord.VoiceChannel) or channel.guild.id != self.guild_id:
                            self.on_status('Discord 指定頻道不是該伺服器的普通語音頻道')
                            self.shutdown.set()
                            return
                        self.voice = await channel.connect(cls=voice_recv.VoiceRecvClient,
                                                           self_deaf=False, self_mute=False, timeout=20)
                        self.voice.listen(ReceiveSink())
                        self.on_status('Discord 語音已連線：停頓後辨識，語音回覆優先排隊')
                    except Exception as exc:
                        self.on_status(f'Discord 語音連線失敗：{type(exc).__name__}，稍後重試')
                for turn in self.buffer.flush_due():
                    self.audio_jobs.put(turn)
                if (self.voice is not None and self.voice.is_connected() and not self.voice.is_listening()
                        and time.monotonic() >= next_listen):
                    next_listen = time.monotonic() + max(1, float(self.settings.get('reconnect_seconds', 5)))
                    try:
                        self.voice.listen(ReceiveSink())
                    except Exception:
                        self.on_status('Discord 收音中斷，稍後重新啟動收音')
                await asyncio.sleep(0.05)

        async with self.client:
            login = asyncio.create_task(self.client.start(self.token, reconnect=True))
            worker = asyncio.create_task(maintain())
            stopping = asyncio.create_task(self.shutdown.wait())
            try:
                await asyncio.wait((login, worker, stopping), return_when=asyncio.FIRST_COMPLETED)
                if worker.done():
                    await worker
                if login.done():
                    await login
            finally:
                self.stop_event.set()
                if self.voice is not None:
                    await self.voice.disconnect(force=True)
                worker.cancel()
                stopping.cancel()
                await self.client.close()
                login.cancel()
                await asyncio.gather(login, worker, stopping, return_exceptions=True)

    def play_audio(self, audio, sample_rate, on_level):
        if self.loop is None or self.loop.is_closed() or self.stop_event.is_set():
            raise RuntimeError('Discord 語音未連線')
        pcm = to_discord_pcm(audio, sample_rate)
        future = asyncio.run_coroutine_threadsafe(self._play(pcm, on_level), self.loop)
        try:
            while not self.stop_event.is_set():
                try:
                    return future.result(timeout=0.2)
                except TimeoutError:
                    if future.done():
                        raise
                    continue
            future.cancel()
        finally:
            on_level(0)

    async def _play(self, pcm, on_level):
        import discord
        if self.voice is None or not self.voice.is_connected():
            raise RuntimeError('Discord 語音頻道已中斷')
        completed = self.loop.create_future()

        class PCMSource(discord.AudioSource):
            def __init__(self):
                self.position = 0

            def read(self):
                frame = pcm[self.position:self.position + 3840]
                self.position += len(frame)
                if frame:
                    values = np.frombuffer(frame, dtype='<i2').astype(np.float32) / 32768
                    on_level(float(np.sqrt(np.mean(values * values))))
                    return frame.ljust(3840, b'\0')
                return b''

        def finished(error):
            def notify():
                if not completed.done():
                    if error:
                        completed.set_exception(error)
                    else:
                        completed.set_result(None)
            try:
                self.loop.call_soon_threadsafe(notify)
            except RuntimeError:
                pass  # Playback can finish just after the service loop closes.

        self.voice.play(PCMSource(), after=finished)
        try:
            await completed
        finally:
            if not completed.done() or completed.cancelled():
                self.voice.stop_playing()
