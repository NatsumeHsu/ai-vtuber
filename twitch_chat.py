"""Read Twitch IRC in a separate thread; model work stays in the AI queue."""
import asyncio
import json
import re
import threading
import time
from urllib.request import Request, urlopen


def parse_line(line):
    tags = {}
    if line.startswith('@'):
        raw, line = line[1:].split(' ', 1)
        for item in raw.split(';'):
            key, _, value = item.partition('=')
            tags[key] = re.sub(r'\\(.)', lambda m: {'s': ' ', ':': ';', 'r': '\r', 'n': '\n', '\\': '\\'}.get(m[1], m[1]), value)
    prefix = ''
    if line.startswith(':'):
        prefix, line = line[1:].split(' ', 1)
    command, _, params = line.partition(' ')
    return tags, prefix, command, params


class TwitchChat:
    def __init__(self, settings, on_message, on_status):
        self.settings = settings
        self.on_message = on_message
        self.on_status = on_status
        self.stop_event = threading.Event()
        self.thread = None
        self.username = str(settings.get('username', '')).lower()
        self.channel = str(settings.get('channel', '')).lower().lstrip('#')
        self.token = str(settings.get('token', '')).removeprefix('oauth:')
        if not all(re.fullmatch(r'[a-z0-9_]+', value) for value in (self.username, self.channel)):
            raise ValueError('Twitch 帳號與頻道只能包含英文字母、數字與底線')
        if not self.token or re.search(r'\s', self.token):
            raise ValueError('請在 setting.yaml 填入有效的 Twitch token')

    def start(self):
        self.thread = threading.Thread(target=self._run, name='twitch-chat', daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)

    def _validate(self):
        request = Request('https://id.twitch.tv/oauth2/validate',
                          headers={'Authorization': f'OAuth {self.token}'})
        with urlopen(request, timeout=15) as response:
            auth = json.load(response)
        if auth.get('login', '').lower() != self.username:
            raise ValueError('Twitch token 所屬帳號與 username 不一致')
        if 'chat:read' not in auth.get('scopes', []):
            raise ValueError('Twitch token 缺少 chat:read 權限')

    def _run(self):
        try:
            self._validate()
        except Exception:
            self.on_status('Twitch 驗證失敗，請檢查 setting.yaml 的帳號、Token 與 chat:read 權限')
            return
        asyncio.run(self._listen())

    def handle_line(self, line):
        tags, prefix, command, params = parse_line(line)
        if command == 'PRIVMSG':
            target, sep, text = params.partition(' :')
            sender = prefix.split('!', 1)[0]
            if (sep and text.strip() and target.lower() == f'#{self.channel}'
                    and sender.lower() != self.username
                    and sender.lower() not in {str(x).lower() for x in self.settings.get('ignored_users', [])}):
                if text.startswith('\x01ACTION ') and text.endswith('\x01'):
                    text = text[8:-1]
                self.on_message(tags.get('display-name') or sender, text)
        return command, params

    async def _listen(self):
        from websockets import connect
        from websockets.exceptions import ConnectionClosed
        while not self.stop_event.is_set():
            try:
                async with connect('wss://irc-ws.chat.twitch.tv:443', open_timeout=15, close_timeout=1) as ws:
                    await ws.send(f'PASS oauth:{self.token}\r\nNICK {self.username}\r\nCAP REQ :twitch.tv/tags twitch.tv/commands\r\n')
                    deadline = time.monotonic() + 30
                    ready = False
                    buffer = ''
                    while not self.stop_event.is_set():
                        if not ready and time.monotonic() > deadline:
                            raise TimeoutError('join timeout')
                        try:
                            data = await asyncio.wait_for(ws.recv(), timeout=1)
                        except asyncio.TimeoutError:
                            continue
                        if isinstance(data, bytes):
                            data = data.decode('utf-8')
                        buffer += data
                        while '\r\n' in buffer:
                            line, buffer = buffer.split('\r\n', 1)
                            if not line:
                                continue
                            command, params = self.handle_line(line)
                            if command == 'PING':
                                await ws.send(f'PONG {params}\r\n')
                            elif command == '001':
                                await ws.send(f'JOIN #{self.channel}\r\n')
                            elif command == '366':
                                ready = True
                                self.on_status(f'Twitch 已連線：#{self.channel}，聊天訊息依序排隊')
                            elif command == 'RECONNECT':
                                raise ConnectionError('reconnect')
                            elif command == 'NOTICE' and re.search(r'authentication failed|login unsuccessful|improperly formatted auth|error logging in', params, re.I):
                                self.on_status('Twitch 登入遭拒，請更新 setting.yaml 的 Token')
                                return
            except (OSError, TimeoutError, ConnectionClosed):
                if not self.stop_event.is_set():
                    self.on_status('Twitch 連線中斷，稍後自動重連')
            except Exception:
                self.on_status('Twitch 連線失敗，請檢查設定及網路')
            for _ in range(max(1, int(float(self.settings.get('reconnect_seconds', 5)) * 10))):
                if self.stop_event.is_set():
                    return
                await asyncio.sleep(0.1)
