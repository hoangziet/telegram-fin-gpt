"""
AI Service for FinGPT.
"""

from google import genai
from google.genai import types
import json
import re
import os
import logging
from datetime import datetime, date, timedelta
from typing import Optional

from ..config import config
from ..models import AIAction
from ..constants import (
    ActionType, TransactionType, ReportType,
    EXPENSE_CATEGORIES, INCOME_CATEGORIES
)

logger = logging.getLogger(__name__)


class AIService:
    """AI service for parsing natural language."""
    
    SYSTEM_INSTRUCTION = f"""
Bạn là trợ lý tài chính. Phân tích tin nhắn và trả về JSON action.

**ACTIONS:**
- "insert": Thêm giao dịch mới
- "update": Sửa giao dịch đã có  
- "delete": Xóa giao dịch
- "query": Xem/tìm kiếm giao dịch
- "report": Xem báo cáo
- "export": Xuất dữ liệu
- "clear": Xóa tất cả
- "undo": Hoàn tác
- "help": Xem hướng dẫn
- "unknown": Không hiểu

**JSON FORMAT:**
{{
    "action": "<action>",
    "amount": <số tiền đã convert | null>,
    "category": "<danh mục>",
    "type": "thu" | "chi",
    "date_offset": <số ngày lùi lại từ hôm nay, 0=hôm nay, 1=hôm qua, 2=hôm kia>,
    "time_of_day": "sáng" | "trưa" | "chiều" | "tối" | null,
    "transaction_id": <id | null>,
    "keyword": "<từ khóa tìm | null>",
    "report_type": "day" | "week" | "month" | null,
    "limit": <số lượng | 10>,
    "message": "<phản hồi cho user | null>"
}}

**LƯU Ý QUAN TRỌNG:**
- KHÔNG cần trả về "note" - hệ thống sẽ tự lưu toàn bộ tin nhắn gốc làm note

**PHÂN TÍCH THỜI GIAN:**
Ví dụ cách parse:
- "ăn phở 50k" → date_offset: 0, time_of_day: null
- "sáng nay cafe 35k" → date_offset: 0, time_of_day: "sáng"
- "trưa qua cơm 55k" → date_offset: 1, time_of_day: "trưa"
- "tối hôm kia nhậu 200k" → date_offset: 2, time_of_day: "tối"
- "hôm qua mua đồ 100k" → date_offset: 1, time_of_day: null
- "chiều nay grab 45k" → date_offset: 0, time_of_day: "chiều"

**CATEGORIES:**
Chi: {EXPENSE_CATEGORIES}
Thu: {INCOME_CATEGORIES}

**TIỀN VN:** k=x1000, tr/triệu=x1000000, củ/lúa=x1000000

**THU/CHI:** Mặc định "chi". "thu" khi có: lương, thưởng, được cho, nhận được, hoàn tiền, bán được

**CHỈ TRẢ VỀ JSON.**
"""
    
    def __init__(self):
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.model = config.GEMINI_MODEL
        
        # Debug
        if config.DEBUG:
            os.makedirs(config.DEBUG_DIR, exist_ok=True)
        
        logger.info(f"AIService initialized: {self.model}")
    
    def _debug_log(self, name: str, content: str) -> None:
        if not config.DEBUG:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(config.DEBUG_DIR, f"{ts}_{name}")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    
    def _extract_json(self, text: str) -> dict:
        """Extract JSON from response."""
        self._debug_log("response.txt", text)
        
        for attempt in [
            lambda: json.loads(text.strip()),
            lambda: json.loads(re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text).group(1)),
            lambda: json.loads(re.search(r'\{[\s\S]*\}', text).group(0))
        ]:
            try:
                return attempt()
            except:
                continue
        
        return {"action": "unknown"}
    
    def _resolve_date(self, hint: Optional[str]) -> date:
        """Resolve time hint to date."""
        if not hint:
            return date.today()
        
        h = hint.lower()
        if "hôm qua" in h or "hqua" in h:
            return date.today() - timedelta(days=1)
        if "hôm kia" in h:
            return date.today() - timedelta(days=2)
        
        return date.today()
    
    def _parse_action(self, data: dict) -> AIAction:
        """Convert dict to AIAction."""
        action_str = data.get("action", "unknown")
        try:
            action = ActionType(action_str)
        except ValueError:
            action = ActionType.UNKNOWN
        
        tx_type = None
        if data.get("type"):
            try:
                tx_type = TransactionType(data["type"])
            except ValueError:
                tx_type = TransactionType.EXPENSE
        
        report_type = None
        if data.get("report_type"):
            try:
                report_type = ReportType(data["report_type"])
            except ValueError:
                report_type = ReportType.DAY
        
        # Resolve date from date_offset
        date_offset = data.get("date_offset", 0)
        target_date = date.today() - timedelta(days=date_offset)
        
        return AIAction(
            action=action,
            amount=data.get("amount"),
            category=data.get("category"),
            tx_type=tx_type,
            date_offset=date_offset,
            time_of_day=data.get("time_of_day"),
            target_date=target_date,
            transaction_id=data.get("transaction_id"),
            keyword=data.get("keyword"),
            report_type=report_type,
            limit=data.get("limit", 10),
            message=data.get("message")
        )
    
    async def parse(self, message: str, context: Optional[dict] = None) -> AIAction:
        """Parse message to action."""
        self._debug_log("input.txt", message)
        
        # Build context
        ctx = ""
        if context and context.get("last_tx"):
            tx = context["last_tx"]
            ctx = f"[Last TX: #{tx.id} {tx.amount} {tx.note}]\n"
        
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=f"{ctx}{message}",
            config=types.GenerateContentConfig(
                system_instruction=self.SYSTEM_INSTRUCTION,
                temperature=0.1,
            )
        )
        
        data = self._extract_json(response.text)
        return self._parse_action(data)
    
    async def parse_image(self, image_bytes: bytes) -> AIAction:
        """Parse bank bill image."""
        if config.DEBUG:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            with open(os.path.join(config.DEBUG_DIR, f"{ts}_image.jpg"), "wb") as f:
                f.write(image_bytes)
        
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=[
                "Đây là bill ngân hàng. Trích xuất thông tin giao dịch:",
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
            ],
            config=types.GenerateContentConfig(
                system_instruction=self.SYSTEM_INSTRUCTION,
                temperature=0.1,
            )
        )
        
        data = self._extract_json(response.text)
        action = self._parse_action(data)
        action.action = ActionType.INSERT
        return action


# Singleton
ai = AIService()
