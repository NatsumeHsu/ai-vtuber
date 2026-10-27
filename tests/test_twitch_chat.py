import queue
import threading
import unittest
from unittest.mock import Mock
from twitch_chat import TwitchChat, parse_line
from vtuberAI import VtuberAI


class TwitchTests(unittest.TestCase):
    def test_messages_filter_and_display_name(self):
        received = []
        chat = TwitchChat({'channel': 'room',
                           'ignored_users': ['ignored']},
                          lambda *args: received.append(args), lambda _: None)
        self.assertRegex(chat.username, r'^justinfan[0-9]+$')
        self.assertFalse(hasattr(chat, 'token'))
        chat.handle_line('@display-name=觀眾\\s甲 :viewer!user@host PRIVMSG #room :繁體中文 : hello')
        for sender, channel in [(chat.username, 'room'), ('ignored', 'room'), ('viewer', 'other')]:
            chat.handle_line(f':{sender}!u@h PRIVMSG #{channel} :skip')
        self.assertEqual(received, [('觀眾 甲', '繁體中文 : hello', 'twitch:login:viewer', None)])
        self.assertEqual(parse_line('PING :tmi.twitch.tv')[2:], ('PING', ':tmi.twitch.tv'))

    def test_busy_messages_are_dropped_and_idle_messages_resume(self):
        app = VtuberAI.__new__(VtuberAI)
        app.stop = threading.Event()
        app.conversation_jobs = queue.Queue()
        app.conversation_gate = threading.Lock()
        app.audience_memory = Mock()
        done = threading.Event()
        app.post_control_event = lambda kind, value: done.set() if kind == 'done' else None
        started = threading.Event()
        release = threading.Event()
        processed = []
        app.generate_reply = lambda text, speaker, **kwargs: processed.append((speaker, text)) or text

        def synthesize(text):
            if text == '第一則':
                started.set()
                self.assertTrue(release.wait(3))
            else:
                app.stop.set()

        app.synthesize = synthesize
        self.assertTrue(app.enqueue_twitch_message('甲', '第一則'))
        self.assertFalse(app.enqueue_twitch_message('乙', '尚未開始處理也不累積'))
        worker = threading.Thread(target=app.control_conversation_loop)
        worker.start()
        try:
            self.assertTrue(started.wait(3))
            self.assertFalse(app.enqueue_twitch_message('乙', '第二則'))
            self.assertEqual(processed, [('甲', '第一則')])
            self.assertEqual(app.conversation_jobs.qsize(), 0)
            release.set()
            self.assertTrue(done.wait(3))
            # Synchronize with the worker's release after its completion event.
            self.assertTrue(app.conversation_gate.acquire(timeout=3))
            app.conversation_gate.release()
            self.assertTrue(app.enqueue_twitch_message('丙', '第三則'))
        finally:
            release.set()
            app.stop.set() if not done.is_set() else None
            worker.join(3)
            app.stop.set()
        self.assertFalse(worker.is_alive())
        self.assertEqual(processed, [('甲', '第一則'), ('丙', '第三則')])

    def test_archive_runs_for_ignored_viewers_and_uses_account_id(self):
        archived, received = [], []
        def archive(*args):
            archived.append(args)
            return len(archived)
        chat = TwitchChat({'channel': 'room', 'ignored_users': ['ignored']},
                          lambda *args: received.append(args), lambda _: None, archive)
        chat.handle_line('@user-id=42;id=m1;display-name=甲 :viewer!u@h PRIVMSG #room :第一則')
        chat.handle_line('@user-id=42;id=m2;display-name=新暱稱 :viewer!u@h PRIVMSG #room :第二則')
        chat.handle_line('@user-id=43;id=m3 :ignored!u@h PRIVMSG #room :保留但不回覆')
        self.assertEqual([item[0] for item in archived], ['twitch:id:42', 'twitch:id:42', 'twitch:id:43'])
        self.assertTrue(archived[-1][-1])
        self.assertEqual(len(received), 2)


if __name__ == '__main__':
    unittest.main()
