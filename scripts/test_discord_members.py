"""Member service events and permissions without Discord network access."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord
from vtuber.discord_members import MemberServices


class MemberTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = SimpleNamespace(guild_id=1, client=Mock(), on_status=Mock(), settings={
            'member_services': {'welcome_channel_id': 2}})
        self.events = {}
        def event(function):
            self.events[function.__name__] = function
            return function
        self.service.client.event.side_effect = event
        self.guild = Mock(id=1, name='測試伺服器', member_count=5)
        self.guild.name = '測試伺服器'
        self.member = Mock(id=42, bot=False, pending=False, roles=[], guild=self.guild)
        self.member.display_name = '新人'
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.guild = self.guild
        self.channel.send = AsyncMock()
        self.guild.get_channel.return_value = self.channel
        self.feature = MemberServices(self.service)
        self.feature.register()

    async def test_join_welcomes(self):
        await self.events['on_member_join'](self.member)
        self.channel.send.assert_awaited_once()
        options = self.channel.send.call_args.kwargs['allowed_mentions']
        self.assertFalse(options.everyone)
        self.assertFalse(options.users)

    async def test_bot_and_other_guild_are_ignored(self):
        self.member.bot = True
        await self.events['on_member_join'](self.member)
        self.member.bot = False
        self.guild.id = 999
        await self.events['on_member_join'](self.member)
        self.channel.send.assert_not_awaited()

    async def test_only_welcome_event_is_registered(self):
        self.assertEqual(set(self.events), {'on_member_join'})

    async def test_send_failure_is_reported(self):
        self.channel.send.side_effect = RuntimeError('network')
        await self.events['on_member_join'](self.member)
        self.service.on_status.assert_called_once()

    async def test_unconfigured_features_do_nothing(self):
        self.feature.settings = {}
        self.assertFalse(self.feature.enabled)
        await self.events['on_member_join'](self.member)
        self.channel.send.assert_not_awaited()

    async def test_cross_guild_channel_rejected(self):
        self.channel.guild = SimpleNamespace(id=999)
        await self.feature.announce(self.member, self.guild)
        self.channel.send.assert_not_awaited()
        self.service.on_status.assert_called_once()


if __name__ == '__main__':
    unittest.main()
