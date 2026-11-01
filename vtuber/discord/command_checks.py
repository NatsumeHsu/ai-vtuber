"""Shared guild restrictions for application-command Cogs."""
from discord.ext import commands

class ServiceCog(commands.Cog):
    def __init__(self, service):
        self.service = service

    async def check(self, interaction, control=False):
        allowed = (self.service.command_allowed(interaction.user.id, interaction.guild_id) if control
                   else interaction.guild_id == self.service.guild_id)
        if not allowed:
            await interaction.response.send_message('你沒有使用此指令的權限。', ephemeral=True)
        return allowed
