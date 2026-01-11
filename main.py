"""
FinGPT V2 - Telegram Finance Bot.
Entry point.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from src.config import config
from src.services import db
from src.handlers import router

# Logging
logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    """Main entry point."""
    # Validate config
    config.validate()
    
    logger.info("🚀 Starting FinGPT V2...")
    
    # Init database
    await db.init()
    logger.info("✅ Database ready")
    
    # Init bot
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    
    logger.info("✅ Bot running! Ctrl+C to stop.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
