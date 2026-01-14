"""
Data models for FinGPT.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, List

from .constants import TransactionType, ActionType, ReportType


@dataclass
class Transaction:
    """Transaction model."""
    id: int
    user_id: int
    amount: float
    category: str
    note: Optional[str]
    type: TransactionType
    transaction_date: date
    created_at: datetime
    updated_at: datetime
    is_deleted: bool = False
    
    @classmethod
    def from_row(cls, row: dict) -> "Transaction":
        """Create from database row."""
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            amount=row["amount"],
            category=row["category"],
            note=row["note"],
            type=TransactionType(row["type"]),
            transaction_date=date.fromisoformat(row["transaction_date"]) if isinstance(row["transaction_date"], str) else row["transaction_date"],
            created_at=datetime.fromisoformat(row["created_at"]) if isinstance(row["created_at"], str) else row["created_at"],
            updated_at=datetime.fromisoformat(row["updated_at"]) if isinstance(row["updated_at"], str) else row["updated_at"],
            is_deleted=bool(row["is_deleted"])
        )


@dataclass
class AIAction:
    """Parsed action from AI."""
    action: ActionType
    amount: Optional[float] = None
    category: Optional[str] = None
    tx_type: Optional[TransactionType] = None
    
    # Date/time - computed from AI's date_offset and time_of_day
    date_offset: int = 0  # 0=today, 1=yesterday, 2=day before
    time_of_day: Optional[str] = None  # sáng/trưa/chiều/tối
    target_date: Optional[date] = None  # Resolved date
    
    # Reference for update/delete
    transaction_id: Optional[int] = None
    keyword: Optional[str] = None
    
    # Query/Report
    report_type: Optional[ReportType] = None
    limit: int = 10
    
    # Response
    message: Optional[str] = None


@dataclass
class Report:
    """Report model."""
    start_date: date
    end_date: date
    total_income: float
    total_expense: float
    balance: float
    by_category: List[dict] = field(default_factory=list)
    transactions: List[Transaction] = field(default_factory=list)
