"""Configured new-member welcome messages."""
import discord


class MemberServices:
    def __init__(self, service):
        self.service = service
        self.settings = service.settings.get('member_services', {})

    @property
    def enabled(self):
        return bool(self.settings.get('welcome_channel_id'))

    def accepts(self, guild_id, user):
        return (guild_id == self.service.guild_id and
                not (self.settings.get('ignore_bots', True) and user.bot))

    def render(self, template, user, guild):
        values = {'user': user.display_name if hasattr(user, 'display_name') else user.name,
                  'mention': f'<@{user.id}>', 'user_id': str(user.id), 'server': guild.name,
                  'member_count': str(guild.member_count) if guild.member_count is not None else '未知'}
        # A single pass prevents member names containing placeholders from being expanded.
        import re
        return re.sub(r'\{(user|mention|user_id|server|member_count)\}',
                      lambda match: values[match[1]], template)[:2000]

    async def announce(self, user, guild):
        try:
            channel_id = int(self.settings.get('welcome_channel_id', 0))
            if not channel_id:
                return
            channel = guild.get_channel(channel_id) or await self.service.client.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
                raise ValueError('通知頻道必須是同一伺服器的文字頻道')
            default = '歡迎 {mention} 加入 {server}！'
            text = self.render(str(self.settings.get('welcome_message', default)), user, guild)
            if text.strip():
                await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except Exception as exc:
            self.service.on_status(f'Discord 歡迎訊息失敗：{type(exc).__name__}，請檢查頻道與傳送訊息權限')

    def register(self):
        @self.service.client.event
        async def on_member_join(member):
            if not self.accepts(member.guild.id, member):
                return
            await self.announce(member, member.guild)
