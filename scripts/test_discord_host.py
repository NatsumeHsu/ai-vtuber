"""Offline coverage for host routing, permission checks and persisted updates."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, AsyncMock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vtuber.config import update_settings
from vtuber.discord_voice import DiscordVoiceService
from vtuber.discord_host import DiscordHost
from vtuber.process_runtime import Peer


def desktop_probe(connection, settings_path):
    import threading
    stop = threading.Event()
    peer = Peer(connection, lambda method: stop.set())
    try:
        peer.call('twitch_channel', 'updated_by_child')
        stop.wait(5)
    finally:
        peer.close()


class HostTests(unittest.TestCase):
    def setUp(self):
        factory = patch('vtuber.discord_voice.DiscordVoiceService')
        self.host_factory = factory.start()
        self.addCleanup(factory.stop)

    def test_no_saved_voice_channel_is_required_or_written(self):
        service = DiscordVoiceService({'bot_token': 'test', 'guild_id': 1}, Mock(), Mock(), Mock())
        self.assertIsNone(service.channel_id)
        self.assertFalse(service.voice_enabled)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'setting.yml'
            original = 'discord: {enabled: false}\ntwitch: {channel: old}\n'
            path.write_text(original, encoding='utf-8')
            host = DiscordHost(path)
            host.channel_changed(123)
            self.assertEqual(path.read_text(encoding='utf-8'), original)

    def test_reload_keeps_login_token_and_applies_other_settings(self):
        import yaml
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'setting.yml'
            fresh = yaml.safe_load(Path('example.yml').read_text(encoding='utf-8'))
            fresh['discord'].update(bot_token='edited-token', guild_id=1, allowed_user_ids=[123])
            fresh['discord']['text_replies']['probability'] = 0.5
            path.write_text(yaml.safe_dump(fresh), encoding='utf-8')
            host = DiscordHost(path)
            previous = SimpleNamespace(token='running-token', guild_id=1, channel_id=9,
                                       voice_enabled=True, close=Mock())
            host.discord = previous
            candidates = []
            def make(settings):
                service = DiscordVoiceService(settings['discord'], Mock(), Mock(), Mock())
                service.start = Mock()
                candidates.append(service)
                return service
            host.make_discord = make
            host.stop_desktop = Mock()
            host.start_desktop = Mock()
            self.assertTrue(host.request_reload())
            self.assertTrue(host._reload_gate.acquire(timeout=3))
            host._reload_gate.release()
            self.assertEqual(host.discord.token, 'running-token')
            self.assertEqual(host.settings['discord']['allowed_user_ids'], [123])
            self.assertEqual(host.settings['discord']['text_replies']['probability'], 0.5)
            self.assertEqual(host.discord.channel_id, 9)
            previous.close.assert_called_once()
            host.start_desktop.assert_called_once()
            host.discord.start.assert_called_once()
            self.assertEqual(yaml.safe_load(path.read_text())['discord']['bot_token'], 'edited-token')

    def test_invalid_reload_does_not_stop_services(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'setting.yml'
            path.write_text('discord: {}\n', encoding='utf-8')
            host = DiscordHost(path)
            previous = host.discord
            host.stop_desktop = Mock()
            with self.assertRaises(ValueError):
                host.request_reload()
            previous.close.assert_not_called()
            host.stop_desktop.assert_not_called()

    def test_reload_replaces_real_desktop_process_without_exiting_host(self):
        import yaml
        import threading
        import time
        def factory(settings, *callbacks):
            service = DiscordVoiceService(settings, *callbacks)
            service.start = Mock()
            service.close = Mock()
            return service
        self.host_factory.side_effect = factory
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'setting.yml'
            settings = yaml.safe_load(Path('example.yml').read_text(encoding='utf-8'))
            settings['discord'].update(bot_token='running-token', guild_id=1)
            path.write_text(yaml.safe_dump(settings), encoding='utf-8')
            host = DiscordHost(path)
            host.desktop_entry = desktop_probe
            thread = threading.Thread(target=host.run)
            thread.start()
            try:
                deadline = time.monotonic() + 5
                while host.peer is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                old_pid = host.desktop.pid
                settings['discord']['bot_token'] = 'edited-token'
                settings['discord']['allowed_user_ids'] = [42]
                path.write_text(yaml.safe_dump(settings), encoding='utf-8')
                self.assertTrue(host.request_reload())
                self.assertTrue(host._reload_gate.acquire(timeout=8))
                host._reload_gate.release()
                self.assertTrue(thread.is_alive())
                self.assertNotEqual(host.desktop.pid, old_pid)
                self.assertEqual(host.discord.token, 'running-token')
                self.assertEqual(host.discord.settings['allowed_user_ids'], [42])
            finally:
                if host.peer:
                    host.peer.call('shutdown')
                thread.join(10)
            self.assertFalse(thread.is_alive())
    def test_host_and_desktop_real_spawn_routing(self):
        import yaml
        import threading
        import time
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'setting.yml'
            path.write_text('discord: {enabled: false}\ntwitch:\n  channel: old\n', encoding='utf-8')
            host = DiscordHost(path)
            host.desktop_entry = desktop_probe
            errors = []
            def run():
                try:
                    host.run()
                except Exception as exc:
                    errors.append(exc)
            thread = threading.Thread(target=run)
            thread.start()
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if yaml.safe_load(path.read_text(encoding='utf-8'))['twitch']['channel'] == 'updated_by_child':
                        break
                    time.sleep(0.05)
                self.assertEqual(yaml.safe_load(path.read_text(encoding='utf-8'))['twitch']['channel'], 'updated_by_child')
                host.peer.call('shutdown')
            finally:
                thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    def test_slash_command_denies_unlisted_users_before_moving(self):
        import discord
        async def run():
            service = DiscordVoiceService({'bot_token': 'test', 'guild_id': 1,
                                           'allowed_user_ids': [10]}, Mock(), Mock(), Mock())
            service.client = discord.Client(intents=discord.Intents.none())
            service.change_channel = AsyncMock()
            tree = service.register_commands()
            command = tree.get_command('join')
            interaction = Mock()
            interaction.user.id = 11
            interaction.guild_id = 1
            interaction.response.send_message = AsyncMock()
            interaction.response.defer = AsyncMock()
            interaction.followup.send = AsyncMock()
            channel = Mock(spec=discord.VoiceChannel, id=3)
            interaction.user.voice.channel = channel
            await command.callback(interaction)
            service.change_channel.assert_not_awaited()
            interaction.response.send_message.assert_awaited_once()
            interaction.user.id = 10
            await command.callback(interaction)
            service.change_channel.assert_awaited_once_with(3)
            await service.client.close()
        asyncio.run(run())

    def test_explicit_id_and_guild_required(self):
        service = DiscordVoiceService({'bot_token': 'test', 'guild_id': 1},
                                      Mock(), Mock(), Mock())
        self.assertFalse(service.command_allowed(10, 1))
        service.settings['allowed_user_ids'] = [10]
        self.assertTrue(service.command_allowed(10, 1))
        self.assertFalse(service.command_allowed(11, 1))
        self.assertFalse(service.command_allowed(10, 2))
        self.assertFalse(service.command_allowed(10, None))

    def test_setting_updates_preserve_other_settings(self):
        import yaml
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'setting.yml'
            path.write_text('# keep comment\ndiscord:\n  bot_token: private\n  auto_join: false\ntwitch:\n  channel: old\n', encoding='utf-8')
            update_settings(path, 'discord', {'auto_join': True, 'allowed_user_ids': [10, 20]})
            update_settings(path, 'twitch', {'channel': 'new_name'})
            text = path.read_text(encoding='utf-8')
            self.assertIn('# keep comment', text)
            result = yaml.safe_load(text)
            self.assertEqual(result['discord'], {'bot_token': 'private', 'auto_join': True, 'allowed_user_ids': [10, 20]})
            self.assertEqual(result['twitch']['channel'], 'new_name')

    def test_validated_twitch_update(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'setting.yml'
            path.write_text('discord: {enabled: false}\ntwitch:\n  channel: old\n', encoding='utf-8')
            host = DiscordHost(path)
            self.assertEqual(host.dispatch('twitch_channel', '#NEW_Name'), 'new_name')
            with self.assertRaises(ValueError):
                host.dispatch('twitch_channel', 'bad/name')

    def test_channel_move_and_guild_validation(self):
        import discord
        async def run():
            service = DiscordVoiceService({'bot_token': 'test', 'guild_id': 1}, Mock(), Mock(), Mock())
            channel = Mock(spec=discord.VoiceChannel)
            channel.guild = SimpleNamespace(id=1)
            channel.id = 3
            service.client = Mock()
            service.client.get_channel.return_value = channel
            service.voice = Mock()
            service.voice.is_connected.return_value = True
            service.voice.is_playing.return_value = False
            service.voice.move_to = AsyncMock()
            service.channel_lock = asyncio.Lock()
            service.on_channel_changed = Mock()
            self.assertEqual(await service.change_channel(3), 3)
            service.voice.move_to.assert_awaited_once_with(channel)
            service.on_channel_changed.assert_called_once_with(3)
            channel.guild.id = 999
            with self.assertRaises(ValueError):
                await service.change_channel(3)
            self.assertEqual(service.voice.move_to.await_count, 1)
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
