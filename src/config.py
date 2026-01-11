"""
FinGPT Configuration.
Centralized config management.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    """Application configuration."""
    
    # Telegram
    BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    
    # Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    
    # Database
    DB_PATH: str = os.getenv("DB_PATH", "data/finance.db")
    
    # Debug
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    DEBUG_DIR: str = "debug"
    
    def validate(self) -> None:
        """Validate required config."""
        if not self.BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not self.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is required")


config = Config()
