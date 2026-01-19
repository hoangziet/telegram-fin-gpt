"""
Web dashboard routes.
"""

import hashlib
import os
from datetime import date, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect, url_for

from ..services.database import db
from ..config import config

dashboard = Blueprint("dashboard", __name__, 
                      template_folder="templates",
                      static_folder="static")

# Secret for token generation
SECRET = os.getenv("DASHBOARD_SECRET", "fingpt-secret-2026")


def generate_token(user_id: int) -> str:
    """Generate auth token for user."""
    data = f"{user_id}:{SECRET}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def verify_token(user_id: int, token: str) -> bool:
    """Verify auth token."""
    return token == generate_token(user_id)


@dashboard.route("/login")
def login_page():
    """Login page."""
    return render_template("login.html")


@dashboard.route("/dashboard")
def dashboard_page():
    """Dashboard page."""
    user_id = request.args.get("user_id", type=int)
    token = request.args.get("token", "")
    
    if not user_id or not verify_token(user_id, token):
        return redirect(url_for("dashboard.login_page", error="Token không hợp lệ"))
    
    return render_template("dashboard.html", user_id=user_id, token=token)


@dashboard.route("/api/summary")
def api_summary():
    """Get summary stats."""
    user_id = request.args.get("user_id", type=int)
    token = request.args.get("token", "")
    days = request.args.get("days", 30, type=int)
    
    if not user_id or not verify_token(user_id, token):
        return jsonify({"error": "Unauthorized"}), 401
    
    import asyncio
    
    async def get_data():
        end = date.today()
        start = end - timedelta(days=days)
        report = await db.get_report(user_id, start, end)
        return {
            "total_income": report.total_income,
            "total_expense": report.total_expense,
            "balance": report.balance,
            "transaction_count": len(report.transactions),
            "start_date": start.isoformat(),
            "end_date": end.isoformat()
        }
    
    return jsonify(asyncio.run(get_data()))


@dashboard.route("/api/transactions")
def api_transactions():
    """Get transactions."""
    user_id = request.args.get("user_id", type=int)
    token = request.args.get("token", "")
    limit = request.args.get("limit", 50, type=int)
    
    if not user_id or not verify_token(user_id, token):
        return jsonify({"error": "Unauthorized"}), 401
    
    import asyncio
    
    async def get_data():
        txs = await db.get_history(user_id, limit=min(limit, 100))
        return [{
            "id": tx.id,
            "amount": tx.amount,
            "category": tx.category,
            "note": tx.note,
            "type": tx.type.value,
            "date": tx.transaction_date.isoformat(),
            "created_at": tx.created_at.isoformat()
        } for tx in txs]
    
    return jsonify(asyncio.run(get_data()))


@dashboard.route("/api/by-category")
def api_by_category():
    """Get spending by category."""
    user_id = request.args.get("user_id", type=int)
    token = request.args.get("token", "")
    days = request.args.get("days", 30, type=int)
    
    if not user_id or not verify_token(user_id, token):
        return jsonify({"error": "Unauthorized"}), 401
    
    import asyncio
    
    async def get_data():
        end = date.today()
        start = end - timedelta(days=days)
        report = await db.get_report(user_id, start, end)
        return report.by_category
    
    return jsonify(asyncio.run(get_data()))


@dashboard.route("/api/trend")
def api_trend():
    """Get daily trend."""
    user_id = request.args.get("user_id", type=int)
    token = request.args.get("token", "")
    days = request.args.get("days", 7, type=int)
    
    if not user_id or not verify_token(user_id, token):
        return jsonify({"error": "Unauthorized"}), 401
    
    import asyncio
    
    async def get_data():
        return await db.get_trend(user_id, days)
    
    return jsonify(asyncio.run(get_data()))


@dashboard.route("/transactions")
def transactions_page():
    """Transactions page."""
    user_id = request.args.get("user_id", type=int)
    token = request.args.get("token", "")
    
    if not user_id or not verify_token(user_id, token):
        return redirect(url_for("dashboard.login_page", error="Token không hợp lệ"))
    
    return render_template("transactions.html", user_id=user_id, token=token)


@dashboard.route("/about")
def about_page():
    """About page."""
    user_id = request.args.get("user_id", type=int)
    token = request.args.get("token", "")
    
    if not user_id or not verify_token(user_id, token):
        return redirect(url_for("dashboard.login_page", error="Token không hợp lệ"))
    
    return render_template("about.html", user_id=user_id, token=token)
