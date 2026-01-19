"""
Telegram message handlers.
"""

import logging
from datetime import date, timedelta

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from aiogram.enums import ParseMode

from ..services import db, ai
from ..models import AIAction, Transaction
from ..constants import ActionType, TransactionType, CATEGORY_ICONS

logger = logging.getLogger(__name__)
router = Router()


def fmt(amount: float) -> str:
    """Format currency."""
    return f"{amount:,.0f}".replace(",", ".")


def fmt_tx(tx: Transaction) -> str:
    """Format transaction."""
    icon = CATEGORY_ICONS.get(tx.category, "❓")
    sign = "🔴" if tx.type == TransactionType.EXPENSE else "🟢"
    date_str = tx.transaction_date.strftime("%d/%m")
    return f"{sign} #{tx.id} | {date_str} | {icon} {tx.category}: {fmt(tx.amount)}đ\n   └ {tx.note or '-'}"


# ==================== Commands ====================

@router.message(Command("start", "help"))
async def cmd_help(message: Message):
    """Help command."""
    await message.answer(
        "🤖 **FinGPT - Trợ lý tài chính**\n\n"
        "**Ghi:** `ăn phở 50k` · `cafe 35 nghìn`\n"
        "**Sửa:** `à nhầm, 30k thôi`\n"
        "**Xem:** `hôm nay chi bao nhiêu` · `tuần này`\n"
        "**Lịch sử:** `xem 10 giao dịch gần nhất`\n"
        "**Xóa:** `xóa cái vừa rồi`\n"
        "**Dashboard:** `/dashboard` - xem biểu đồ đẹp\n\n"
        "📸 Gửi ảnh bill để nhận dạng!",
        parse_mode=ParseMode.MARKDOWN
    )


@router.message(Command("dashboard"))
async def cmd_dashboard(message: Message):
    """Generate dashboard link."""
    from ..web.routes import generate_token
    from ..config import config
    
    user_id = message.from_user.id
    token = generate_token(user_id)
    
    # URL for local/deployed env
    base_url = "http://localhost:5000" if config.DEBUG else "https://YOUR_REPL_URL.repl.co"
    # For now assume localhost until deployed
    url = f"http://localhost:5000/dashboard?user_id={user_id}&token={token}"
    
    await message.answer(
        f"📊 **Dashboard của bạn:**\n\n"
        f"👉 [Nhấn vào để mở]({url})\n\n"
        f"🔑 Token: `{token}`\n"
        f"⚠️ _Link này là bí mật, đừng chia sẻ!_",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )


# ==================== Photo Handler ====================

@router.message(F.photo)
async def handle_photo(message: Message, bot: Bot):
    """Handle photo messages."""
    user_id = message.from_user.id
    
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    image_data = file_bytes.read() if hasattr(file_bytes, 'read') else file_bytes
    
    msg = await message.answer("🔍 Đang đọc bill...")
    
    action = await ai.parse_image(image_data)
    
    if not action.amount or action.amount <= 0:
        await msg.edit_text("❌ Không đọc được. Thử ghi thủ công.")
        return
    
    tx_id = await db.insert(
        user_id=user_id,
        amount=action.amount,
        category=action.category or "Khác",
        note=action.note or "Từ bill",
        tx_type=action.tx_type or TransactionType.EXPENSE
    )
    
    emoji = "💸" if action.tx_type == TransactionType.EXPENSE else "💰"
    await msg.edit_text(
        f"{emoji} **Đã ghi từ bill!**\n"
        f"📂 {action.category or 'Khác'} | 💵 {fmt(action.amount)}đ\n"
        f"✅ #{tx_id}",
        parse_mode=ParseMode.MARKDOWN
    )


# ==================== Text Handler ====================

