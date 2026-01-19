"""
Database service for FinGPT.
Supports both SQLite and PostgreSQL (Supabase).
"""

import aiosqlite
import asyncpg
import asyncio
import os
import logging
import re
from datetime import datetime, date, timedelta
from typing import Optional, List, Any, Dict

from ..config import config
from ..models import Transaction, Report
from ..constants import TransactionType

logger = logging.getLogger(__name__)

class DatabaseService:
    """Database operations with multi-backend support."""
    
    def __init__(self):
        self.db_path = config.DB_PATH
        self.db_url = config.DATABASE_URL
        self.type = config.DB_TYPE  # 'sqlite' or 'postgres'
        self.pool = None
        
        # Ensure data directory exists for SQLite
        if self.type == "sqlite":
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
    async def init(self) -> None:
        """Initialize database."""
        if self.type == "postgres":
            await self._init_postgres()
        else:
            await self._init_sqlite()
            
    async def _init_postgres(self):
        """Init Postgres connection and schema."""
        if not self.pool:
            try:
                self.pool = await asyncpg.create_pool(
                    self.db_url,
                    min_size=1,
                    max_size=10,
                    # Supabase Transaction Pooler (port 6543) requires this
                    statement_cache_size=0,
                    # SSL is required for Supabase
                    ssl='require'
                )
                logger.info("✅ Connected to Supabase (Postgres)")
            except Exception as e:
                logger.error(f"❌ Failed to connect to Postgres: {e}")
                raise

        async with self.pool.acquire() as conn:
            # Create table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    amount DOUBLE PRECISION NOT NULL,
                    category TEXT NOT NULL,
                    note TEXT,
                    type TEXT NOT NULL CHECK(type IN ('thu', 'chi')),
                    transaction_date DATE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_deleted INTEGER DEFAULT 0
                )
            """)
            # Index
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_date 
                ON transactions(user_id, transaction_date, is_deleted)
            """)
            
    async def _init_sqlite(self):
        """Init SQLite schema."""
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
            logger.info("✅ Connected to SQLite")



    # ==================== CoreExec ====================

    def _convert_sql(self, sql: str) -> str:
        """Convert '?' placeholders to '$n' for Postgres."""
        if self.type != "postgres":
            return sql
            
        # Replace ? with $1, $2, ...
        parts = sql.split('?')
        if len(parts) == 1:
            return sql
            
        new_sql = parts[0]
        for i in range(1, len(parts)):
            new_sql += f"${i}" + parts[i]
        return new_sql
    
    async def _get_conn(self):
        """Get connection context manager.
        Handles both pool (main loop) and transient connection (other loops).
        """
        if self.type == "postgres":
            if not self.pool:
                await self.init()
            
            # Check if current loop matches pool's loop
            try:
                # asyncpg pool is bound to the loop where it was created
                # If we are in a different loop, acquire() will fail or return a future for another loop
                # We can check simple equality if we stored the loop, but asyncpg checks internally.
                # Simplest way: try to use the pool, if it fails with RuntimeError, fall back to ad-hoc.
                # However, acquire() is async.
                
                # Better approach: check loop
                if self.pool._loop != asyncio.get_running_loop():
                    # Transient connection for this request
                    return await asyncpg.connect(self.db_url, ssl='require')
                else:
                    # Use pool
                    return self.pool.acquire()
            except AttributeError:
                # Pool might not expose _loop publically or implementation detail changes
                # Fallback to creating a new connection as safe default if unsure
                return await asyncpg.connect(self.db_url, ssl='require')
        else:
            return aiosqlite.connect(self.db_path)

    async def _execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute query."""
        if self.type == "postgres":
            sql = self._convert_sql(sql)
            
            # Helper to handle connection/pool differences
            conn_ctx = None
            try:
                # Logic to determine if we use pool or new connection
                if self.pool and self.pool._loop == asyncio.get_running_loop():
                   conn_ctx = self.pool.acquire()
                   is_pool = True
                else:
                   conn_ctx = await asyncpg.connect(self.db_url, ssl='require')
                   is_pool = False
            except Exception:
                 conn_ctx = await asyncpg.connect(self.db_url, ssl='require')
                 is_pool = False

            # Use the context
            # If is_pool is True, conn_ctx is an AsyncContextManager -> 'async with conn_ctx'
            # If is_pool is False, conn_ctx is a Connection object. But asyncpg Connection is also a context manager? 
            # No, asyncpg.connect returns a connection context manager.
            # But await asyncpg.connect() returns a Connection object which is NOT a context manager in the same way for 'acquire'.
            # wait, asyncpg.connect() is awaitable returning Connection.
            # Connection object can be used in 'async with' to close it automatically.
            
            # So:
            # if pool: async with pool.acquire() as conn:
            # if manual: conn = await connect(); async with conn: ...
            
            if self.pool and self.pool._loop == asyncio.get_running_loop():
                async with self.pool.acquire() as conn:
                    return await self._run_pg_execute(conn, sql, params)
            else:
                conn = await asyncpg.connect(self.db_url, ssl='require')
                try:
                     return await self._run_pg_execute(conn, sql, params)
                finally:
                    await conn.close()
                    
        else:
            # SQLite
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(sql, params)
                await db.commit()
                if sql.strip().upper().startswith("INSERT"):
                    return cursor.lastrowid
                return cursor.rowcount

    async def _run_pg_execute(self, conn, sql, params):
        if sql.strip().upper().startswith("INSERT"):
            if "RETURNING" not in sql.upper():
                sql += " RETURNING id"
                val = await conn.fetchval(sql, *params)
                return val
            else:
                return await conn.fetchval(sql, *params)
        else:
            return await conn.execute(sql, *params)

    async def _fetch(self, sql: str, params: tuple = (), one: bool = False) -> Any:
        """Fetch data."""
        if self.type == "postgres":
            sql = self._convert_sql(sql)
            
            if self.pool and self.pool._loop == asyncio.get_running_loop():
                async with self.pool.acquire() as conn:
                    return await self._run_pg_fetch(conn, sql, params, one)
            else:
                # Transient
                conn = await asyncpg.connect(self.db_url, ssl='require')
                try:
                    return await self._run_pg_fetch(conn, sql, params, one)
                finally:
                    await conn.close()

        else:
            # SQLite
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                if one:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
                else:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
                    
    async def _run_pg_fetch(self, conn, sql, params, one):
        if one:
            row = await conn.fetchrow(sql, *params)
            return dict(row) if row else None
        else:
            rows = await conn.fetch(sql, *params)
            return [dict(row) for row in rows]

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
        
        # Postgres requires explicit date type, SQLite takes string or date (adapter handles it)
        # asyncpg handles date objects natively.
        
        return await self._execute(
            """
            INSERT INTO transactions 
            (user_id, amount, category, note, type, transaction_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, abs(amount), category, note, tx_type.value, tx_date)
        )
    
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
        params.append(datetime.now())
        
        params.extend([tx_id, user_id])
        
        # Postgres execute returns 'UPDATE 1', SQLite returns int rowcount
        res = await self._execute(
            f"UPDATE transactions SET {', '.join(updates)} "
            f"WHERE id = ? AND user_id = ? AND is_deleted = 0",
            tuple(params)
        )
        
        if self.type == "postgres":
            # res is string like "UPDATE 1"
            return "UPDATE 0" not in res
        return res > 0
    
    async def delete(self, tx_id: int, user_id: int) -> bool:
        """Soft delete transaction."""
        res = await self._execute(
            """
            UPDATE transactions SET is_deleted = 1, updated_at = ?
            WHERE id = ? AND user_id = ? AND is_deleted = 0
            """,
            (datetime.now(), tx_id, user_id)
        )
        if self.type == "postgres":
            return "UPDATE 0" not in res
        return res > 0
    
    async def get_last(self, user_id: int) -> Optional[Transaction]:
        """Get last transaction."""
        row = await self._fetch(
            "SELECT * FROM transactions WHERE user_id = ? AND is_deleted = 0 "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
            one=True
        )
        return Transaction.from_row(row) if row else None
    
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
            params.append(tx_date)
        
        params.append(limit)
        
        rows = await self._fetch(
            f"SELECT * FROM transactions WHERE {' AND '.join(conditions)} "
            f"ORDER BY created_at DESC LIMIT ?",
            tuple(params)
        )
        return [Transaction.from_row(row) for row in rows]
    
    # ==================== Reports ====================
    
    async def get_report(self, user_id: int, start: date, end: date) -> Report:
        """Get report for date range."""
        # Income
        row_inc = await self._fetch(
            "SELECT COALESCE(SUM(amount), 0) as total FROM transactions "
            "WHERE user_id = ? AND type = 'thu' AND is_deleted = 0 "
            "AND transaction_date BETWEEN ? AND ?",
            (user_id, start, end),
            one=True
        )
        income = row_inc["total"] if row_inc else 0
        
        # Expense  
        row_exp = await self._fetch(
            "SELECT COALESCE(SUM(amount), 0) as total FROM transactions "
            "WHERE user_id = ? AND type = 'chi' AND is_deleted = 0 "
            "AND transaction_date BETWEEN ? AND ?",
            (user_id, start, end),
            one=True
        )
        expense = row_exp["total"] if row_exp else 0
        
        # By category
        by_category = await self._fetch(
            "SELECT category, type, SUM(amount) as total, COUNT(*) as count "
            "FROM transactions WHERE user_id = ? AND is_deleted = 0 "
            "AND transaction_date BETWEEN ? AND ? "
            "GROUP BY category, type ORDER BY total DESC",
            (user_id, start, end)
        )
        
        # Transactions
        rows_tx = await self._fetch(
            "SELECT * FROM transactions WHERE user_id = ? AND is_deleted = 0 "
            "AND transaction_date BETWEEN ? AND ? "
            "ORDER BY transaction_date DESC, created_at DESC",
            (user_id, start, end)
        )
        txs = [Transaction.from_row(row) for row in rows_tx]
        
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
    
    async def get_trend(self, user_id: int, days: int = 7) -> List[Dict]:
        """Get daily trend."""
        end = date.today()
        start = end - timedelta(days=days-1)
        
        rows = await self._fetch(
            """
            SELECT transaction_date, type, SUM(amount) as total
            FROM transactions
            WHERE user_id = ? AND is_deleted = 0
            AND transaction_date BETWEEN ? AND ?
            GROUP BY transaction_date, type
            ORDER BY transaction_date
            """,
            (user_id, start, end)
        )
        
        # Build daily data
        result = []
        current = start
        while current <= end:
            day_data = {"date": current.isoformat(), "income": 0, "expense": 0}
            current_iso = current.isoformat()
            
            for row in rows:
                # Handle date type difference (Postgres returns date obj)
                d = row["transaction_date"]
                row_date = d.isoformat() if hasattr(d, 'isoformat') else d
                
                if row_date == current_iso:
                    if row["type"] == "thu":
                        day_data["income"] = row["total"]
                    else:
                        day_data["expense"] = row["total"]
            
            result.append(day_data)
            current += timedelta(days=1)
            
        return result
    
    async def clear_all(self, user_id: int) -> int:
        """Soft delete all transactions."""
        res = await self._execute(
            "UPDATE transactions SET is_deleted = 1, updated_at = ? "
            "WHERE user_id = ? AND is_deleted = 0",
            (datetime.now(), user_id)
        )
        if self.type == "postgres":
             # "UPDATE N" => parse N
             try:
                 return int(res.split(" ")[1])
             except:
                 return 0
        return res
    
    async def export_csv(self, user_id: int) -> str:
        """Export to CSV string."""
        rows = await self._fetch(
            "SELECT transaction_date, type, category, amount, note "
            "FROM transactions WHERE user_id = ? AND is_deleted = 0 "
            "ORDER BY transaction_date DESC",
            (user_id,)
        )
        
        lines = ["Ngày,Loại,Danh mục,Số tiền,Ghi chú"]
        for r in rows:
            # Handle date format difference if any
            d = r['transaction_date']
            # Postgres returns date obj, SQLite returns str (maybe)
            d_str = d.isoformat() if hasattr(d, 'isoformat') else str(d)
            
            lines.append(f"{d_str},{r['type']},{r['category']},"
                        f"{r['amount']},{r['note'] or ''}")
        return "\n".join(lines)
    
    async def get_stats(self, user_id: int) -> dict:
        """Get DB stats."""
        row = await self._fetch(
            "SELECT COUNT(*) as count FROM transactions WHERE user_id = ? AND is_deleted = 0",
            (user_id,),
            one=True
        )
        count = row["count"] if row else 0
        
        # Size only for sqlite
        size = 0
        if self.type == "sqlite" and os.path.exists(self.db_path):
            size = os.path.getsize(self.db_path)
            
        return {"count": count, "size_bytes": size}


# Singleton instance
db = DatabaseService()
