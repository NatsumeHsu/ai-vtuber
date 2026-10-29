import asyncio
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vtuber.twitch_chat import TwitchChat, EventSubClient


class EventSubTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.commands = root / 'commands.json'
        self.commands.write_text(json.dumps({'commands': {
            'ping': {'role': 'moderator', 'response': '{user}: {args}'},
            'owner': {'role': 'broadcaster', 'response': 'owner'}},
            'keywords': {'hello': 'hi {user}'}}), encoding='utf-8')
        self.observed, self.received, self.status = [], [], []
        def observe(*args):
            self.observed.append(args)
            return 42
        self.chat = TwitchChat({'channel': 'owner', 'tokens_file': str(root / 'tokens.json'),
                                'commands_file': str(self.commands)},
                               lambda *a: self.received.append(a), self.status.append, observe)
        self.client = EventSubClient(self.chat)
        self.client.bot = {'id': 'bot'}
        self.client.broadcaster = {'id': 'owner'}
        self.client.app_access = 'test'
        self.client.request = AsyncMock(return_value={'data': [{'is_sent': True}]})
        self.event = {'broadcaster_user_id': 'owner', 'chatter_user_id': 'viewer',
                      'chatter_user_login': 'viewer', 'chatter_user_name': 'Viewer',
                      'message_id': 'id1', 'message': {'text': 'hello'}, 'badges': []}

    async def test_delivery_dedup_and_reply(self):
        await self.client.notification(self.event)
        await self.client.notification(self.event)
        self.assertEqual(len(self.observed), 1)
        self.assertEqual(self.received, [('Viewer', 'hello', 'twitch:id:viewer', 42)])
        self.assertEqual(self.client.request.call_args.kwargs['json']['message'], 'hi viewer')
        self.assertEqual(self.client.request.call_count, 1)

    async def test_permissions_and_reload(self):
        e = copy.deepcopy(self.event)
        e['message']['text'] = '!ping one two'
        self.assertIsNone(self.client.response(e))
        e['badges'] = [{'set_id': 'moderator'}]
        self.assertEqual(self.client.response(e), 'viewer: one two')
        e['message']['text'] = '!owner'
        self.assertIsNone(self.client.response(e))
        e['chatter_user_id'] = 'owner'
        self.assertEqual(self.client.response(e), 'owner')
        self.commands.write_text('{"keywords": {"hello": "updated"}}', encoding='utf-8')
        self.assertEqual(self.client.response(self.event), 'updated')

    async def test_ignored_and_self_and_wrong_channel(self):
        self.chat.settings['ignored_users'] = ['VIEWER']
        await self.client.notification(self.event)
        self.assertTrue(self.observed[0][-1])
        self.assertFalse(self.received)
        self.assertFalse(self.client.request.called)
        for field, value in [('chatter_user_id', 'bot'), ('broadcaster_user_id', 'other')]:
            e = dict(self.event, **{field: value}, message_id=field)
            await self.client.notification(e)
        self.assertEqual(len(self.observed), 1)

    async def test_refresh_preserves_ts_milliseconds(self):
        self.client.saved['bot'] = {'access_token': 'old', 'refresh_token': 'refresh', 'expires_at': 0}
        self.client.request.return_value = dict(access_token='new', refresh_token='new-refresh', expires_in=3600, scope=['user:read:chat'])
        self.assertEqual(await self.client.token('bot'), 'new')
        persisted = json.loads(self.client.file.read_text(encoding='utf-8'))
        self.assertGreater(persisted['bot']['expires_at'], 1_000_000_000_000)

    async def test_send_refreshes_app_token_on_401(self):
        self.client.request.side_effect = [RuntimeError('Twitch API HTTP 401'),
                                           {'access_token': 'fresh'}, {'data': [{'is_sent': True}]}]
        await self.client.send_message('reply', 'id')
        self.assertEqual(self.client.app_access, 'fresh')
        self.assertEqual(self.client.request.call_count, 3)

    async def test_stop_cancels_active_task(self):
        started = threading.Event()
        async def wait():
            started.set()
            await asyncio.sleep(3600)
        with patch.object(EventSubClient, 'run', side_effect=wait) as run:
            self.chat.start()
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            self.chat.start()
            await asyncio.to_thread(self.chat.close)
            run.assert_awaited_once()
        self.assertFalse(self.chat.thread.is_alive())

    async def test_reject_nonlocal_callback(self):
        self.chat.settings['redirect_uri'] = 'https://example.com/callback'
        with self.assertRaises(ValueError):
            EventSubClient(self.chat)

    async def test_websocket_transfer_does_not_resubscribe(self):
        def packet(kind, payload):
            return {'metadata': {'message_type': kind}, 'payload': payload}
        first, second = AsyncMock(), AsyncMock()
        first.receive_json.side_effect = [
            packet('session_welcome', {'session': {'id': 'first', 'keepalive_timeout_seconds': 10}}),
            packet('session_reconnect', {'session': {'reconnect_url': 'wss://eventsub.wss.twitch.tv/ws?transfer=1'}})]
        second.receive_json.side_effect = [
            packet('session_welcome', {'session': {'id': 'second', 'keepalive_timeout_seconds': 10}}),
            asyncio.TimeoutError()]
        self.client.session = AsyncMock()
        self.client.session.ws_connect.side_effect = [first, second]
        self.client.helix = AsyncMock()
        with self.assertRaises(asyncio.TimeoutError):
            await self.client.listen()
        self.assertEqual(self.client.helix.call_count, 1)
        self.assertEqual(self.client.helix.call_args.kwargs['json']['transport']['session_id'], 'first')
        first.close.assert_awaited_once()
        second.close.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