@router.message(F.text)
async def handle_text(message: Message):
    """Handle text messages."""
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Get context
    last_tx = await db.get_last(user_id)
    context = {"last_tx": last_tx} if last_tx else None
    
    # Parse with error handling
    try:
        action = await ai.parse(text, context)
        logger.info(f"User {user_id}: {action.action.value}")
    except Exception as e:
        logger.error(f"AI parse error: {e}")
        await message.answer(
            "🤔 Không hiểu tin nhắn. Thử:\n"
            "• `ăn phở 50k` - ghi chi tiêu\n"
            "• `hôm nay chi bao nhiêu` - xem báo cáo\n"
            "• `/help` - xem hướng dẫn",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Execute
    handlers = {
        ActionType.INSERT: _handle_insert,
        ActionType.UPDATE: _handle_update,
        ActionType.DELETE: _handle_delete,
        ActionType.UNDO: _handle_delete,
        ActionType.QUERY: _handle_query,
        ActionType.REPORT: _handle_report,
        ActionType.EXPORT: _handle_export,
        ActionType.CLEAR: _handle_clear,
        ActionType.HELP: lambda m, u, a, l: cmd_help(m),
    }
    
    handler = handlers.get(action.action)
    if handler:
        await handler(message, user_id, action, last_tx)
    else:
        await message.answer(
            action.message or "🤔 Không hiểu. Thử: `ăn phở 50k` hoặc `/help`",
            parse_mode=ParseMode.MARKDOWN
        )


async def _handle_insert(message: Message, user_id: int, action: AIAction, _):
    """Handle insert."""
    if not action.amount or action.amount <= 0:
        await message.answer("🤔 Không hiểu số tiền. Thử: `ăn phở 50k`", parse_mode=ParseMode.MARKDOWN)
        return
    
    # Use full message text as note for easy reference
    full_note = message.text.strip()
    
    # Calculate target date from date_offset
    tx_date = action.target_date or date.today()
    
    tx_id = await db.insert(
        user_id=user_id,
        amount=action.amount,
        category=action.category or "Khác",
        note=full_note,  # Full message as note
        tx_type=action.tx_type or TransactionType.EXPENSE,
        tx_date=tx_date
    )
    
    emoji = "💸" if action.tx_type == TransactionType.EXPENSE else "💰"
    icon = CATEGORY_ICONS.get(action.category or "Khác", "❓")
    
    # Format date info
    date_info = ""
    if action.date_offset > 0 or action.time_of_day:
        date_str = tx_date.strftime("%d/%m")
        time_str = f" {action.time_of_day}" if action.time_of_day else ""
        date_info = f"📅 {date_str}{time_str}\n"
    
    await message.answer(
        f"{emoji} **Đã ghi!**\n"
        f"{date_info}"
        f"{icon} {action.category or 'Khác'} | 💵 {fmt(action.amount)}đ\n"
        f"✅ #{tx_id}",
        parse_mode=ParseMode.MARKDOWN
    )


async def _handle_update(message: Message, user_id: int, action: AIAction, last_tx):
    """Handle update."""
    tx_id = action.transaction_id
    
    if not tx_id and action.keyword:
        txs = await db.find(user_id, keyword=action.keyword, limit=1)
        if txs:
            tx_id = txs[0].id
    
    if not tx_id and last_tx:
        tx_id = last_tx.id
    
    if not tx_id:
        await message.answer("❌ Không tìm thấy giao dịch để sửa.")
        return
    
    success = await db.update(tx_id, user_id, action.amount, action.category, action.note)
    
    if success:
        await message.answer(f"✅ Đã sửa #{tx_id}")
    else:
        await message.answer("❌ Không thể sửa.")


async def _handle_delete(message: Message, user_id: int, action: AIAction, last_tx):
    """Handle delete."""
    tx_id = action.transaction_id or (last_tx.id if last_tx else None)
    
    if not tx_id:
        await message.answer("❌ Không tìm thấy giao dịch để xóa.")
        return
    
    success = await db.delete(tx_id, user_id)
    await message.answer(f"🗑️ Đã xóa #{tx_id}" if success else "❌ Không thể xóa.")


async def _handle_query(message: Message, user_id: int, action: AIAction, _):
    """Handle query."""
    txs = await db.get_history(user_id, limit=min(action.limit, 50))
    
    if not txs:
        await message.answer("📋 Chưa có giao dịch.")
        return
    
    lines = [f"📋 **{len(txs)} giao dịch gần nhất:**\n"]
    lines.extend(fmt_tx(tx) for tx in txs)
    await message.answer("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def _handle_report(message: Message, user_id: int, action: AIAction, _):
    """Handle report."""
    from ..constants import ReportType
    
    if action.report_type == ReportType.WEEK:
        report = await db.get_weekly_report(user_id)
        title = "tuần này"
    elif action.report_type == ReportType.MONTH:
        report = await db.get_monthly_report(user_id)
        title = "tháng này"
    else:
        report = await db.get_daily_report(user_id)
        title = "hôm nay"
    
    if not report.transactions:
        await message.answer(f"📊 Chưa có giao dịch {title}.")
        return
    
    cat_lines = []
    for cat in report.by_category[:5]:
        icon = CATEGORY_ICONS.get(cat["category"], "❓")
        sign = "🔴" if cat["type"] == "chi" else "🟢"
        cat_lines.append(f"{sign} {icon} {cat['category']}: {fmt(cat['total'])}đ")
    
    await message.answer(
        f"📊 **Báo cáo {title}**\n\n"
        f"🟢 Thu: **{fmt(report.total_income)}đ**\n"
        f"🔴 Chi: **{fmt(report.total_expense)}đ**\n"
        f"💰 Còn: **{fmt(report.balance)}đ**\n\n"
        f"📂 **Theo danh mục:**\n" + "\n".join(cat_lines),
        parse_mode=ParseMode.MARKDOWN
    )


async def _handle_export(message: Message, user_id: int, action: AIAction, _):
    """Handle export."""
    csv = await db.export_csv(user_id)
    
    if csv.count("\n") <= 1:
        await message.answer("📋 Chưa có dữ liệu.")
        return
    
    file = BufferedInputFile(
        csv.encode("utf-8-sig"),
        filename=f"fingpt_{date.today()}.csv"
    )
    await message.answer_document(file, caption="📁 File CSV!")


async def _handle_clear(message: Message, user_id: int, action: AIAction, _):
    """Handle clear all."""
    text = message.text.lower()
    if "confirm" not in text and "xác nhận" not in text:
        await message.answer("⚠️ Nói: `xóa hết xác nhận`", parse_mode=ParseMode.MARKDOWN)
        return
    
    count = await db.clear_all(user_id)
    await message.answer(f"🗑️ Đã xóa {count} giao dịch.")
