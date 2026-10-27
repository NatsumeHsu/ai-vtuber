import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from audience_memory import AudienceMemory
from vtuberAI import VtuberAI


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.fail = False

    def tokenize(self, text):
        return list(range(len(text) // 3))

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError('simulated failure')
        content = json.dumps({'summary': '常來討論遊戲，互動友善。', 'relationship': '熟悉的觀眾',
                              'warmth': 0.7, 'engagement': 0.8}, ensure_ascii=False)
        return {'choices': [{'message': {'content': content}}]}


class AudienceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'chat.sqlite3'
        self.settings = {'analysis_batch_messages': 2}
        self.memory = AudienceMemory(self.path, self.settings)
        self.stop = threading.Event()

    def tearDown(self):
        self.folder.cleanup()

    def test_busy_archive_duplicate_and_persistence(self):
        app = VtuberAI.__new__(VtuberAI)
        app.audience_memory = self.memory
        app.stop = self.stop
        app.conversation_gate = threading.Lock()
        app.conversation_gate.acquire()
        mid = app.observe_twitch_message('twitch:id:1', '觀眾', '忙碌時仍保存', 'id1')
        self.assertFalse(app.enqueue_twitch_message('觀眾', '忙碌時仍保存', 'twitch:id:1', mid))
        self.assertIsNone(app.observe_twitch_message('twitch:id:1', '觀眾', '忙碌時仍保存', 'id1'))
        restarted = AudienceMemory(self.path, self.settings)
        with restarted.connect() as db:
            self.assertEqual(db.execute('SELECT user_text,status FROM audience_messages').fetchall(),
                             [('忙碌時仍保存', 'skipped_busy')])

    def test_all_batches_then_only_new_messages_on_restart(self):
        for index in range(5):
            mid = self.memory.record('twitch:id:1', '觀眾', f'訊息 {index}', 'twitch', 'skipped_busy')
            if index == 0:
                self.memory.finish(mid, 'replied', '你好')
        llm = FakeLLM()
        self.memory.analyze_startup(llm, 4096, self.stop, lambda _: None)
        self.assertEqual(len(llm.calls), 3)
        first_payload = json.loads(llm.calls[0]['messages'][1]['content'])
        self.assertEqual(first_payload['messages'][0]['reply'], '你好')
        self.assertEqual(first_payload['messages'][1]['status'], 'skipped_busy')
        restarted = AudienceMemory(self.path, self.settings)
        restarted.analyze_startup(llm, 4096, self.stop, lambda _: None)
        self.assertEqual(len(llm.calls), 3)
        restarted.record('twitch:id:1', '新暱稱', '新的發言', 'twitch', 'skipped_busy')
        restarted.analyze_startup(llm, 4096, self.stop, lambda _: None)
        self.assertEqual(len(llm.calls), 4)
        self.assertEqual(restarted.profile('twitch:id:1')[0]['warmth'], 0.7)
        self.assertIn('熟悉的觀眾', restarted.supplement('twitch:id:1'))
        self.assertEqual(restarted.profile('local:觀眾')[0]['summary'], '')

    def test_failed_analysis_keeps_profile_and_retries(self):
        self.memory.record('a', '甲', '你好', 'local')
        llm = FakeLLM()
        self.memory.analyze_startup(llm, 4096, self.stop, lambda _: None)
        before = self.memory.profile('a')
        self.memory.record('a', '甲', '稍後再分析', 'local')
        llm.fail = True
        with self.assertLogs('audience_memory', level='WARNING'):
            self.memory.analyze_startup(llm, 4096, self.stop, lambda _: None)
        self.assertEqual(before, self.memory.profile('a'))
        llm.fail = False
        self.memory.analyze_startup(llm, 4096, self.stop, lambda _: None)
        self.assertGreater(self.memory.profile('a')[1], before[1])

    def test_legacy_import_only_once_without_identity_guessing(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE chat_turns (id INTEGER PRIMARY KEY, speaker TEXT, user_text TEXT, assistant_text TEXT, created_at TEXT)')
            db.execute("INSERT INTO chat_turns VALUES(1, '夏目', '你好', '歡迎', '2026-10-28')")
        AudienceMemory(self.path, self.settings)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO chat_turns VALUES(2, '甲', '新對話另已保存', '你好', '2026-10-28')")
        AudienceMemory(self.path, self.settings)
        with self.memory.connect() as db:
            self.assertEqual(db.execute('SELECT viewer_key FROM audience_messages').fetchall(), [('legacy:夏目',)])

    def test_supplement_is_used_but_original_text_remains(self):
        mid = self.memory.record('twitch:id:1', '觀眾', '遊戲好玩', 'twitch')
        self.memory.analyze_startup(FakeLLM(), 4096, self.stop, lambda _: None)
        app = VtuberAI.__new__(VtuberAI)
        app.audience_memory = self.memory
        app.history, app.history_turns = [], 2
        app.system_prompt = '夏目是你的製作者。'
        app.n_ctx, app.max_tokens, app.temperature = 4096, 100, 0.8
        app.llm = FakeLLM()
        app.save_chat_turn = lambda *args: None
        reply = app.generate_reply('繁體中文', '觀眾', viewer_key='twitch:id:1')
        self.assertIn('夏目是你的製作者', app.llm.calls[-1]['messages'][0]['content'])
        self.assertIn('熟悉的觀眾', app.llm.calls[-1]['messages'][0]['content'])
        self.assertEqual(app.llm.calls[-1]['temperature'], 0.8)
        with self.memory.connect() as db:
            row = db.execute('SELECT user_text,assistant_text FROM audience_messages ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual(row, ('繁體中文', reply))

    def test_context_budget_reduces_batch_without_losing_archived_text(self):
        text = '觀眾完整的長訊息。' * 200
        for _ in range(3):
            self.memory.record('a', '甲', text, 'twitch', 'skipped_busy')
        self.memory.settings['analysis_message_chars'] = 2000
        llm = FakeLLM()
        self.memory.analyze_startup(llm, 1000, self.stop, lambda _: None)
        self.assertEqual(self.memory.profile('a')[1], 3)
        for call in llm.calls:
            used = len(llm.tokenize('\n'.join(m['content'] for m in call['messages']).encode('utf-8')))
            self.assertLessEqual(used + call['max_tokens'] + 192, 1000)
        with self.memory.connect() as db:
            self.assertEqual(db.execute('SELECT user_text FROM audience_messages').fetchall(), [(text,)] * 3)

    def test_invalid_scores_rejected_and_valid_scores_clamped(self):
        for value in (True, float('nan'), float('inf'), '0.9'):
            with self.assertRaises(ValueError):
                self.memory.validate({'summary': '摘要', 'relationship': '觀眾',
                                      'warmth': value, 'engagement': 0.5}, 100)
        profile = self.memory.validate({'summary': '摘要', 'relationship': '觀眾',
                                        'warmth': 9, 'engagement': -2}, 100)
        self.assertEqual((profile['warmth'], profile['engagement']), (1, 0))


if __name__ == '__main__':
    unittest.main()
