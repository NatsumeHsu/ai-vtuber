"""Guild command behaviour without logging in or sending network requests."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord
from discord.ext import commands
from types import SimpleNamespace
from functools import partial
from vtuber.discord.discord_voice import DiscordVoiceService


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = DiscordVoiceService({'bot_token': 'test', 'guild_id': 1,
                                            'allowed_user_ids': [10]}, Mock(), Mock(), Mock())
        self.service.client = commands.Bot(command_prefix=[], help_command=None, intents=discord.Intents.none())
        self.service.channel_lock = asyncio.Lock()
        self.tree = await self.service.register_commands()

    async def asyncTearDown(self):
        await self.service.client.close()

    def interaction(self, user=11, guild=1):
        result = Mock()
        result.user.id = user
        result.guild_id = guild
        result.response.send_message = AsyncMock()
        result.response.defer = AsyncMock()
        result.followup.send = AsyncMock()
        return result

    def command(self, name):
        command = self.tree.get_command(name)
        return SimpleNamespace(callback=partial(command.callback, command.binding), parameters=command.parameters)

    async def test_commands_are_native_cogs_with_bound_callbacks(self):
        self.assertEqual(set(self.service.client.cogs),
                         {'GeneralCommands', 'VoiceCommands', 'FunCommands', 'AccessCommands', 'MusicCommands', 'RPGCog'})
        for command in self.tree.walk_commands():
            if isinstance(command, discord.app_commands.Command):
                self.assertIsInstance(command.binding, commands.Cog)
        self.assertEqual(set(c.name for c in self.tree.get_command('discord-users').commands),
                         {'add', 'remove', 'clear', 'list'})

    async def test_owner_can_bootstrap_empty_allowlist(self):
        self.service.settings['allowed_user_ids'] = []
        self.service.manage_users = Mock(return_value=[20])
        interaction = self.interaction(user=10)
        interaction.guild.owner_id = 10
        member = Mock(spec=discord.Member, id=20)
        command = self.tree.get_command('discord-users').get_command('add')
        await command.callback(command.binding, interaction, member)
        self.service.manage_users.assert_called_once_with('add', 20)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        interaction.followup.send.assert_awaited_once()

    async def test_allowlisted_nonowner_cannot_change_access(self):
        self.service.manage_users = Mock()
        for guild, user in ((1, 10), (99, 99), (None, 99)):
            interaction = self.interaction(user=user, guild=guild)
            interaction.guild.owner_id = 99
            for name in ('clear', 'list'):
                command = self.tree.get_command('discord-users').get_command(name)
                await command.callback(command.binding, interaction)
            interaction.response.defer.assert_not_awaited()
        self.service.manage_users.assert_not_called()

    async def test_help_lists_registered_commands(self):
        interaction = self.interaction()
        await self.command('help').callback(interaction)
        text = interaction.response.send_message.call_args.args[0]
        for name in ('help', 'ping', 'status', 'serverinfo', 'userinfo', 'join', 'leave', 'roll', 'choose', 'poll', 'avatar', 'reload'):
            self.assertIsNotNone(self.command(name))
            self.assertIn('/' + name, text)
        self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])

    async def test_readonly_commands_accept_ordinary_members_and_reject_other_guilds(self):
        for name in ('help', 'ping', 'status'):
            for guild in (1, None, 99):
                with self.subTest(command=name, guild=guild):
                    interaction = self.interaction(guild=guild)
                    await self.command(name).callback(interaction)
                    text = interaction.response.send_message.call_args.args[0]
                    self.assertEqual('沒有使用此指令的權限' in text, guild != 1)

    async def test_leave_denies_nonallowlisted_user(self):
        self.service.leave_channel = AsyncMock()
        interaction = self.interaction()
        await self.command('leave').callback(interaction)
        self.service.leave_channel.assert_not_awaited()
        interaction.response.defer.assert_not_awaited()

    async def test_reload_requires_allowlist(self):
        self.service.reload_settings = Mock(return_value=True)
        interaction = self.interaction()
        await self.command('reload').callback(interaction)
        self.service.reload_settings.assert_not_called()
        interaction = self.interaction(user=10)
        await self.command('reload').callback(interaction)
        self.service.reload_settings.assert_called_once()

    async def test_join_uses_callers_voice_channel_without_parameters(self):
        self.service.change_channel = AsyncMock()
        interaction = self.interaction(user=10)
        interaction.user.voice.channel = Mock(spec=discord.VoiceChannel, id=123)
        self.assertEqual(self.command('join').parameters, [])
        await self.command('join').callback(interaction)
        self.service.change_channel.assert_awaited_once_with(123)

    async def test_join_requires_ordinary_voice_channel(self):
        self.service.change_channel = AsyncMock()
        for voice in (None, Mock(channel=Mock(spec=discord.StageChannel))):
            interaction = self.interaction(user=10)
            interaction.user.voice = voice
            await self.command('join').callback(interaction)
            interaction.response.send_message.assert_awaited_once()
            interaction.response.defer.assert_not_awaited()
        self.service.change_channel.assert_not_awaited()

    async def test_leave_disables_rejoin_clears_audio_and_persists(self):
        self.service.voice = Mock()
        self.service.voice.disconnect = AsyncMock()
        self.service.audio_jobs.put((1, 'viewer', b'pcm'))
        self.service.on_voice_enabled_changed = Mock()
        interaction = self.interaction(user=10)
        await self.command('leave').callback(interaction)
        self.assertFalse(self.service.voice_enabled)
        self.assertFalse(self.service.settings['auto_join'])
        self.assertIsNone(self.service.voice)
        self.assertTrue(self.service.audio_jobs.empty())
        self.service.on_voice_enabled_changed.assert_called_once_with(False)
        interaction.followup.send.assert_awaited_once()

    async def test_sync_only_once_after_reconnect(self):
        self.tree.sync = AsyncMock()
        await self.service.client.on_ready()
        await self.service.client.on_ready()
        self.assertEqual(self.tree.sync.await_args_list, [call(), call(guild=discord.Object(id=1))])
        self.assertEqual(len(self.tree.get_commands()), 15)
        self.assertEqual(self.tree.get_commands(guild=discord.Object(id=1)), [])

    async def test_failed_global_sync_does_not_clear_guild_commands_and_retries(self):
        self.tree.sync = AsyncMock(side_effect=[RuntimeError('network'), [], []])
        self.tree.clear_commands = Mock()
        await self.service.client.on_ready()
        self.tree.clear_commands.assert_not_called()
        await self.service.client.on_ready()
        self.assertEqual(self.tree.sync.await_args_list, [call(), call(), call(guild=discord.Object(id=1))])
        self.tree.clear_commands.assert_called_once_with(guild=discord.Object(id=1))

    async def test_user_and_server_info_do_not_ping_users(self):
        for name in ('userinfo', 'serverinfo'):
            interaction = self.interaction()
            interaction.user.joined_at = None
            interaction.guild.text_channels = []
            interaction.guild.voice_channels = []
            await self.command(name).callback(interaction)
            options = interaction.response.send_message.call_args.kwargs
            self.assertTrue(options['ephemeral'])
            self.assertFalse(options['allowed_mentions'].everyone)
            self.assertFalse(options['allowed_mentions'].users)

    async def test_roll_results_and_invalid_dice(self):
        interaction = self.interaction()
        with patch('vtuber.discord.discord_fun.secrets.randbelow', side_effect=[0, 5]):
            await self.command('roll').callback(interaction, '2d6')
        self.assertIn('合計 7', interaction.response.send_message.call_args.args[0])
        for dice in ('0d6', '21d6', '1d1', '1d1001', 'oops'):
            interaction = self.interaction()
            await self.command('roll').callback(interaction, dice)
            self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])

    async def test_choose_and_poll_validate_options(self):
        interaction = self.interaction()
        with patch('vtuber.discord.discord_fun.secrets.choice', return_value='茶'):
            await self.command('choose').callback(interaction, '咖啡 | 茶')
        self.assertEqual(interaction.response.send_message.call_args.args[0], '抽中了：茶')
        self.assertFalse(interaction.response.send_message.call_args.kwargs['allowed_mentions'].everyone)
        for options in ('只有一個', '甲||乙', '甲|甲'):
            for name in ('choose', 'poll'):
                interaction = self.interaction()
                if name == 'choose':
                    await self.command(name).callback(interaction, options)
                else:
                    await self.command(name).callback(interaction, '問題', options)
                self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])

    async def test_native_poll_options_duration_and_permissions(self):
        interaction = self.interaction()
        await self.command('poll').callback(interaction, '晚餐？', '飯 | 麵', 2, True)
        ballot = interaction.response.send_message.call_args.kwargs['poll']
        self.assertEqual(ballot.question, '晚餐？')
        self.assertEqual([answer.text for answer in ballot.answers], ['飯', '麵'])
        self.assertTrue(ballot.multiple)
        for user_allowed, bot_allowed in ((False, True), (True, False)):
            interaction = self.interaction()
            interaction.permissions.send_polls = user_allowed
            interaction.app_permissions.send_polls = bot_allowed
            await self.command('poll').callback(interaction, '問題', '甲|乙')
            self.assertNotIn('poll', interaction.response.send_message.call_args.kwargs)

    async def test_avatar_and_interactive_commands_reject_other_guilds(self):
        interaction = self.interaction()
        interaction.user.display_name = '觀眾'
        interaction.user.display_avatar.with_size.return_value.url = 'https://cdn.discordapp.com/example.png'
        await self.command('avatar').callback(interaction)
        embed = interaction.response.send_message.call_args.kwargs['embed']
        self.assertEqual(embed.image.url, 'https://cdn.discordapp.com/example.png')
        for name, args in (('roll', ()), ('choose', ('甲|乙',)), ('poll', ('問題', '甲|乙')), ('avatar', ())):
            interaction = self.interaction(guild=999)
            await self.command(name).callback(interaction, *args)
            self.assertIn('沒有使用此指令的權限', interaction.response.send_message.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
