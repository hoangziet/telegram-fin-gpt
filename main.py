"""
FinGPT V2 - Telegram Finance Bot.
Entry point with Flask server for Replit Autoscale.
"""

import asyncio
import logging
import threading
from flask import Flask

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

# Flask app for Replit Autoscale keep-alive
app = Flask(__name__)
import os
app.secret_key = os.getenv("DASHBOARD_SECRET", "fingpt-secret-key-2026")

# Init web routes and login manager
from src.web.routes import init_web
init_web(app)

@app.route("/")
def home():
    return '🤖 FinGPT Bot is running! <a href="/login">Login to Dashboard</a>'

@app.route("/health")
def health():
    return {"status": "ok"}

def run_flask():
    """Run Flask in a separate thread."""
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)


async def main():
    """Main entry point."""
    # Validate config
    config.validate()
    
    logger.info("🚀 Starting FinGPT V2...")
    
    # Start Flask server in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Web server started on port 5000")
    
    # Init database
    await db.init()
    logger.info("✅ Database ready")
    
    # Start Multi-tenant Bot Manager
    from src.services.bot_manager import bot_manager
    logger.info("🤖 Starting Multi-tenant Bot Manager...")
    await bot_manager.start_all()
    
    logger.info("✅ System running! Ctrl+C to stop.")
    
    # Keep main alive
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
