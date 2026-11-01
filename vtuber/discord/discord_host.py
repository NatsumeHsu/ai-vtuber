"""Discord owns the lifetime of the desktop and AI worker processes."""
import asyncio
import multiprocessing as mp
from pathlib import Path
import threading

from vtuber.core.paths import PROJECT_ROOT

from vtuber.core.config import update_settings
from vtuber.runtime.ipc import Peer


class HostDiscordProxy:
    def __init__(self, peer):
        self.peer = peer

    def play_audio(self, audio, sample_rate, on_level):
        return self.peer.call('play_audio', audio, sample_rate)

    def close(self):
        pass  # The host, rather than the desktop, owns Discord.


def desktop_main(connection, settings_path):
    from vtuber.app import VtuberAI
    app = VtuberAI(settings_path)

    def dispatch(method, *args):
        if method == 'shutdown':
            app.stop.set()
        elif method == 'text_ready':
            service = getattr(app, 'services', {}).get('text')
            return bool(app.llm is not None and service and service.ready and service.process and service.process.is_alive()
                        and service.peer and not service.peer.closed.is_set() and not app.stop.is_set())
        elif method == 'text_message':
            return app.enqueue_discord_text(args[0])
        elif method == 'song_action':
            action, speaker, values = args
            return app.songs.action(action, speaker, **values)
        elif method == 'transcribe':
            if app.recognizer is None:
                return ''
            return app.recognize_samples(*args)
        elif method == 'transcript':
            if app.llm is None or app.tts is None:
                return False
            return app.enqueue_discord_message(*args)
        elif method == 'mouth':
            app.mouth = min(1.0, args[0] * app.mouth_gain)
        elif method == 'channel_changed':
            app.post_control_event('discord_channel', str(args[0]))
        elif method == 'log':
            app.post_control_event('log', args[0])
        else:
            raise ValueError(method)

    peer = Peer(connection, dispatch)
    app.host_peer = peer
    app.discord = HostDiscordProxy(peer)
    def monitor():
        peer.closed.wait()
        app.stop.set()
        # Close model children even if desktop initialization is blocked.
        app.close()
    threading.Thread(target=monitor, daemon=True).start()
    try:
        app.run()
    finally:
        app.close()
        peer.close()


