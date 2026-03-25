"""
Web dashboard routes with Multi-tenant Authentication.
"""
import asyncio
from datetime import date, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from ..services.database import db

dashboard = Blueprint("dashboard", __name__, template_folder="templates", static_folder="static")

login_manager = LoginManager()
login_manager.login_view = "dashboard.login_page"

def init_web(app):
    login_manager.init_app(app)
    app.register_blueprint(dashboard)

class AuthedUser(UserMixin):
    def __init__(self, user):
        self.id = str(user.id)
        self.username = user.username
        self.bot_token = user.bot_token
        self.telegram_user_id = user.telegram_user_id

@login_manager.user_loader
def load_user(user_id):
    try:
        user = asyncio.run(db.get_user_by_id(int(user_id)))
        if user:
            return AuthedUser(user)
    except:
        pass
    return None

# ================= AUTHENTICATION ================= #
@dashboard.route("/login", methods=["GET", "POST"])
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard_page"))
        
    error = None
    if request.method == "POST":
        action = request.form.get("action")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        if not username or not password:
            error = "Vui lòng nhập đầy đủ thông tin!"
        else:
            if action == "register":
                # Check exist
                existing = asyncio.run(db.get_user_by_username(username))
                if existing:
                    error = "Tên đăng nhập đã tồn tại!"
                else:
                    h = generate_password_hash(password)
                    uid = asyncio.run(db.create_user(username, h))
                    user = asyncio.run(db.get_user_by_id(uid))
                    login_user(AuthedUser(user))
                    return redirect(url_for("dashboard.settings_page"))
                    
            elif action == "login":
                user = asyncio.run(db.get_user_by_username(username))
                if user and check_password_hash(user.password_hash, password):
                    login_user(AuthedUser(user), remember=True)
                    return redirect(url_for("dashboard.dashboard_page"))
                else:
                    error = "Sai tên đăng nhập hoặc mật khẩu!"
                    
    return render_template("login.html", error=error)

@dashboard.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("dashboard.login_page"))

# ================= PAGES ================= #
@dashboard.route("/dashboard")
@login_required
def dashboard_page():
    if not current_user.bot_token:
        return redirect(url_for('dashboard.settings_page'))
    return render_template("dashboard.html", user_id=current_user.id)

@dashboard.route("/transactions")
@login_required
def transactions_page():
    return render_template("transactions.html", user_id=current_user.id)

@dashboard.route("/about")
@login_required
def about_page():
    return render_template("about.html", user_id=current_user.id)

@dashboard.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    msg = None
    error = None
    if request.method == "POST":
        bot_token = request.form.get("bot_token", "").strip()
        telegram_user_id_str = request.form.get("telegram_user_id", "").strip()
        telegram_user_id = int(telegram_user_id_str) if telegram_user_id_str.isdigit() else 0
        
        if bot_token and telegram_user_id:
            from aiogram import Bot
            from aiogram.exceptions import TelegramUnauthorizedError, TelegramForbiddenError, TelegramBadRequest
            
            async def test_conn():
                bot = Bot(token=bot_token)
                try:
                    me = await bot.get_me()
                    await bot.send_message(
                        chat_id=telegram_user_id, 
                        text=f"✅ Kết nối thành công! Web Dashboard của bạn đã liên kết với Bot @{me.username}."
                    )
                    return True, None
                except TelegramUnauthorizedError:
                    return False, "Bot Token không hợp lệ. Vui lòng kiểm tra lại cấu hình."
                except TelegramForbiddenError:
                    return False, "Bot không thể gửi tin nhắn. Hãy chắc chắn bạn đã vào Telegram và gửi /start cho Bot trước."
                except TelegramBadRequest as e:
                    if "chat not found" in str(e).lower():
                        return False, "Không tìm thấy đoạn chat (Telegram ID sai hoặc bạn chưa /start bot)."
                    return False, f"Lỗi yêu cầu: {str(e)}"
                except Exception as e:
                    return False, f"Lỗi không xác định: {str(e)}"
                finally:
                    await bot.session.close()
                    
            success, err_msg = asyncio.run(test_conn())
            if not success:
                error = err_msg
            else:
                asyncio.run(db.update_user_config(int(current_user.id), bot_token, telegram_user_id))
                msg = "Đã lưu cấu hình và test gửi tin nhắn thành công! Hệ thống sẽ tự động cập nhật bot trong vòng 15 giây."
        else:
            asyncio.run(db.update_user_config(int(current_user.id), bot_token, telegram_user_id))
            msg = "Đã lưu cấu hình! (Không test được do chưa đủ Token hoặc ID)."
        
    user = asyncio.run(db.get_user_by_id(int(current_user.id)))
    return render_template("settings.html", user=user, msg=msg, error=error)

# ================= API ENDPOINTS ================= #
@dashboard.route("/api/summary")
@login_required
def api_summary():
    days = request.args.get("days", 30, type=int)
    async def get_data():
        end = date.today()
        start = end - timedelta(days=days)
        report = await db.get_report(int(current_user.id), start, end)
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
@login_required
def api_transactions():
    limit = request.args.get("limit", 50, type=int)
    async def get_data():
        txs = await db.get_history(int(current_user.id), limit=min(limit, 100))
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
@login_required
def api_by_category():
    days = request.args.get("days", 30, type=int)
    async def get_data():
        end = date.today()
        start = end - timedelta(days=days)
        report = await db.get_report(int(current_user.id), start, end)
        return report.by_category
    return jsonify(asyncio.run(get_data()))

@dashboard.route("/api/trend")
@login_required
def api_trend():
    days = request.args.get("days", 7, type=int)
    async def get_data():
        return await db.get_trend(int(current_user.id), days)
    return jsonify(asyncio.run(get_data()))

@dashboard.route("/api/transactions/<int:tx_id>", methods=["DELETE"])
@login_required
def api_delete_tx(tx_id):
    success = asyncio.run(db.delete(tx_id, int(current_user.id)))
    return jsonify({"success": success})

@dashboard.route("/api/transactions/<int:tx_id>", methods=["PUT"])
@login_required
def api_update_tx(tx_id):
    data = request.json
    amt = float(data.get("amount")) if data.get("amount") else None
    success = asyncio.run(db.update(tx_id, int(current_user.id), amount=amt, category=data.get("category"), note=data.get("note")))
    return jsonify({"success": success})
