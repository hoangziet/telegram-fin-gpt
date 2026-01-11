"""
Constants and Enums for FinGPT.
"""

from enum import Enum
from typing import Dict, List


class TransactionType(str, Enum):
    """Transaction type enum."""
    INCOME = "thu"
    EXPENSE = "chi"


class ActionType(str, Enum):
    """AI action types."""
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    QUERY = "query"
    REPORT = "report"
    EXPORT = "export"
    CLEAR = "clear"
    UNDO = "undo"
    HELP = "help"
    UNKNOWN = "unknown"


class ReportType(str, Enum):
    """Report period types."""
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


# Fixed categories - không tự động tạo mới
EXPENSE_CATEGORIES: List[str] = [
    "Ăn uống",
    "Di chuyển", 
    "Mua sắm",
    "Giải trí",
    "Hóa đơn",
    "Sức khỏe",
    "Học tập",
    "Quà tặng",
    "Khác"
]

INCOME_CATEGORIES: List[str] = [
    "Lương",
    "Thưởng",
    "Thu khác"
]

ALL_CATEGORIES: List[str] = EXPENSE_CATEGORIES + INCOME_CATEGORIES

# Category icons
CATEGORY_ICONS: Dict[str, str] = {
    "Ăn uống": "🍜",
    "Di chuyển": "🚗",
    "Mua sắm": "🛒",
    "Giải trí": "🎮",
    "Hóa đơn": "🏠",
    "Sức khỏe": "💊",
    "Học tập": "📚",
    "Quà tặng": "🎁",
    "Khác": "❓",
    "Lương": "💼",
    "Thưởng": "🎯",
    "Thu khác": "💰",
}
