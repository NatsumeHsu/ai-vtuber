"""Read Twitch IRC in a separate thread; model work stays in the AI queue."""
import asyncio
import re
import secrets
import ssl
import threading
import time


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
    def __init__(self, settings, on_message, on_status, on_observation=None):
        self.settings = settings
        self.on_message = on_message
        self.on_status = on_status
        self.on_observation = on_observation
        self.stop_event = threading.Event()
        self.thread = None
        self.username = f'justinfan{secrets.randbelow(900000) + 100000}'
        self.channel = str(settings.get('channel', '')).lower().lstrip('#')
        if not re.fullmatch(r'[a-z0-9_]+', self.channel):
            raise ValueError('Twitch 頻道只能包含英文字母、數字與底線')

    def start(self):
        self.thread = threading.Thread(target=self._run, name='twitch-chat', daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)

    def _run(self):
        asyncio.run(self._listen())

    def handle_line(self, line):
        tags, prefix, command, params = parse_line(line)
        if command == 'PRIVMSG':
            target, sep, text = params.partition(' :')
            sender = prefix.split('!', 1)[0]
            if (sep and text.strip() and target.lower() == f'#{self.channel}'
                    and sender.lower() != self.username):
                if text.startswith('\x01ACTION ') and text.endswith('\x01'):
                    text = text[8:-1]
                speaker = tags.get('display-name') or sender
                viewer_key = ('twitch:id:' + tags['user-id'] if tags.get('user-id')
                              else 'twitch:login:' + sender.lower())
                message_id = None
                ignored = sender.lower() in {str(x).lower() for x in self.settings.get('ignored_users', [])}
                if self.on_observation:
                    message_id = self.on_observation(viewer_key, speaker, text, tags.get('id'), ignored)
                    if message_id is None:
                        return command, params
                if not ignored:
                    self.on_message(speaker, text, viewer_key, message_id)
        return command, params

    async def _listen(self):
        while not self.stop_event.is_set():
            writer = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection('irc.chat.twitch.tv', 6697,
                                            ssl=ssl.create_default_context()), timeout=15)

                async def send(line):
                    writer.write((line + '\r\n').encode('utf-8'))
                    await writer.drain()

                await send(f'NICK {self.username}')
                await send('CAP REQ :twitch.tv/tags twitch.tv/commands')
                deadline = time.monotonic() + 30
                ready = False
                while not self.stop_event.is_set():
                    if not ready and time.monotonic() > deadline:
                        raise TimeoutError('join timeout')
                    try:
                        data = await asyncio.wait_for(reader.readline(), timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    if not data:
                        raise ConnectionError('IRC disconnected')
                    line = data.decode('utf-8', errors='replace').rstrip('\r\n')
                    if not line:
                        continue
                    command, params = self.handle_line(line)
                    if command == 'PING':
                        await send(f'PONG {params}')
                    elif command == '001':
                        await send(f'JOIN #{self.channel}')
                    elif command == '366':
                        ready = True
                        self.on_status(f'Twitch 匿名 IRC 已連線：#{self.channel}，忙碌時略過新訊息')
                    elif command == 'RECONNECT':
                        raise ConnectionError('reconnect')
                    elif command == 'NOTICE' and re.search(r'authentication failed|login unsuccessful|improperly formatted auth|error logging in', params, re.I):
                        self.on_status('Twitch 拒絕匿名 IRC 連線，稍後重試')
                        break
            except (OSError, TimeoutError):
                if not self.stop_event.is_set():
                    self.on_status('Twitch 連線中斷，稍後自動重連')
            except Exception:
                self.on_status('Twitch 連線失敗，請檢查設定及網路')
            finally:
                if writer is not None:
                    writer.close()
                    try:
                        await asyncio.wait_for(writer.wait_closed(), timeout=1)
                    except (OSError, asyncio.TimeoutError):
                        pass
            for _ in range(max(1, int(float(self.settings.get('reconnect_seconds', 5)) * 10))):
                if self.stop_event.is_set():
                    return
                await asyncio.sleep(0.1)
