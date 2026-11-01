"""Song requests and queue control via discord.py application commands."""
import asyncio
import discord
from discord import app_commands
from vtuber.discord.command_checks import ServiceCog


class MusicCommands(ServiceCog):
    music = app_commands.Group(name='music', description='YouTube 點歌、待播清單與播放控制')

    async def perform(self, interaction, action, control=False, **values):
        if not await self.check(interaction, control=control):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await asyncio.to_thread(self.service.song_action, action,
                interaction.user.display_name, source='discord', **values)
            if action == 'list':
                rows = result['songs'][:15]
                message = '待播清單為空。' if not rows else '\n'.join(
                    f'{index}. #{row["id"]} {row["title"][:70]} — {row["requestedBy"][:40]}'
                    for index, row in enumerate(rows, 1))
                if len(result['songs']) > 15:
                    message += f'\n其餘 {len(result["songs"]) - 15} 首可在點歌網頁查看。'
            else:
                message = result['message']
            await interaction.followup.send(message[:1900], ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception:
            await interaction.followup.send('點歌服務暫時無法使用，請稍後再試。', ephemeral=True)

    @music.command(name='add', description='加入 YouTube 影片到待播清單')
    async def add(self, interaction: discord.Interaction, url: str):
        await self.perform(interaction, 'add', url=url)

    @music.command(name='queue', description='查看目前待播清單')
    async def queue(self, interaction: discord.Interaction):
        await self.perform(interaction, 'list')

    @music.command(name='skip', description='切換下一首並累計播放次數（白名單）')
    async def skip(self, interaction: discord.Interaction):
        await self.perform(interaction, 'skip', control=True)

    @music.command(name='pause', description='暫停播放（白名單）')
    async def pause(self, interaction: discord.Interaction):
        await self.perform(interaction, 'pause', control=True)

    @music.command(name='resume', description='繼續播放（白名單）')
    async def resume(self, interaction: discord.Interaction):
        await self.perform(interaction, 'resume', control=True)

    @music.command(name='volume', description='設定點歌音量（白名單）')
    async def volume(self, interaction: discord.Interaction, value: app_commands.Range[int, 0, 100]):
        await self.perform(interaction, 'volume', control=True, volume=value)

    @music.command(name='remove', description='依待播編號移除歌曲，不累計次數（白名單）')
    async def remove(self, interaction: discord.Interaction, request_id: app_commands.Range[int, 1]):
        await self.perform(interaction, 'remove', control=True, id=request_id)
