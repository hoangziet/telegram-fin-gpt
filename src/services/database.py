"""
Database service for FinGPT.
SQLite implementation for Local deployment and Multi-tenant support.
"""

import aiosqlite
import os
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List, Any, Dict

from ..config import config
from ..models import Transaction, Report, User
from ..constants import TransactionType

logger = logging.getLogger(__name__)

class DatabaseService:
    """Database operations via SQLite."""
    
    def __init__(self):
        self.db_path = config.DB_PATH
        # Ensure data directory exists for SQLite
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
    async def init(self) -> None:
        """Init SQLite schema."""
        async with aiosqlite.connect(self.db_path) as db:
            # Table for Transactions
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
            
            # Table for Web Users (Multi-tenant)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    bot_token TEXT,
                    telegram_user_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()
            logger.info("✅ Connected to SQLite & Initialized Schema")

    async def _execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute query modifying data."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(sql, params)
            await db.commit()
            if sql.strip().upper().startswith("INSERT"):
                return cursor.lastrowid
            return cursor.rowcount

    async def _fetch(self, sql: str, params: tuple = (), one: bool = False) -> Any:
        """Fetch data."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            if one:
                row = await cursor.fetchone()
                return dict(row) if row else None
            else:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # ==================== User Auth & Multi-tenant ====================
    async def create_user(self, username: str, password_hash: str) -> int:
        return await self._execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash)
        )
        
    async def get_user_by_username(self, username: str) -> Optional[User]:
        row = await self._fetch("SELECT * FROM users WHERE username = ?", (username,), one=True)
        if not row:
            return None
        return User(
            id=row["id"], username=row["username"], password_hash=row["password_hash"],
            bot_token=row["bot_token"], telegram_user_id=row["telegram_user_id"],
            created_at=row["created_at"]
        )

    async def get_user_by_bot_token(self, bot_token: str) -> Optional[User]:
        row = await self._fetch("SELECT * FROM users WHERE bot_token = ?", (bot_token,), one=True)
        if not row:
            return None
        return User(
            id=row["id"], username=row["username"], password_hash=row["password_hash"],
            bot_token=row["bot_token"], telegram_user_id=row["telegram_user_id"],
            created_at=row["created_at"]
        )
        
    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        row = await self._fetch("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
        if not row:
            return None
        return User(
            id=row["id"], username=row["username"], password_hash=row["password_hash"],
            bot_token=row["bot_token"], telegram_user_id=row["telegram_user_id"],
            created_at=row["created_at"]
        )
        
    async def update_user_config(self, user_id: int, bot_token: str, telegram_user_id: int) -> bool:
        res = await self._execute(
            "UPDATE users SET bot_token = ?, telegram_user_id = ? WHERE id = ?",
            (bot_token, telegram_user_id, user_id)
        )
        return res > 0
        
    async def get_all_bots(self) -> List[dict]:
        """Lấy danh sách tất cả Bot Token đã được cấu hình để chạy."""
        return await self._fetch("SELECT bot_token, telegram_user_id FROM users WHERE bot_token IS NOT NULL AND bot_token != ''")

    # ==================== CRUD Transactions ====================
    async def insert(self, user_id: int, amount: float, category: str, note: Optional[str], tx_type: TransactionType, tx_date: Optional[date] = None) -> int:
        tx_date = tx_date or date.today()
        return await self._execute(
            "INSERT INTO transactions (user_id, amount, category, note, type, transaction_date) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, abs(amount), category, note, tx_type.value, tx_date)
        )
    
    async def update(self, tx_id: int, user_id: int, amount: Optional[float] = None, category: Optional[str] = None, note: Optional[str] = None) -> bool:
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
        params.append(datetime.now())
        params.extend([tx_id, user_id])
        res = await self._execute(
            f"UPDATE transactions SET {', '.join(updates)} WHERE id = ? AND user_id = ? AND is_deleted = 0",
            tuple(params)
        )
        return res > 0
    
    async def delete(self, tx_id: int, user_id: int) -> bool:
        res = await self._execute(
            "UPDATE transactions SET is_deleted = 1, updated_at = ? WHERE id = ? AND user_id = ? AND is_deleted = 0",
            (datetime.now(), tx_id, user_id)
        )
        return res > 0
    
    async def get_last(self, user_id: int) -> Optional[Transaction]:
        row = await self._fetch(
            "SELECT * FROM transactions WHERE user_id = ? AND is_deleted = 0 ORDER BY created_at DESC LIMIT 1",
            (user_id,), one=True
        )
        return Transaction.from_row(row) if row else None
    
    async def find(self, user_id: int, keyword: Optional[str] = None, category: Optional[str] = None, tx_date: Optional[date] = None, limit: int = 10) -> List[Transaction]:
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
            params.append(tx_date)
        params.append(limit)
        rows = await self._fetch(
            f"SELECT * FROM transactions WHERE {' AND '.join(conditions)} ORDER BY created_at DESC LIMIT ?",
            tuple(params)
        )
        return [Transaction.from_row(row) for row in rows]
    
    # ==================== Reports ====================
    async def get_report(self, user_id: int, start: date, end: date) -> Report:
        row_inc = await self._fetch("SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE user_id = ? AND type = 'thu' AND is_deleted = 0 AND transaction_date BETWEEN ? AND ?", (user_id, start, end), one=True)
        income = row_inc["total"] if row_inc else 0
        row_exp = await self._fetch("SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE user_id = ? AND type = 'chi' AND is_deleted = 0 AND transaction_date BETWEEN ? AND ?", (user_id, start, end), one=True)
        expense = row_exp["total"] if row_exp else 0
        by_category = await self._fetch("SELECT category, type, SUM(amount) as total, COUNT(*) as count FROM transactions WHERE user_id = ? AND is_deleted = 0 AND transaction_date BETWEEN ? AND ? GROUP BY category, type ORDER BY total DESC", (user_id, start, end))
        rows_tx = await self._fetch("SELECT * FROM transactions WHERE user_id = ? AND is_deleted = 0 AND transaction_date BETWEEN ? AND ? ORDER BY transaction_date DESC, created_at DESC", (user_id, start, end))
        txs = [Transaction.from_row(row) for row in rows_tx]
        return Report(start_date=start, end_date=end, total_income=income, total_expense=expense, balance=income - expense, by_category=by_category, transactions=txs)
    
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
    async def get_trend(self, user_id: int, days: int = 7) -> List[Dict]:
        end = date.today()
        start = end - timedelta(days=days-1)
        rows = await self._fetch(
            "SELECT transaction_date, type, SUM(amount) as total FROM transactions WHERE user_id = ? AND is_deleted = 0 AND transaction_date BETWEEN ? AND ? GROUP BY transaction_date, type ORDER BY transaction_date",
            (user_id, start, end)
        )
        result = []
        current = start
        while current <= end:
            day_data = {"date": current.isoformat(), "income": 0, "expense": 0}
            current_iso = current.isoformat()
            for row in rows:
                d = row["transaction_date"]
                row_date = d.isoformat() if hasattr(d, 'isoformat') else d
                if row_date == current_iso:
                    if row["type"] == "thu": day_data["income"] = row["total"]
                    else: day_data["expense"] = row["total"]
            result.append(day_data)
            current += timedelta(days=1)
        return result
    
    async def clear_all(self, user_id: int) -> int:
        res = await self._execute("UPDATE transactions SET is_deleted = 1, updated_at = ? WHERE user_id = ? AND is_deleted = 0", (datetime.now(), user_id))
        return res
    
    async def export_csv(self, user_id: int) -> str:
        rows = await self._fetch("SELECT transaction_date, type, category, amount, note FROM transactions WHERE user_id = ? AND is_deleted = 0 ORDER BY transaction_date DESC", (user_id,))
        lines = ["Ngày,Loại,Danh mục,Số tiền,Ghi chú"]
        for r in rows:
            d = r['transaction_date']
            d_str = d.isoformat() if hasattr(d, 'isoformat') else str(d)
            lines.append(f"{d_str},{r['type']},{r['category']},{r['amount']},{r['note'] or ''}")
        return "\n".join(lines)
    
    async def get_stats(self, user_id: int) -> dict:
        row = await self._fetch("SELECT COUNT(*) as count FROM transactions WHERE user_id = ? AND is_deleted = 0", (user_id,), one=True)
        count = row["count"] if row else 0
        size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        return {"count": count, "size_bytes": size}

# Singleton instance
db = DatabaseService()
