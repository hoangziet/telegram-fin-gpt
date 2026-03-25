"""
Bot Manager to handle multiple Bot polling tasks dynamically.
"""
import asyncio
import logging
from aiogram import Bot, Dispatcher
from typing import Dict

from .database import db
from ..handlers.messages import router

logger = logging.getLogger(__name__)

class BotManager:
    """Manages dynamic bot polling."""
    def __init__(self):
        self.running_tasks: Dict[str, asyncio.Task] = {}
        self.dp = Dispatcher()
        self.dp.include_router(router)
        self.bots: Dict[str, Bot] = {}

    async def start_all(self):
        """Khởi động tất cả bot đã được add token trong DB."""
        bots_data = await db.get_all_bots()
        for b in bots_data:
            token = b["bot_token"]
            await self._start_bot(token)
            
        logger.info(f"✅ Đã khởi động {len(self.running_tasks)} bots từ Database.")
        
        # Start a loop to check for new bots periodically
        asyncio.create_task(self._watch_new_bots())

    async def _start_bot(self, token: str):
        if token in self.running_tasks:
            return
        
        bot = Bot(token=token)
        self.bots[token] = bot
        
        # Start polling in a separate task
        task = asyncio.create_task(self.dp.start_polling(bot))
        self.running_tasks[token] = task
        logger.info(f"🚀 Started polling for Bot Token: {token[:10]}...")

    async def _watch_new_bots(self):
        """Background task checking if new bot tokens were added or changed to DB."""
        while True:
            await asyncio.sleep(15)  # Check every 15 secs
            bots_data = await db.get_all_bots()
            active_tokens = {b["bot_token"] for b in bots_data if b["bot_token"]}
            
            # Start new bots
            for token in active_tokens:
                if token not in self.running_tasks:
                    logger.info(f"✨ Found new Bot Token in DB, starting it...")
                    await self._start_bot(token)
            
            # Stop removed or changed bots
            for token in list(self.running_tasks.keys()):
                if token not in active_tokens:
                    logger.info(f"🛑 Stopping unused Bot Token: {token[:10]}...")
                    self.running_tasks[token].cancel()
                    del self.running_tasks[token]
                    if token in self.bots:
                        await self.bots[token].session.close()
                        del self.bots[token]

bot_manager = BotManager()
