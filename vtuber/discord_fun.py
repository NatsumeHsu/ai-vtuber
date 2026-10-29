"""Lightweight interactive commands independent of AI models."""
from datetime import timedelta
import re
import secrets
import discord
from discord import app_commands


def split_options(text):
    options = [part.strip() for part in re.split(r'[|｜,，\n]', text)]
    if any(not part for part in options):
        raise ValueError('選項不可留空，請用 | 分隔。')
    return options


def register_fun_commands(tree, check):
    @tree.command(name='roll', description='擲骰，例如 1d6、2d20')
    @app_commands.describe(dice='骰子格式 NdM：N 顆、每顆 M 面，預設 1d6')
    async def roll(interaction: discord.Interaction, dice: str = '1d6'):
        if not await check(interaction):
            return
        match = re.fullmatch(r'(\d{1,2})[dD](\d{1,4})', dice.strip())
        if not match or not (1 <= int(match[1]) <= 20 and 2 <= int(match[2]) <= 1000):
            await interaction.response.send_message('請使用 NdM，例如 2d6；可擲 1～20 顆，每顆 2～1000 面。', ephemeral=True)
            return
        count, sides = map(int, match.groups())
        results = [secrets.randbelow(sides) + 1 for _ in range(count)]
        await interaction.response.send_message(
            f'🎲 {count}d{sides}：' + '、'.join(map(str, results)) + f'（合計 {sum(results)}）',
            allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name='choose', description='從選項中隨機抽一個，用 | 分隔')
    async def choose(interaction: discord.Interaction, options: str):
        if not await check(interaction):
            return
        try:
            choices = split_options(options)
            if not 2 <= len(choices) <= 20 or any(len(item) > 100 for item in choices):
                raise ValueError('請提供 2～20 個選項，每個最多 100 字，用 | 分隔。')
            if len(set(choices)) != len(choices):
                raise ValueError('選項不可重複。')
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(f'抽中了：{secrets.choice(choices)}',
                                                allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name='poll', description='建立 Discord 投票，選項用 | 分隔')
    @app_commands.describe(question='投票問題，最多 300 字', options='2～10 個選項，每個最多 55 字，用 | 分隔',
                           hours='投票持續時間，1～168 小時', multiple='是否允許複選')
    async def poll(interaction: discord.Interaction, question: str, options: str,
                   hours: app_commands.Range[int, 1, 168] = 24, multiple: bool = False):
        if not await check(interaction):
            return
        if not interaction.permissions.send_polls or not interaction.app_permissions.send_polls:
            await interaction.response.send_message('你與機器人都需要此頻道的「建立投票」權限。', ephemeral=True)
            return
        try:
            choices = split_options(options)
            if not question.strip() or len(question) > 300:
                raise ValueError('投票問題須為 1～300 字。')
            if not 2 <= len(choices) <= 10 or any(len(item) > 55 for item in choices):
                raise ValueError('請提供 2～10 個選項，每個最多 55 字，用 | 分隔。')
            if len(set(choices)) != len(choices):
                raise ValueError('投票選項不可重複。')
            if not 1 <= hours <= 168:
                raise ValueError('投票時間須為 1～168 小時。')
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        ballot = discord.Poll(question=question.strip(), duration=timedelta(hours=hours), multiple=multiple)
        for choice in choices:
            ballot.add_answer(text=choice)
        await interaction.response.send_message(poll=ballot, allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name='avatar', description='查看自己或指定成員的頭像')
    async def avatar(interaction: discord.Interaction, member: discord.Member = None):
        if not await check(interaction):
            return
        user = member or interaction.user
        asset = user.display_avatar.with_size(1024)
        embed = discord.Embed(title=f'{user.display_name} 的頭像')
        embed.set_image(url=asset.url)
        await interaction.response.send_message(embed=embed, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
