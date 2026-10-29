"""Twitch 服務：生命週期、OAuth、EventSub 收訊與指令回覆。"""
import asyncio
from collections import deque
import json
import os
import re
import threading
from pathlib import Path
import secrets
import time
from urllib.parse import urlencode, urlparse

import aiohttp
from aiohttp import web


class TwitchChat:
    def __init__(self, settings, on_message, on_status, on_observation=None):
        self.settings = settings
        self.on_message = on_message
        self.on_status = on_status
        self.on_observation = on_observation
        self.stop_event = threading.Event()
        self.thread = None
        self.loop = None
        self.task = None
        self.channel = str(settings.get('channel', '')).lower().lstrip('#')
        if not re.fullmatch(r'[a-z0-9_]+', self.channel):
            raise ValueError('Twitch 頻道只能包含英文字母、數字與底線')

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name='twitch-chat', daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self._cancel)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)

    def _cancel(self):
        if self.task:
            self.task.cancel()

    def _run(self):
        asyncio.run(self._serve())

    async def _serve(self):
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        try:
            if self.stop_event.is_set():
                return
            await EventSubClient(self).run()
        except asyncio.CancelledError:
            pass
        except (ValueError, OSError, RuntimeError) as exc:
            self.on_status(f'Twitch：{exc}')
        except Exception:
            self.on_status('Twitch 啟動失敗，請檢查設定與授權檔格式')
        finally:
            self.task = None
            self.loop = None


