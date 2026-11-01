"""Authenticated identity supplied by platform events."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    platform: str
    user_id: str

    def __post_init__(self):
        if self.platform not in ('twitch', 'discord'):
            raise ValueError('platform 必須是 twitch 或 discord')
        if not isinstance(self.user_id, str) or not self.user_id.isascii() or not self.user_id.isdecimal() or int(self.user_id) <= 0:
            raise ValueError('user_id 必須是平台事件中的正整數 ID')
