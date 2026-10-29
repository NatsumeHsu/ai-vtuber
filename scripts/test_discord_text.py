"""Discord probability, history snapshot and database integration checks."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord
import yaml
from vtuber import VtuberAI
from vtuber.discord_text import TextReplies


class TextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = SimpleNamespace(guild_id=1, settings={}, client=Mock(), on_status=Mock(),
                                       text_ready=Mock(return_value=True), on_text_message=Mock())
        self.events = {}
        self.service.client.event.side_effect = lambda function: self.events.setdefault(function.__name__, function)
        self.channel = Mock(spec=discord.TextChannel, id=2)
        self.message = self.message_at(20)
        self.previous = [self.message_at(i) for i in range(19, 9, -1)]
        async def history(**kwargs):
            for item in self.previous:
                yield item
        self.channel.history = Mock(side_effect=history)
        TextReplies(self.service).register()

    def message_at(self, ident):
        return SimpleNamespace(id=ident, guild=SimpleNamespace(id=1), channel=self.channel,
            author=SimpleNamespace(id=42, display_name='觀眾', bot=False), content=f'訊息{ident}',
            webhook_id=None, created_at=datetime.now(timezone.utc))

    async def test_probability_boundary_and_last_ten_in_order(self):
        with patch('vtuber.discord_text.secrets.randbelow', return_value=25000):
            await self.events['on_message'](self.message)
        self.channel.history.assert_not_called()
        with patch('vtuber.discord_text.secrets.randbelow', return_value=24999):
            await self.events['on_message'](self.message)
        self.channel.history.assert_called_once_with(limit=10, before=self.message, oldest_first=False)
        payload = self.service.on_text_message.call_args.args[0]
        self.assertEqual([entry['message_id'] for entry in payload['context']], list(map(str, range(10, 20))))
        self.assertEqual(payload['message_id'], '20')

    async def test_no_capture_when_generator_stopped(self):
        self.service.text_ready.return_value = False
        with patch('vtuber.discord_text.secrets.randbelow', return_value=0):
            await self.events['on_message'](self.message)
        self.channel.history.assert_not_called()
        self.service.on_text_message.assert_not_called()

    async def test_bot_wrong_guild_and_commands_do_not_trigger(self):
        for field, value in (('guild', None), ('guild', SimpleNamespace(id=999)),
                             ('content', '/help'), ('webhook_id', 123)):
            message = self.message_at(20)
            setattr(message, field, value)
            await self.events['on_message'](message)
        self.message.author.bot = True
        await self.events['on_message'](self.message)
        self.service.on_text_message.assert_not_called()

    async def test_snapshot_saved_once_busy_status_and_no_tts(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = yaml.safe_load(Path('example.yml').read_text(encoding='utf-8'))
            settings['chat']['database_path'] = str(Path(folder) / 'history.db')
            path = Path(folder) / 'settings.yml'
            path.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding='utf-8')
            app = VtuberAI(path)
            payload = {**TextReplies.entry(self.message), 'guild_id': '1', 'channel_id': '2',
                       'context': [TextReplies.entry(item) for item in reversed(self.previous)]}
            try:
                app.conversation_gate.acquire()
                self.assertFalse(app.enqueue_discord_text(payload))
                app.conversation_gate.release()
                with app.audience_memory.connect() as db:
                    row = db.execute('SELECT status, context_json FROM discord_text_contexts').fetchone()
                    self.assertEqual(row[0], 'skipped_busy')
                    self.assertIn('訊息10', row[1])
                    db.execute('DELETE FROM discord_text_contexts')
                    db.execute('DELETE FROM audience_messages')
                    db.commit()
                app.host_peer = Mock()
                app.llm = Mock()
                app.llm.tokenize.return_value = []
                app.llm.create_chat_completion.return_value = {'choices': [{'message': {'content': '你好'}}]}
                app.synthesize = Mock()
                app.history = [{'role': 'user', 'content': '其他頻道不應混入'}]
                self.assertTrue(app.enqueue_discord_text(payload))
                self.assertFalse(app.enqueue_discord_text(payload))
                app.control_window = object()
                app.host_peer.call.side_effect = lambda *a: app.stop.set()
                worker = threading.Thread(target=app.control_conversation_loop)
                worker.start()
                worker.join(3)
                self.assertFalse(worker.is_alive())
                app.synthesize.assert_not_called()
                messages = app.llm.create_chat_completion.call_args.kwargs['messages']
                self.assertNotIn('其他頻道不應混入', str(messages))
                self.assertIn('channel_context', messages[-1]['content'])
                with app.audience_memory.connect() as db:
                    self.assertEqual(db.execute('SELECT status, assistant_text FROM discord_text_contexts').fetchone(), ('sent', '你好'))
            finally:
                app.close()


if __name__ == '__main__':
    unittest.main()
