"""Probabilistic guild text replies with a channel-local history snapshot."""
import asyncio
import secrets
import discord


class TextReplies:
    def __init__(self, service):
        self.service = service
        self.settings = service.settings.get('text_replies', {})
        self.enabled = self.settings.get('enabled', True)
        self.probability = float(self.settings.get('probability', 0.025))
        self.context_messages = max(0, min(100, int(self.settings.get('context_messages', 10))))
        if not 0 <= self.probability <= 1:
            raise ValueError('discord.text_replies.probability 必須介於 0 與 1')

    @staticmethod
    def entry(message):
        return {'message_id': str(message.id), 'user_id': str(message.author.id),
                'speaker': message.author.display_name, 'text': message.content,
                'bot': message.author.bot, 'created_at': message.created_at.isoformat()}

    def register(self):
        @self.service.client.event
        async def on_message(message):
            if (not self.enabled or message.guild is None or message.guild.id != self.service.guild_id
                    or not isinstance(message.channel, discord.TextChannel) or message.author.bot
                    or message.webhook_id is not None or not message.content.strip()
                    or message.content.lstrip().startswith('/')):
                return
            if secrets.randbelow(1_000_000) >= self.probability * 1_000_000:
                return
            try:
                if not await asyncio.to_thread(self.service.text_ready):
                    return
                previous = [item async for item in message.channel.history(
                    limit=self.context_messages, before=message, oldest_first=False)] if self.context_messages else []
                payload = {**self.entry(message), 'guild_id': str(message.guild.id),
                           'channel_id': str(message.channel.id),
                           'context': [self.entry(item) for item in reversed(previous)]}
                await asyncio.to_thread(self.service.on_text_message, payload)
            except Exception as exc:
                self.service.on_status(f'Discord 文字回覆處理失敗：{type(exc).__name__}')
