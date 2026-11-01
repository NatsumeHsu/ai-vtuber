"""Shared async command entry point."""
from .models import Identity
from .service import RPGService


class RPGCommands:
    def __init__(self, service: RPGService):
        self.service = service

    async def execute(self, identity: Identity, command: str, name='冒險者') -> str:
        return await self.service.execute(identity, command, name)
