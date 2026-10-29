"""Global slash commands; voice controls require the local ID allowlist."""
import math
import asyncio
import time
import discord
from discord import app_commands


def register_commands(service):
    tree = app_commands.CommandTree(service.client)
    guild = discord.Object(id=service.guild_id)

    async def check(interaction, control=False):
        allowed = (service.command_allowed(interaction.user.id, interaction.guild_id) if control
                   else interaction.guild_id == service.guild_id)
        if not allowed:
            await interaction.response.send_message('你沒有使用此指令的權限。', ephemeral=True)
        return allowed

    @tree.command(name='help', description='查看白璃的指令說明')
    async def help_command(interaction: discord.Interaction):
        if not await check(interaction):
            return
        await interaction.response.send_message(
            '一般指令：\n/help：指令說明\n/ping：連線延遲\n/status：機器人狀態\n'
            '/serverinfo：伺服器資訊\n/userinfo：使用者資訊\n'
            '/roll：擲骰（例如 2d6）\n/choose：抽籤（選項用 | 分隔）\n'
            '/poll：建立投票\n/avatar：查看頭像\n\n'
            '僅限白名單使用者：\n/join：加入你目前所在的語音頻道\n/leave：離開語音並停止自動重連\n/reload：重新載入設定（保留 bot_token）',
            ephemeral=True)

    @tree.command(name='ping', description='查看 Discord 連線延遲')
    async def ping(interaction: discord.Interaction):
        if not await check(interaction):
            return
        latency = service.client.latency
        value = f'{latency * 1000:.0f} ms' if math.isfinite(latency) else '連線測量中'
        await interaction.response.send_message(f'Pong！{value}', ephemeral=True)

    @tree.command(name='status', description='查看白璃的連線與語音狀態')
    async def status(interaction: discord.Interaction):
        if not await check(interaction):
            return
        connected = service.voice is not None and service.voice.is_connected()
        voice = f'已連線：{service.voice.channel.name}' if connected else '尚未連線'
        seconds = max(0, int(time.monotonic() - service.started_at))
        await interaction.response.send_message(
            f'白璃服務運行中\n運行時間：{seconds // 3600} 小時 {(seconds % 3600) // 60} 分\n'
            f'語音：{voice}\n自動加入語音：{"開" if service.voice_enabled else "關"}\n'
            f'等待辨識的語音片段：{service.audio_jobs.qsize()}', ephemeral=True)

    @tree.command(name='serverinfo', description='查看目前伺服器的基本資訊')
    async def serverinfo(interaction: discord.Interaction):
        if not await check(interaction):
            return
        server = interaction.guild
        await interaction.response.send_message(
            f'伺服器：{server.name}\nID：{server.id}\n成員數：{server.member_count if server.member_count is not None else "未知"}\n'
            f'文字頻道：{len(server.text_channels)}\n語音頻道：{len(server.voice_channels)}',
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name='userinfo', description='查看自己或指定成員的基本資訊')
    async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
        if not await check(interaction):
            return
        user = member or interaction.user
        joined = user.joined_at.strftime('%Y-%m-%d %H:%M UTC') if getattr(user, 'joined_at', None) else '未知'
        await interaction.response.send_message(
            f'名稱：{user.display_name}\nID：{user.id}\n機器人：{"是" if user.bot else "否"}\n加入時間：{joined}',
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name='join', description='加入你目前所在的語音頻道（僅限指定使用者）')
    async def join(interaction: discord.Interaction):
        if not await check(interaction, control=True):
            return
        voice = getattr(interaction.user, 'voice', None)
        channel = getattr(voice, 'channel', None)
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message('請先加入普通語音頻道，再使用 /join。', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await service.change_channel(channel.id)
            await interaction.followup.send('語音目標已更新，未連線時將自動加入。', ephemeral=True)
        except Exception:
            await interaction.followup.send('切換失敗，請檢查頻道與機器人權限。', ephemeral=True)

    @tree.command(name='leave', description='離開語音並停止自動重連（僅限指定使用者）')
    async def leave(interaction: discord.Interaction):
        if not await check(interaction, control=True):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await service.leave_channel()
            await interaction.followup.send('已離開語音；使用 /join 或介面更新頻道可重新加入。', ephemeral=True)
        except Exception:
            await interaction.followup.send('離開語音失敗，請查看本機記錄。', ephemeral=True)

    @tree.command(name='reload', description='重新載入設定，保留目前 bot_token（僅限指定使用者）')
    async def reload_settings(interaction: discord.Interaction):
        if not await check(interaction, control=True):
            return
        await interaction.response.send_message('開始重新載入設定，Discord 將重新連線、子程序將重啟；bot_token 保持原值。', ephemeral=True)
        try:
            accepted = await asyncio.to_thread(service.reload_settings)
            if not accepted:
                service.on_status('已有設定重新載入工作或服務正在關閉。')
        except Exception as exc:
            service.on_status(f'設定重新載入未啟動：{type(exc).__name__}')

    from .discord_fun import register_fun_commands
    register_fun_commands(tree, check)

    synced = False
    @service.client.event
    async def on_ready():
        nonlocal synced
        if synced:
            return
        try:
            await tree.sync()
            tree.clear_commands(guild=guild)
            await tree.sync(guild=guild)
            synced = True
        except Exception:
            service.on_status('Discord 指令註冊失敗，請檢查 applications.commands 授權')

    @tree.error
    async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        service.on_status(f'Discord 指令失敗：{type(error).__name__}')
        if interaction.response.is_done():
            await interaction.followup.send('指令執行失敗，請稍後重試。', ephemeral=True)
        else:
            await interaction.response.send_message('指令執行失敗，請稍後重試。', ephemeral=True)

    return tree