class DiscordHost:
    desktop_entry = staticmethod(desktop_main)

    def __init__(self, settings_path=None):
        import yaml
        from vtuber.discord.discord_voice import DiscordVoiceService
        self.settings_path = Path(settings_path or PROJECT_ROOT / 'setting.yml').resolve()
        self.settings = yaml.safe_load(self.settings_path.read_text(encoding='utf-8'))
        self.peer = self.desktop = None
        self._lifecycle_lock = threading.RLock()
        self._reload_gate = threading.Lock()
        self._closing = threading.Event()
        self.discord = self.make_discord(self.settings)

    def make_discord(self, settings):
        from vtuber.discord.discord_voice import DiscordVoiceService
        service = DiscordVoiceService(settings['discord'],
            lambda samples: self.peer.call('transcribe', samples),
            lambda *args: self.peer.call('transcript', *args), self.log)
        service.on_channel_changed = self.channel_changed
        service.text_ready = lambda: self.peer is not None and self.peer.call('text_ready')
        service.on_text_message = lambda payload: self.peer.call('text_message', payload)
        service.on_voice_enabled_changed = lambda enabled: update_settings(
            self.settings_path, 'discord', {'auto_join': enabled})
        service.reload_settings = self.request_reload
        service.manage_users = self.manage_users
        service.song_action = self.song_action
        service.rpg_settings = settings.get('rpg', {})
        service.rpg_twitch_prefix = settings.get('twitch', {}).get('prefix', '!')
        return service

    def song_action(self, action, speaker, **values):
        if self.peer is None or self.peer.closed.is_set():
            raise RuntimeError('點歌服務尚未就緒。')
        return self.peer.call('song_action', action, speaker, values)

    def manage_users(self, action, user_id=None):
        with self._lifecycle_lock:
            if action not in ('add', 'remove', 'clear'):
                raise ValueError('不支援的白名單操作')
            ids = set(map(int, self.discord.settings.get('allowed_user_ids', [])))
            if action == 'clear':
                ids.clear()
            else:
                user_id = int(user_id)
                if user_id <= 0:
                    raise ValueError('Discord 使用者 ID 必須為正整數')
                if action == 'add':
                    ids.add(user_id)
                else:
                    ids.discard(user_id)
            ids = sorted(ids)
            update_settings(self.settings_path, 'discord', {'allowed_user_ids': ids})
            self.discord.settings['allowed_user_ids'] = ids
            self.settings['discord']['allowed_user_ids'] = ids
            return ids

    def request_reload(self):
        from vtuber.core.config import SettingsMixin
        from vtuber.discord.discord_text import TextReplies
        fresh = SettingsMixin.load_settings(self)
        fresh['discord'].pop('enabled', None)
        fresh['discord']['bot_token'] = self.discord.token
        # Validate before stopping any running service. Never expose file contents.
        candidate = self.make_discord(fresh)
        TextReplies(candidate)
        if candidate.guild_id == self.discord.guild_id:
            candidate.channel_id = self.discord.channel_id
            candidate.voice_enabled = self.discord.voice_enabled
        if self._closing.is_set() or not self._reload_gate.acquire(blocking=False):
            return False
        def reload():
            try:
                with self._lifecycle_lock:
                    if self._closing.is_set():
                        return
                    self.discord.close()
                    self.stop_desktop()
                    self.settings = fresh
                    self.discord = candidate
                    self.start_desktop()
                    self.discord.start()
                self.log('設定已重新載入，bot_token 保持原值；選用服務已停止，請按需重新啟動。')
            except Exception as exc:
                self.log(f'重新載入失敗：{type(exc).__name__}，請檢查設定與本機記錄。')
            finally:
                self._reload_gate.release()
        threading.Thread(target=reload, name='reload-settings', daemon=True).start()
        return True

    def log(self, text):
        print(text, flush=True)
        if self.peer and not self.peer.closed.is_set():
            try:
                self.peer.call('log', text)
            except (RuntimeError, OSError, EOFError):
                pass

    def channel_changed(self, channel_id):
        if self.peer and not self.peer.closed.is_set():
            self.peer.call('channel_changed', channel_id)

    def dispatch(self, method, *args):
        if method == 'rpg_action':
            if self.discord is None or self.discord.loop is None:
                raise RuntimeError('RPG 主服務尚未就緒')
            async def execute_rpg():
                from vtuber.rpg import Identity
                cog = self.discord.client.get_cog('RPGCog')
                if cog is None or cog.game is None:
                    return 'RPG 服務尚未就緒。'
                user_id, name, command = args
                reply = await cog.game.execute(Identity('twitch', str(user_id)), command, name)
                localizer = getattr(cog.game, 'localizer', None)
                return await localizer.text_reply(reply) if localizer else reply
            return asyncio.run_coroutine_threadsafe(execute_rpg(), self.discord.loop).result(timeout=45)
        if method == 'shutdown_host':
            self._closing.set()
            return True
        if method == 'reload_settings':
            return self.request_reload()
        if method == 'text_reply':
            if self.discord is None or self.discord.loop is None:
                raise RuntimeError('Discord 尚未連線')
            async def send():
                import discord
                payload, reply = args
                if int(payload['guild_id']) != self.discord.guild_id:
                    raise ValueError('伺服器不符')
                channel = self.discord.client.get_channel(int(payload['channel_id']))
                if not isinstance(channel, discord.TextChannel) or channel.guild.id != self.discord.guild_id:
                    raise ValueError('文字頻道不符')
                reference = discord.MessageReference(message_id=int(payload['message_id']),
                    channel_id=channel.id, guild_id=channel.guild.id, fail_if_not_exists=False)
                for offset in range(0, len(reply), 2000):
                    await channel.send(reply[offset:offset + 2000], reference=reference if offset == 0 else None,
                                       allowed_mentions=discord.AllowedMentions.none())
            return asyncio.run_coroutine_threadsafe(send(), self.discord.loop).result(timeout=45)
        if method == 'play_audio':
            if self.discord is None:
                raise RuntimeError('Discord 未啟用')
            return self.discord.play_audio(*args, on_level=lambda value: self.peer.call('mouth', value))
        if method == 'discord_channel':
            if self.discord is None or self.discord.loop is None:
                raise RuntimeError('Discord 尚未連線')
            future = asyncio.run_coroutine_threadsafe(self.discord.change_channel(int(args[0])), self.discord.loop)
            return future.result(timeout=35)
        if method == 'twitch_channel':
            import re
            channel = str(args[0]).strip().lower().lstrip('#')
            if not re.fullmatch(r'[a-z0-9_]+', channel):
                raise ValueError('Twitch 頻道只能包含英文字母、數字與底線')
            update_settings(self.settings_path, 'twitch', {'channel': channel})
            return channel
        raise ValueError(method)

    def start_desktop(self):
        context = mp.get_context('spawn')
        parent, child = context.Pipe()
        self.desktop = context.Process(target=self.desktop_entry, args=(child, self.settings_path), name='vtuber-desktop')
        self.desktop.start()
        child.close()
        self.peer = Peer(parent, self.dispatch)

    def stop_desktop(self):
        if self.peer:
            try:
                self.peer.call('shutdown')
            except (RuntimeError, OSError, EOFError):
                pass
        if self.desktop:
            self.desktop.join(20)
            if self.desktop.is_alive():
                self.desktop.terminate()
                self.desktop.join(5)
            self.desktop.close()
            self.desktop = None
        if self.peer:
            self.peer.close()
            self.peer = None

    def run(self):
        self.start_desktop()
        try:
            self.discord.start()
            while not self._closing.wait(0.2):
                with self._lifecycle_lock:
                    if self._reload_gate.locked():
                        continue
                    if not self.desktop or not self.desktop.is_alive() or self.peer.closed.is_set():
                        self.stop_desktop()
                        self.start_desktop()
        except KeyboardInterrupt:
            pass
        finally:
            self._closing.set()
            with self._lifecycle_lock:
                self.discord.close()
                self.stop_desktop()
