"""Attach Idleology game Cogs to the existing Discord bot, never a second bot."""
import importlib
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from vtuber.discord.command_checks import ServiceCog
from .models import Identity
from .service import RPGService
from .localization import ChineseCommandTranslator, install_localization, instrument_module, rpg_output

# Keep the host's administration, help and lifecycle. Only game Cogs are loaded.
GAME_COGS = tuple(sorted(p.stem for p in (Path(__file__).parent / 'idleology/cogs').glob('*.py')
                         if p.stem not in ('__init__', 'admin', 'owner', 'general')))


class RPGCog(ServiceCog):
    rpg = app_commands.Group(name='rpg', description='Twitch／Discord 共用 RPG')

    def __init__(self, service):
        super().__init__(service)
        self.game = None

    @rpg_output
    async def _execute(self, interaction, command):
        if not await self.check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        if self.game is None:
            await interaction.followup.send('RPG 服務尚未就緒。', ephemeral=True)
            return
        reply = await self.game.execute(Identity('discord', str(interaction.user.id)), command,
                                        interaction.user.display_name)
        await interaction.followup.send(reply, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @rpg.command(name='help', description='查看共用 RPG 指令')
    async def help(self, interaction: discord.Interaction):
        await self._execute(interaction, 'help')

    @rpg.command(name='register', description='快速建立共用 RPG 角色')
    @app_commands.describe(name='角色名稱（最多 24 字）')
    async def register(self, interaction: discord.Interaction, name: str):
        await self._execute(interaction, 'register ' + name)

    @rpg.command(name='profile', description='查看共用 RPG 角色')
    async def profile(self, interaction: discord.Interaction):
        await self._execute(interaction, 'profile')

    @rpg.command(name='inventory', description='查看共用背包摘要')
    async def inventory(self, interaction: discord.Interaction):
        await self._execute(interaction, 'inventory')

    @rpg.command(name='combat', description='使用共用規則進行一次自動戰鬥')
    async def combat(self, interaction: discord.Interaction):
        await self._execute(interaction, 'combat')

    @rpg.command(name='package', description='選擇升級配點，省略選項可查看內容')
    @app_commands.describe(option='1、2 或 3')
    async def package(self, interaction: discord.Interaction, option: app_commands.Range[int, 1, 3] | None = None):
        await self._execute(interaction, 'package' + (f' {option}' if option else ''))

    @rpg.command(name='link', description='私密取得 Twitch 帳號綁定碼')
    async def link(self, interaction: discord.Interaction):
        await self._execute(interaction, 'link')

    async def cog_unload(self):
        if self.game:
            # Character's upstream regen loops lack cog_unload cleanup.
            character = self.service.client.get_cog('character')
            if character:
                character.check_hp.cancel()
                character.check_stamina.cancel()
            for module in GAME_COGS:
                for name, cog in list(self.service.client.cogs.items()):
                    if type(cog).__module__ == 'vtuber.rpg.idleology.cogs.' + module:
                        await self.service.client.remove_cog(name)
            await self.game.close()


async def install_game(service, settings=None):
    """Called by setup_hook, after discord.py initializes its event loop."""
    settings = settings or {}
    bot = service.client
    cog = bot.get_cog('RPGCog')
    if not settings.get('enabled', True):
        return
    game = RPGService(settings.get('database', 'data/rpg.sqlite3'), world_id=service.guild_id,
                      link_ttl_seconds=settings.get('link_ttl_seconds', 300),
                      twitch_prefix=getattr(service, 'rpg_twitch_prefix', '!'))
    await game.start()
    cog.game = game
    bot.database, bot.state_manager, bot.logger = game.database, game.state_manager, game.logger
    # Upstream game Cogs expect these host methods. No token/config from the
    # standalone upstream bot is loaded.
    bot.config = {'admin_user_ids': []}
    localizer = install_localization(bot, game)

    async def check_user_registered(interaction, row):
        if row:
            return True
        await interaction.response.send_message('請先使用 /rpg register 建立角色。', ephemeral=True)
        return False

    async def check_is_active(interaction, user_id):
        if game.state_manager.is_active(user_id):
            await interaction.response.send_message('請先完成或關閉目前的 RPG 互動視窗。', ephemeral=True)
            return False
        return True

    async def allowed(interaction):
        if interaction.guild_id == service.guild_id:
            return True
        await interaction.response.send_message('此 RPG 僅限主伺服器使用。', ephemeral=True)
        return False

    bot.check_user_registered, bot.check_is_active = check_user_registered, check_is_active
    existing = set(bot.cogs)
    try:
        for module in GAME_COGS:
            imported = importlib.import_module('vtuber.rpg.idleology.cogs.' + module)
            instrument_module(imported)
            await imported.setup(bot)
        # Upstream includes a no-cost developer raid command; keep it out of
        # the live game. The event-channel setting must require admin rights.
        bot.tree.remove_command('rite_debug')
        event_setup = bot.tree.get_command('setup_events')
        if event_setup:
            async def administrator(interaction):
                if interaction.permissions.administrator:
                    return True
                raise app_commands.MissingPermissions(['administrator'])
            event_setup.add_check(administrator)
        # Every upstream leaf command gets the same guild restriction as host commands.
        for name in set(bot.cogs) - existing:
            for command in bot.get_cog(name).walk_app_commands():
                if isinstance(command, app_commands.Command):
                    command.add_check(allowed)
        # Import-time button/modal classes live in additional core modules.
        import sys
        for name, module in list(sys.modules.items()):
            if name.startswith('vtuber.rpg.idleology.') and module:
                instrument_module(module)
        objects = set()
        for command in bot.tree.walk_commands():
            binding = getattr(command, 'binding', None)
            if (binding is not None and (isinstance(binding, RPGCog) or type(binding).__module__.startswith('vtuber.rpg.idleology.'))) or (isinstance(command, app_commands.Group) and command.name == 'rpg'):
                objects.add(id(command))
                if isinstance(command, app_commands.Command):
                    objects.update(id(parameter) for parameter in command._params.values())
                    objects.update(id(choice) for parameter in command._params.values() for choice in (parameter.choices or ()))
        await bot.tree.set_translator(ChineseCommandTranslator(localizer.catalog, objects))
    except BaseException:
        for name in set(bot.cogs) - existing:
            await bot.remove_cog(name)
        await game.close()
        cog.game = None
        raise
