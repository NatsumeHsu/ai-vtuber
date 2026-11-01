"""Real Idleology database and platform adapters, no login or network."""
import asyncio
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord
from discord.ext import commands
from vtuber.rpg import Identity, RPGService
from vtuber.rpg.discord_adapter import RPGCog, GAME_COGS, install_game


class RPGTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'game.db'
        self.now = 1000
        self.game = RPGService(self.path, world_id=1, clock=lambda: self.now)
        self.addAsyncCleanup(self.game.close)
        self.discord = Identity('discord', '123')
        self.twitch = Identity('twitch', '123')

    async def run_command(self, text, identity=None):
        return await self.game.execute(identity or self.discord, text)

    async def test_registration_initializes_upstream_schema_and_persists(self):
        await self.run_command('register 測試')
        self.assertIn('金幣 200', await self.run_command('profile'))
        for skill in ('mining', 'fishing', 'woodcutting'):
            self.assertIsNotNone(await self.game.database.skills.get_data('123', '1', skill))
        await self.game.close()
        self.assertIn('測試', await self.run_command('profile'))
        self.assertIn('金幣 200', await self.run_command('profile'))

    async def test_concurrent_registration_grants_starter_once(self):
        results = await asyncio.gather(*(self.run_command('register 測試') for _ in range(8)))
        self.assertEqual(sum('已建立' in result for result in results), 1)
        self.assertIn('金幣 200', await self.run_command('profile'))

    async def test_ids_are_platform_namespaced(self):
        await self.run_command('register Discord')
        await self.run_command('register Twitch', self.twitch)
        self.assertIn('Discord', await self.run_command('profile'))
        self.assertIn('Twitch', await self.run_command('profile', self.twitch))
        self.assertEqual(await self.game._canonical(self.twitch), '-123')

    async def test_twitch_player_can_fight_without_discord_registration(self):
        await self.run_command('register 勇者', self.twitch)
        def hit(player, monster):
            monster.hp = 0
        with patch('vtuber.rpg.combat.engine.process_player_turn', side_effect=hit):
            self.assertIn('戰勝', await self.run_command('combat', self.twitch))
        row = await self.game.database.users.get('-123', '1')
        self.assertEqual(row['combat_stamina'], 9)

    async def test_link_is_single_use_and_shares_native_user_row(self):
        await self.run_command('register 共用')
        reply = await self.run_command('link')
        code = re.search(r'link ([a-f0-9]+)', reply)[1]
        self.assertIn('綁定完成', await self.run_command('link ' + code, self.twitch))
        self.assertEqual(await self.run_command('profile'), await self.run_command('profile', self.twitch))
        self.assertIn('無效', await self.run_command('link ' + code, Identity('twitch', '456')))
        self.assertEqual(await self.game._canonical(self.twitch), '123')

    async def test_expired_link_and_existing_player_are_rejected(self):
        await self.run_command('register 共用')
        code = re.search(r'link ([a-f0-9]+)', await self.run_command('link'))[1]
        self.now += 301
        self.assertIn('過期', await self.run_command('link ' + code, self.twitch))
        code = re.search(r'link ([a-f0-9]+)', await self.run_command('link'))[1]
        await self.run_command('register 獨立', self.twitch)
        self.assertIn('無法直接覆蓋', await self.run_command('link ' + code, self.twitch))
        self.assertIn('獨立', await self.run_command('profile', self.twitch))

    async def test_native_view_lock_blocks_twitch_mutations(self):
        await self.run_command('register 共用')
        code = re.search(r'link ([a-f0-9]+)', await self.run_command('link'))[1]
        await self.run_command('link ' + code, self.twitch)
        self.game.state_manager.set_active('123', 'inventory')
        self.assertIn('其他 RPG 操作', await self.run_command('combat', self.twitch))
        self.game.state_manager.clear_active('123')
        self.assertIn('金幣 200', await self.run_command('profile'))

    async def test_combat_uses_upstream_turns_and_rewards(self):
        await self.run_command('register 戰士')
        # Real reward, XP and save paths; deterministic turns ensure a victory.
        def hit(player, monster):
            monster.hp = 0
        with patch('vtuber.rpg.combat.engine.process_player_turn', side_effect=hit):
            result = await self.run_command('combat')
        self.assertIn('戰勝', result)
        row = await self.game.database.users.get('123', '1')
        self.assertGreater(row['gold'], 200)
        self.assertEqual(row['combat_stamina'], 9)
        self.assertGreater(row['experience'] + row['level'], 1)
        if await self.game.database.users.get_pending_packages('123', '1'):
            self.assertIn('配點完成', await self.run_command('package 1'))

    async def test_failed_reward_rolls_back_stamina_and_balances(self):
        await self.run_command('register 戰士')
        before = dict(await self.game.database.users.get('123', '1'))
        def hit(player, monster):
            monster.hp = 0
        with patch('vtuber.rpg.combat.engine.process_player_turn', side_effect=hit), patch(
                'vtuber.rpg.combat.apply_victory_rewards', new=AsyncMock(side_effect=RuntimeError('test failure'))):
            with self.assertRaises(RuntimeError):
                await self.run_command('combat')
        self.assertEqual(dict(await self.game.database.users.get('123', '1')), before)
        self.assertFalse(self.game.state_manager.is_active('123'))

    async def test_assets_work_outside_project_directory(self):
        from vtuber.rpg.idleology.core.combat.mobgen.gen_mob import _load_monster_rows
        from vtuber.rpg.idleology.core.combat.economy.experience import ExperienceManager
        original = os.getcwd()
        try:
            os.chdir(self.temp.name)
            self.assertGreater(len(_load_monster_rows()), 0)
            self.assertTrue(ExperienceManager._get_exp_table())
        finally:
            os.chdir(original)

    async def test_prototype_save_is_backed_up(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE rpg_players (player_id INTEGER, gold INTEGER)')
            db.execute('INSERT INTO rpg_players VALUES (1, 777)')
            db.commit()
        await self.game.start()
        with closing(sqlite3.connect(str(self.path) + '.prototype.bak')) as db:
            self.assertEqual(db.execute('SELECT gold FROM rpg_players').fetchone()[0], 777)

    async def test_world_mismatch_is_rejected(self):
        await self.game.start()
        other = RPGService(self.path, world_id=2)
        with self.assertRaises(ValueError):
            await other.start()

    async def test_host_routes_twitch_requests_to_existing_game(self):
        from vtuber.discord.discord_host import DiscordHost
        await self.game.start()
        host = DiscordHost.__new__(DiscordHost)
        cog = SimpleNamespace(game=self.game)
        host.discord = SimpleNamespace(loop=asyncio.get_running_loop(),
                                       client=SimpleNamespace(get_cog=lambda name: cog))
        reply = await asyncio.to_thread(host.dispatch, 'rpg_action', '123', 'Twitch', 'register 共用勇者')
        self.assertIn('已建立', reply)
        self.assertIn('共用勇者', await self.run_command('profile', self.twitch))

    async def test_full_native_game_loads_with_existing_bot(self):
        async with commands.Bot(command_prefix=[], help_command=None, intents=discord.Intents.none()) as bot:
            service = SimpleNamespace(client=bot, guild_id=1)
            cog = RPGCog(service)
            await bot.add_cog(cog)
            await install_game(service, {'database': str(self.path)})
            for module in GAME_COGS:
                self.assertIn('vtuber.rpg.idleology.cogs.' + module, sys.modules)
            for name in ('register', 'combat', 'inventory', 'gather', 'settlement', 'journey', 'partner', 'rpg'):
                self.assertIsNotNone(bot.tree.get_command(name))
            self.assertIsNone(bot.tree.get_command('rite_debug'))
            self.assertLess(len(bot.tree.get_commands()), 100)
            self.assertIs(bot.database, cog.game.database)
            translated = await bot.tree.get_command('combat').get_translated_payload(bot.tree, bot.tree.translator)
            self.assertEqual(translated['name'], 'combat')
            self.assertEqual(translated['name_localizations']['zh-TW'], '戰鬥')
            for command in bot.tree.walk_commands():
                if isinstance(command, discord.app_commands.Command) and command.root_parent is not cog.rpg:
                    self.assertTrue(command.checks, command.name)


if __name__ == '__main__':
    unittest.main()
