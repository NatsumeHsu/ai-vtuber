"""Discord audio and priority scheduling tests; no account or network required."""
import asyncio
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import yaml
from vtuber import VtuberAI
from vtuber.discord_voice import DiscordVoiceService, VoiceTurnBuffer, to_asr_samples, to_discord_pcm


def voice_frame():
    return (np.ones(1920, dtype='<i2') * 8192).tobytes()


class AudioTests(unittest.TestCase):
    def test_resample_formats_and_finite_audio(self):
        mono = np.ones(1600, dtype=np.float32) * 0.25
        pcm = to_discord_pcm(mono, 16000)
        self.assertEqual(len(pcm), 4800 * 4)
        samples = to_asr_samples(pcm)
        self.assertEqual(samples.shape, (1600,))
        self.assertTrue(np.isfinite(samples).all())
        self.assertAlmostEqual(float(samples[100:-100].mean()), 0.25, places=3)

    def test_turns_separate_speakers_and_wait_for_silence(self):
        buffer = VoiceTurnBuffer({'silence_seconds': 0.4, 'min_speech_seconds': 0.02})
        buffer.feed(1, '甲', voice_frame(), now=0)
        buffer.feed(2, '乙', voice_frame(), now=0.1)
        self.assertEqual(buffer.flush_due(now=0.3), [])
        first = buffer.flush_due(now=0.45)
        self.assertEqual([(row[0], row[1]) for row in first], [(1, '甲')])
        self.assertEqual(buffer.flush_due(now=0.6)[0][:2], (2, '乙'))

    def test_silence_is_ignored_and_utterance_size_is_bounded(self):
        buffer = VoiceTurnBuffer({'min_speech_seconds': 0.02, 'max_utterance_seconds': 0.04})
        buffer.feed(1, '甲', bytes(3840), now=0)
        self.assertEqual(buffer.flush_due(now=10), [])
        for index in range(10):
            buffer.feed(1, '甲', voice_frame(), now=index * 0.02)
        self.assertEqual(len(buffer.flush_due(now=0.2)[0][2]), 7680)

    def test_real_opus_codec_and_dave_receiver_available(self):
        import discord
        from discord.ext.voice_recv.opus import PacketDecoder
        self.assertTrue(hasattr(PacketDecoder, '_dave_decrypt'))
        packet = discord.opus.Encoder().encode(voice_frame(), 960)
        self.assertEqual(len(discord.opus.Decoder().decode(packet, fec=False)), 3840)

    def test_playback_waits_for_completion_and_pads_frames(self):
        async def run():
            service = DiscordVoiceService({'bot_token': 'test', 'guild_id': 1, 'voice_channel_id': 2},
                                          lambda _: '', lambda *args: None, lambda _: None)
            service.loop = asyncio.get_running_loop()
            frames = []

            class Voice:
                def is_connected(self):
                    return True

                def play(self, source, after):
                    while frame := source.read():
                        frames.append(frame)
                    after(None)

            service.voice = Voice()
            await service._play(voice_frame() + voice_frame()[:400], lambda _: None)
            self.assertEqual([len(frame) for frame in frames], [3840, 3840])
        asyncio.run(run())

    def test_connection_lifecycle_and_receive_sink_without_network(self):
        import discord
        from types import SimpleNamespace
        async def run():
            service = DiscordVoiceService({'bot_token': 'test', 'guild_id': 1, 'voice_channel_id': 2,
                                            'min_speech_seconds': 0.02, 'silence_seconds': 0.02},
                                          lambda _: '', lambda *args: None, lambda _: None)
            voice = Mock()
            voice.disconnect = AsyncMock()
            voice.is_connected.return_value = True
            voice.is_listening.return_value = True
            channel = Mock(spec=discord.VoiceChannel)
            channel.guild = SimpleNamespace(id=1)
            channel.connect = AsyncMock(return_value=voice)

            class Client:
                def __init__(self, **kwargs):
                    self.ready = asyncio.Event()
                    self.closed = asyncio.Event()

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                async def start(self, token, **kwargs):
                    self.ready.set()
                    await self.closed.wait()

                async def wait_until_ready(self):
                    await self.ready.wait()

                def get_channel(self, channel_id):
                    return channel

                async def close(self):
                    self.closed.set()

            def status(text):
                if '已連線' in text:
                    sink = voice.listen.call_args.args[0]
                    sink.write(SimpleNamespace(id=42, name='viewer', display_name='觀眾', bot=False),
                               SimpleNamespace(pcm=voice_frame()))
                    asyncio.get_running_loop().call_later(0.15, service.shutdown.set)

            service.on_status = status
            with patch.object(discord, 'Client', Client):
                await asyncio.wait_for(service._serve(), timeout=3)
            self.assertEqual(service.audio_jobs.get_nowait()[:2], (42, '觀眾'))
            self.assertFalse(channel.connect.call_args.kwargs['self_deaf'])
            voice.disconnect.assert_awaited_once()
        asyncio.run(run())


class PriorityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        config = yaml.safe_load((ROOT / 'example.yml').read_text(encoding='utf-8'))
        config['chat']['database_path'] = str(Path(self.folder.name) / 'test.sqlite3')
        path = Path(self.folder.name) / 'setting.yml'
        path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
        self.app = VtuberAI(path)

    def tearDown(self):
        self.app.close()
        self.folder.cleanup()

    def test_voice_waits_for_current_round_then_precedes_text_fifo(self):
        started, release = threading.Event(), threading.Event()
        replies, destinations = [], []
        self.app.generate_reply = lambda text, speaker, **kwargs: replies.append(text) or text

        def speak(text):
            destinations.append(self.app.reply_destination)
            if text == '當輪':
                started.set()
                release.wait(3)
            if len(replies) == 4:
                self.app.stop.set()

        self.app.synthesize = speak
        self.app.schedule_conversation('text', '甲', '當輪')
        worker = threading.Thread(target=self.app.control_conversation_loop)
        worker.start()
        try:
            self.assertTrue(started.wait(3))
            self.assertTrue(self.app.schedule_conversation('text', '乙', '一般文字', allow_busy=True))
            self.assertTrue(self.app.enqueue_discord_message(42, '語音觀眾', '語音一'))
            self.assertTrue(self.app.enqueue_discord_message(42, '改過暱稱', '語音二'))
            self.assertEqual(replies, ['當輪'])
            release.set()
            worker.join(3)
        finally:
            release.set()
            self.app.stop.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(replies, ['當輪', '語音一', '語音二', '一般文字'])
        self.assertEqual(destinations, ['local', 'discord', 'discord', 'local'])
        self.assertFalse(self.app.conversation_gate.locked())
        with self.app.audience_memory.connect() as db:
            self.assertEqual(db.execute('SELECT viewer_key,user_text FROM audience_messages ORDER BY id').fetchall(),
                             [('discord:id:42', '語音一'), ('discord:id:42', '語音二')])

    def test_receive_worker_transcribes_and_queues_text(self):
        received = threading.Event()
        def callback(user_id, speaker, text):
            self.app.enqueue_discord_message(user_id, speaker, text)
            received.set()
        service = DiscordVoiceService({'bot_token': 'test', 'guild_id': 1, 'voice_channel_id': 2},
                                      lambda samples: '辨識後的繁體文字', callback, lambda _: None)
        service.asr_thread = threading.Thread(target=service._transcribe_loop)
        service.asr_thread.start()
        try:
            service.audio_jobs.put((42, '觀眾', voice_frame()))
            self.assertTrue(received.wait(3))
            priority, _, job = self.app.conversation_jobs.get_nowait()
            self.assertEqual(priority, 0)
            self.assertEqual(job[0:4], ('discord_voice', '觀眾', '辨識後的繁體文字', 'discord:id:42'))
            self.app.finish_conversation()
        finally:
            service.close()

    def test_asr_waits_for_model_lock_and_converts_to_traditional(self):
        from opencc import OpenCC
        self.app.recognizer = Mock()
        called = threading.Event()
        def recognize(**kwargs):
            called.set()
            return [{'text': '欢迎回来'}]
        self.app.recognizer.generate.side_effect = recognize
        self.app.converter = OpenCC('s2twp')
        results = []
        self.app.model_lock.acquire()
        worker = threading.Thread(target=lambda: results.append(self.app.recognize_samples(np.zeros(1600))))
        try:
            worker.start()
            self.assertFalse(called.wait(0.05))
        finally:
            self.app.model_lock.release()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, ['歡迎回來'])


if __name__ == '__main__':
    unittest.main()
