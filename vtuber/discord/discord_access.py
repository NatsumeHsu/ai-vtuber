"""Owner-only settings commands replace launcher arguments."""
import asyncio
import discord
from discord import app_commands
from vtuber.discord.command_checks import ServiceCog

class AccessCommands(ServiceCog):
    users = app_commands.Group(name='discord-users', description='管理 Discord 指令白名單（伺服器擁有者）')

    async def owner_check(self, interaction):
        if (interaction.guild_id != self.service.guild_id or interaction.guild is None
                or interaction.user.id != interaction.guild.owner_id):
            await interaction.response.send_message('只有設定伺服器的擁有者可管理指令白名單。', ephemeral=True)
            return False
        return True

    async def update(self, interaction, action, user_id=None):
        if not await self.owner_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        ids = await asyncio.to_thread(self.service.manage_users, action, user_id)
        await interaction.followup.send(f'白名單已更新，共 {len(ids)} 人，立即生效。', ephemeral=True)

    @users.command(name='add', description='加入允許使用管理指令的成員')
    @app_commands.describe(member='要加入白名單的伺服器成員')
    async def add(self, interaction: discord.Interaction, member: discord.Member):
        await self.update(interaction, 'add', member.id)

    @users.command(name='remove', description='移除白名單成員')
    async def remove(self, interaction: discord.Interaction, member: discord.Member):
        await self.update(interaction, 'remove', member.id)

    @users.command(name='clear', description='清空指令白名單')
    async def clear(self, interaction: discord.Interaction):
        await self.update(interaction, 'clear')

    @users.command(name='list', description='查看目前指令白名單')
    async def list_users(self, interaction: discord.Interaction):
        if not await self.owner_check(interaction):
            return
        ids = self.service.settings.get('allowed_user_ids', [])
        # Stay within Discord response limits even for a large local list.
        lines = [str(value) for value in ids]
        text = '白名單為空。' if not lines else '指令白名單：\n' + '\n'.join(lines)
        await interaction.response.send_message(text[:1900], ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