class EventSubClient:
    def __init__(self, chat):
        self.chat = chat
        self.settings = chat.settings
        self.root = Path(__file__).resolve().parent.parent
        self.file = self.root / self.settings.get('tokens_file', 'data/tokens.json')
        self.saved = json.loads(self.file.read_text(encoding='utf-8-sig')) if self.file.exists() else {}
        self.client_id = self.settings.get('client_id') or os.getenv('TWITCH_CLIENT_ID', '')
        self.secret = self.settings.get('client_secret') or os.getenv('TWITCH_CLIENT_SECRET', '')
        self.redirect = self.settings.get('redirect_uri', 'http://localhost:3000/callback')
        parsed = urlparse(self.redirect)
        if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1')
                or parsed.path != '/callback' or parsed.query or parsed.fragment or parsed.username):
            raise ValueError('Twitch redirect_uri 必須為 http://localhost:連接埠/callback')
        self.host, self.port = parsed.hostname, parsed.port or 80
        self.seen = deque(maxlen=2048)

    async def app_token(self):
        self.app_access = (await self.request('https://id.twitch.tv/oauth2/token', method='POST',
            data=dict(client_id=self.client_id, client_secret=self.secret,
                      grant_type='client_credentials')))['access_token']

    async def send_message(self, reply, message_id):
        for attempt in range(2):
            try:
                return await self.request('https://api.twitch.tv/helix/chat/messages', method='POST',
                    headers={'Authorization': 'Bearer ' + self.app_access, 'Client-Id': self.client_id},
                    json=dict(broadcaster_id=self.broadcaster['id'], sender_id=self.bot['id'],
                              message=reply[:500], reply_parent_message_id=message_id))
            except RuntimeError as exc:
                if str(exc) != 'Twitch API HTTP 401' or attempt:
                    raise
                await self.app_token()

    async def request(self, url, **kwargs):
        async with self.session.request(kwargs.pop('method', 'GET'), url, **kwargs) as response:
            # Never include response bodies or request URLs in errors: OAuth contains secrets.
            if response.status >= 400:
                raise RuntimeError(f'Twitch API HTTP {response.status}')
            return await response.json()

    def persist(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.file.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.saved, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.file)

    async def grant(self, role, **values):
        result = await self.request('https://id.twitch.tv/oauth2/token', method='POST', data={
            'client_id': self.client_id, 'client_secret': self.secret, **values})
        self.saved[role] = dict(access_token=result['access_token'],
            refresh_token=result.get('refresh_token', self.saved.get(role, {}).get('refresh_token')),
            expires_at=time.time() * 1000 + result['expires_in'] * 1000,
            scope=result.get('scope', []))
        self.persist()

    async def token(self, role, force=False):
        old = self.saved[role]
        if force or old['expires_at'] < time.time() * 1000 + 120000:
            await self.grant(role, grant_type='refresh_token', refresh_token=old['refresh_token'])
        return self.saved[role]['access_token']

    async def helix(self, path, role='bot', **kwargs):
        for attempt in range(2):
            access = await self.token(role, force=bool(attempt))
            try:
                return await self.request('https://api.twitch.tv/helix' + path,
                    headers={'Authorization': 'Bearer ' + access, 'Client-Id': self.client_id}, **kwargs)
            except RuntimeError as exc:
                if str(exc) != 'Twitch API HTTP 401' or attempt:
                    raise

    async def authorize(self, role, scopes):
        if role in self.saved and set(scopes) <= set(self.saved[role].get('scope', [])):
            try:
                await self.helix('/users', role)
                return
            except (RuntimeError, KeyError):
                pass
        state = secrets.token_urlsafe(32)
        done = asyncio.get_running_loop().create_future()

        async def callback(request):
            if not secrets.compare_digest(request.query.get('state', ''), state):
                return web.Response(status=400, text='Invalid OAuth state')
            if request.query.get('error'):
                if not done.done():
                    done.set_exception(RuntimeError('Twitch 授權已取消'))
                return web.Response(status=400, text='Authorization cancelled')
            if not request.query.get('code') or done.done():
                return web.Response(status=400, text='Invalid OAuth callback')
            try:
                await self.grant(role, grant_type='authorization_code',
                                 code=request.query['code'], redirect_uri=self.redirect)
                done.set_result(None)
                return web.Response(text='授權完成，可以關閉此分頁。')
            except Exception:
                done.set_exception(RuntimeError('Twitch OAuth 交換失敗'))
                return web.Response(status=500, text='OAuth failed')

        app = web.Application()
        app.router.add_get('/callback', callback)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.TCPSite(runner, self.host, self.port).start()
            url = 'https://id.twitch.tv/oauth2/authorize?' + urlencode(dict(
                response_type='code', client_id=self.client_id, redirect_uri=self.redirect,
                scope=' '.join(scopes), state=state, force_verify='true'))
            self.chat.on_status(f'Twitch 請以 {role} 帳號開啟授權網址：{url}')
            await asyncio.wait_for(done, timeout=300)
        finally:
            await runner.cleanup()

    def response(self, event):
        path = self.root / self.settings.get('commands_file', 'data/commands.json')
        if not path.exists():
            return None
        settings = json.loads(path.read_text(encoding='utf-8-sig'))
        text = event['message']['text'].strip()
        prefix = self.settings.get('prefix', '!')
        response = None
        if text.startswith(prefix):
            parts = text[len(prefix):].split()
            command = settings.get('commands', {}).get(parts[0].lower() if parts else '')
            if command:
                role = command.get('role', 'everyone')
                broadcaster = event['chatter_user_id'] == self.broadcaster['id']
                moderator = any(b.get('set_id') == 'moderator' for b in event.get('badges', []))
                if role == 'everyone' or (role == 'broadcaster' and broadcaster) or (role == 'moderator' and (broadcaster or moderator)):
                    response = command['response'].replace('{args}', ' '.join(parts[1:]))
        else:
            response = next((v for k, v in settings.get('keywords', {}).items() if k in text), None)
        return response.replace('{user}', event['chatter_user_login']) if response else None

    async def notification(self, event):
        if event['chatter_user_id'] == self.bot['id'] or event.get('broadcaster_user_id') != self.broadcaster['id']:
            return
        message_id = event['message_id']
        if message_id in self.seen:
            return
        self.seen.append(message_id)
        login = event['chatter_user_login']
        text = event['message']['text']
        speaker = event.get('chatter_user_name') or login
        key = 'twitch:id:' + event['chatter_user_id']
        ignored = login.lower() in {str(x).lower() for x in self.settings.get('ignored_users', [])}
        mid = None
        if self.chat.on_observation:
            mid = self.chat.on_observation(key, speaker, text, message_id, ignored)
            if mid is None:
                return
        if ignored:
            return
        self.chat.on_message(speaker, text, key, mid)
        try:
            reply = self.response(event)
            if reply:
                result = await self.send_message(reply, message_id)
                if not result.get('data', [{}])[0].get('is_sent'):
                    self.chat.on_status('Twitch 指令回覆未送出')
        except (RuntimeError, ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError):
            self.chat.on_status('Twitch 指令回覆失敗，請檢查授權與 commands.json')

    async def run(self):
        if not self.client_id or not self.secret:
            raise ValueError('請設定 twitch.client_id / client_secret 或 TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET')
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as self.session:
            await self.authorize('bot', ['user:read:chat', 'user:write:chat', 'user:bot'])
            await self.authorize('channel', ['channel:bot'])
            self.bot = (await self.helix('/users'))['data'][0]
            self.broadcaster = (await self.helix('/users', 'channel'))['data'][0]
            if self.bot['id'] == self.broadcaster['id']:
                raise ValueError('Twitch Bot 與實況主必須使用不同帳號')
            if self.broadcaster['login'].lower() != self.chat.channel:
                raise ValueError('Twitch channel 授權帳號與設定頻道不符，請重新授權 channel')
            while not self.chat.stop_event.is_set():
                try:
                    await self.app_token()
                    await self.listen()
                except (aiohttp.ClientError, RuntimeError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
                    self.chat.on_status('Twitch EventSub 連線中斷，稍後自動重連')
                await asyncio.sleep(float(self.settings.get('reconnect_seconds', 5)))

    async def listen(self):
        url = 'wss://eventsub.wss.twitch.tv/ws'
        transition = False
        previous = None
        try:
            while True:
                socket = await self.session.ws_connect(url, autoping=True)
                try:
                    timeout = 15
                    while True:
                        message = await socket.receive_json(timeout=timeout)
                        kind = message['metadata']['message_type']
                        payload = message['payload']
                        if kind == 'session_welcome':
                            timeout = payload['session'].get('keepalive_timeout_seconds', 10) + 5
                            if previous:
                                await previous.close()
                                previous = None
                            if not transition:
                                await self.helix('/eventsub/subscriptions', method='POST', json={
                                    'type': 'channel.chat.message', 'version': '1',
                                    'condition': {'broadcaster_user_id': self.broadcaster['id'], 'user_id': self.bot['id']},
                                    'transport': {'method': 'websocket', 'session_id': payload['session']['id']}})
                            self.chat.on_status(f'Twitch EventSub 已連線：#{self.chat.channel}')
                        elif kind == 'notification':
                            await self.notification(payload['event'])
                        elif kind == 'session_reconnect':
                            url = payload['session']['reconnect_url']
                            parsed = urlparse(url)
                            if parsed.scheme != 'wss' or parsed.hostname != 'eventsub.wss.twitch.tv':
                                raise ValueError('Invalid Twitch reconnect URL')
                            transition = True
                            previous = socket
                            break
                        elif kind == 'revocation':
                            raise RuntimeError('Twitch 訂閱授權已撤銷')
                finally:
                    if socket is not previous:
                        await socket.close()
        finally:
            if previous:
                await previous.close()
