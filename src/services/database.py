"""
Database service for FinGPT.
"""

import aiosqlite
import os
from datetime import datetime, date, timedelta
from typing import Optional, List

from ..config import config
from ..models import Transaction, Report
from ..constants import TransactionType


class DatabaseService:
    """Database operations."""
    
    def __init__(self):
        self.db_path = config.DB_PATH
        # Ensure data directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
    
    async def init(self) -> None:
        """Initialize database."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    category TEXT NOT NULL,
                    note TEXT,
                    type TEXT NOT NULL CHECK(type IN ('thu', 'chi')),
                    transaction_date DATE NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_deleted INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_date 
                ON transactions(user_id, transaction_date, is_deleted)
            """)
            await db.commit()
    
    # ==================== CRUD ====================
    
    async def insert(
        self,
        user_id: int,
        amount: float,
        category: str,
        note: Optional[str],
        tx_type: TransactionType,
        tx_date: Optional[date] = None
    ) -> int:
        """Insert transaction."""
        tx_date = tx_date or date.today()
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO transactions 
                (user_id, amount, category, note, type, transaction_date)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, abs(amount), category, note, tx_type.value, tx_date.isoformat())
            )
            await db.commit()
            return cursor.lastrowid
    
    async def update(
        self,
        tx_id: int,
        user_id: int,
        amount: Optional[float] = None,
        category: Optional[str] = None,
        note: Optional[str] = None
    ) -> bool:
        """Update transaction."""
        updates, params = [], []
        
        if amount is not None:
            updates.append("amount = ?")
            params.append(abs(amount))
        if category is not None:
            updates.append("category = ?")
            params.append(category)
        if note is not None:
            updates.append("note = ?")
            params.append(note)
        
        if not updates:
            return False
        
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.extend([tx_id, user_id])
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"UPDATE transactions SET {', '.join(updates)} "
                f"WHERE id = ? AND user_id = ? AND is_deleted = 0",
                params
            )
            await db.commit()
            return cursor.rowcount > 0
    
    async def delete(self, tx_id: int, user_id: int) -> bool:
        """Soft delete transaction."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE transactions SET is_deleted = 1, updated_at = ?
                WHERE id = ? AND user_id = ? AND is_deleted = 0
                """,
                (datetime.now().isoformat(), tx_id, user_id)
            )
            await db.commit()
            return cursor.rowcount > 0
    
    async def get_last(self, user_id: int) -> Optional[Transaction]:
        """Get last transaction."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM transactions WHERE user_id = ? AND is_deleted = 0 "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id,)
            )
            row = await cursor.fetchone()
            return Transaction.from_row(dict(row)) if row else None
    
    async def find(
        self,
        user_id: int,
        keyword: Optional[str] = None,
        category: Optional[str] = None,
        tx_date: Optional[date] = None,
        limit: int = 10
    ) -> List[Transaction]:
        """Find transactions."""
        conditions = ["user_id = ?", "is_deleted = 0"]
        params: list = [user_id]
        
        if keyword:
            conditions.append("note LIKE ?")
            params.append(f"%{keyword}%")
        if category:
            conditions.append("category = ?")
            params.append(category)
        if tx_date:
            conditions.append("transaction_date = ?")
            params.append(tx_date.isoformat())
        
        params.append(limit)
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT * FROM transactions WHERE {' AND '.join(conditions)} "
                f"ORDER BY created_at DESC LIMIT ?",
                params
            )
            rows = await cursor.fetchall()
            return [Transaction.from_row(dict(row)) for row in rows]
    
    # ==================== Reports ====================
    
    async def get_report(self, user_id: int, start: date, end: date) -> Report:
        """Get report for date range."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Income
            cursor = await db.execute(
                "SELECT COALESCE(SUM(amount), 0) as total FROM transactions "
                "WHERE user_id = ? AND type = 'thu' AND is_deleted = 0 "
                "AND transaction_date BETWEEN ? AND ?",
                (user_id, start.isoformat(), end.isoformat())
            )
            income = (await cursor.fetchone())["total"]
            
            # Expense  
            cursor = await db.execute(
                "SELECT COALESCE(SUM(amount), 0) as total FROM transactions "
                "WHERE user_id = ? AND type = 'chi' AND is_deleted = 0 "
                "AND transaction_date BETWEEN ? AND ?",
                (user_id, start.isoformat(), end.isoformat())
            )
            expense = (await cursor.fetchone())["total"]
            
            # By category
            cursor = await db.execute(
                "SELECT category, type, SUM(amount) as total, COUNT(*) as count "
                "FROM transactions WHERE user_id = ? AND is_deleted = 0 "
                "AND transaction_date BETWEEN ? AND ? "
                "GROUP BY category, type ORDER BY total DESC",
                (user_id, start.isoformat(), end.isoformat())
            )
            by_category = [dict(row) for row in await cursor.fetchall()]
            
            # Transactions
            cursor = await db.execute(
                "SELECT * FROM transactions WHERE user_id = ? AND is_deleted = 0 "
                "AND transaction_date BETWEEN ? AND ? "
                "ORDER BY transaction_date DESC, created_at DESC",
                (user_id, start.isoformat(), end.isoformat())
            )
            txs = [Transaction.from_row(dict(row)) for row in await cursor.fetchall()]
        
        return Report(
            start_date=start,
            end_date=end,
            total_income=income,
            total_expense=expense,
            balance=income - expense,
            by_category=by_category,
            transactions=txs
        )
    
    async def get_daily_report(self, user_id: int, d: Optional[date] = None) -> Report:
        d = d or date.today()
        return await self.get_report(user_id, d, d)
    
    async def get_weekly_report(self, user_id: int) -> Report:
        today = date.today()
        start = today - timedelta(days=today.weekday())
        return await self.get_report(user_id, start, today)
    
    async def get_monthly_report(self, user_id: int) -> Report:
        today = date.today()
        return await self.get_report(user_id, today.replace(day=1), today)
    
    async def get_history(self, user_id: int, limit: int = 10) -> List[Transaction]:
        return await self.find(user_id, limit=limit)
    
    # ==================== Utils ====================
    
    async def clear_all(self, user_id: int) -> int:
        """Soft delete all transactions."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE transactions SET is_deleted = 1, updated_at = ? "
                "WHERE user_id = ? AND is_deleted = 0",
                (datetime.now().isoformat(), user_id)
            )
            await db.commit()
            return cursor.rowcount
    
    async def export_csv(self, user_id: int) -> str:
        """Export to CSV string."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT transaction_date, type, category, amount, note "
                "FROM transactions WHERE user_id = ? AND is_deleted = 0 "
                "ORDER BY transaction_date DESC",
                (user_id,)
            )
            rows = await cursor.fetchall()
        
        lines = ["Ngày,Loại,Danh mục,Số tiền,Ghi chú"]
        for r in rows:
            lines.append(f"{r['transaction_date']},{r['type']},{r['category']},"
                        f"{r['amount']},{r['note'] or ''}")
        return "\n".join(lines)
    
    async def get_stats(self, user_id: int) -> dict:
        """Get DB stats."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM transactions WHERE user_id = ? AND is_deleted = 0",
                (user_id,)
            )
            count = (await cursor.fetchone())[0]
        
        size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        return {"count": count, "size_bytes": size}


# Singleton instance
db = DatabaseService()
