import asyncio
from typing import Any, Dict


class Agent:
    def __init__(self, name: str, inbox: asyncio.Queue) -> None:
        self.name = name
        self.inbox = inbox

    async def start(self) -> None:
        while True:
            msg = await self.inbox.get()
            if msg is None:
                break
            await self.handle(msg)

    async def handle(self, msg: Dict[str, Any]) -> None:
        raise NotImplementedError
