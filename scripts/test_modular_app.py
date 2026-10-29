"""Behavior checks across the modular application without hardware or models."""
from pathlib import Path
import runpy
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml
import vtuberAI
from vtuber import VtuberAI


class ModularAppTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        settings = yaml.safe_load((ROOT / 'example.yml').read_text(encoding='utf-8'))
        settings['chat']['database_path'] = str(Path(self.folder.name) / 'history.sqlite3')
        self.config = Path(self.folder.name) / 'setting.yml'
        self.config.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding='utf-8')
        self.app = VtuberAI(self.config)

    def tearDown(self):
        self.app.close()
        self.folder.cleanup()

    def test_legacy_entry_starts_same_application(self):
        self.assertIs(vtuberAI.VtuberAI, VtuberAI)
        with patch.object(VtuberAI, 'run') as run:
            runpy.run_path(str(ROOT / 'vtuberAI.py'), run_name='__main__')
        run.assert_called_once()

    def test_root_paths_and_lazy_model_loading(self):
        self.assertEqual(self.app.root, ROOT)
        self.assertEqual(self.app.project_path('models/llm'), ROOT / 'models/llm')
        self.assertTrue(self.app.database_path.is_file())
        self.assertIsNone(self.app.llm)
        self.assertIsNone(self.app.tts)
        self.assertIsNone(self.app.recognizer)

    def test_dialogue_survives_restart_and_clear_preserves_records(self):
        self.app.llm = Mock()
        self.app.llm.tokenize.return_value = []
        self.app.llm.create_chat_completion.return_value = {'choices': [{'message': {'content': '歡迎回來'}}]}
        self.app.generate_reply('繁體中文', '觀眾')
        restarted = VtuberAI(self.config)
        try:
            self.assertEqual(restarted.history[-1]['content'], '歡迎回來')
            restarted.clear_history()
            self.assertEqual(restarted.history, [])
            with restarted.audience_memory.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM chat_turns').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT user_text FROM audience_messages').fetchone()[0], '繁體中文')
        finally:
            restarted.close()

    def test_busy_message_is_archived_without_reply_queue(self):
        self.app.conversation_gate.acquire()
        mid = self.app.observe_twitch_message('twitch:id:1', '甲', '忙碌時也保存')
        self.assertFalse(self.app.enqueue_twitch_message('甲', '忙碌時也保存', 'twitch:id:1', mid))
        self.assertTrue(self.app.conversation_jobs.empty())
        with self.app.audience_memory.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM audience_messages WHERE id=?', (mid,)).fetchone()[0], 'skipped_busy')
        self.app.conversation_gate.release()

    def test_worker_keeps_busy_until_speech_returns(self):
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        original_post = self.app.post_control_event

        def post(kind, value):
            original_post(kind, value)
            if kind == 'done':
                done.set()

        def speak(text):
            started.set()
            release.wait(3)

        self.app.post_control_event = post
        self.app.generate_reply = lambda text, speaker, **kwargs: '回覆'
        self.app.synthesize = speak
        self.assertTrue(self.app.enqueue_twitch_message('甲', '第一則'))
        thread = threading.Thread(target=self.app.control_conversation_loop)
        thread.start()
        try:
            self.assertTrue(started.wait(3))
            self.assertFalse(self.app.enqueue_twitch_message('乙', '不補排隊'))
            release.set()
            self.assertTrue(done.wait(3))
            self.assertTrue(self.app.conversation_gate.acquire(timeout=3))
            self.app.conversation_gate.release()
        finally:
            release.set()
            self.app.stop.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_tracking_uses_application_settings(self):
        self.app.live2d_settings['camera_tracking'] = {'gain': 1, 'mirror': False}
        self.assertEqual(self.app.face_to_gaze((40, 40, 20, 20), 100, 100), (0, 0))
        self.assertEqual(self.app.face_to_gaze((80, 40, 20, 20), 100, 100), (0.8, 0))


if __name__ == '__main__':
    unittest.main()
