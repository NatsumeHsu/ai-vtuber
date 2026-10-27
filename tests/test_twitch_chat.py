import queue
import threading
import unittest
from twitch_chat import TwitchChat, parse_line
from vtuberAI import VtuberAI


class TwitchTests(unittest.TestCase):
    def test_messages_filter_and_display_name(self):
        received = []
        chat = TwitchChat({'username': 'bot', 'channel': 'room', 'token': 'test',
                           'ignored_users': ['ignored']},
                          lambda *args: received.append(args), lambda _: None)
        chat.handle_line('@display-name=觀眾\\s甲 :viewer!user@host PRIVMSG #room :繁體中文 : hello')
        for sender, channel in [('bot', 'room'), ('ignored', 'room'), ('viewer', 'other')]:
            chat.handle_line(f':{sender}!u@h PRIVMSG #{channel} :skip')
        self.assertEqual(received, [('觀眾 甲', '繁體中文 : hello')])
        self.assertEqual(parse_line('PING :tmi.twitch.tv')[2:], ('PING', ':tmi.twitch.tv'))

    def test_queue_waits_until_playback_finishes(self):
        app = VtuberAI.__new__(VtuberAI)
        app.stop = threading.Event()
        app.conversation_jobs = queue.Queue()
        app.post_control_event = lambda *args: None
        started = threading.Event()
        release = threading.Event()
        processed = []
        app.generate_reply = lambda text, speaker: processed.append((speaker, text)) or text

        def synthesize(text):
            if text == '第一則':
                started.set()
                self.assertTrue(release.wait(3))
            else:
                app.stop.set()

        app.synthesize = synthesize
        app.enqueue_twitch_message('甲', '第一則')
        worker = threading.Thread(target=app.control_conversation_loop)
        worker.start()
        try:
            self.assertTrue(started.wait(3))
            app.enqueue_twitch_message('乙', '第二則')
            self.assertEqual(processed, [('甲', '第一則')])
            self.assertEqual(app.conversation_jobs.qsize(), 1)
        finally:
            release.set()
            worker.join(3)
            app.stop.set()
        self.assertFalse(worker.is_alive())
        self.assertEqual(processed, [('甲', '第一則'), ('乙', '第二則')])


if __name__ == '__main__':
    unittest.main()
