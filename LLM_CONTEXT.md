# FinGPT V2 - HỒ SƠ DỰ ÁN DÀNH CHO LLM (LLM Context File)
*Sử dụng file này làm bối cảnh gốc (system prompt / context) mỗi khi bạn bắt đầu một cuộc hội thoại mới với LLM để yêu cầu viết code, fix bug, hoặc phát triển thêm tính năng cho dự án này.*

---

## 1. TỔNG QUAN YÊU CẦU (SRS)
**FinGPT V2** là hệ thống quản lý tài chính cá nhân toàn diện, người dùng nhập liệu chủ yếu thông qua Telegram Bot (bằng văn bản tự nhiên hoặc ảnh hóa đơn). Dữ liệu sau đó sẽ được hiển thị và thống kê trên Web Dashboard.
- **Tính năng Bot:** NLP để bóc tách thông tin (Thêm/Sửa/Xóa/Truy vấn), OCR đọc hóa đơn chuyển khoản.
- **Tính năng Web:** Xem tổng quan số dư, biểu đồ thu/chi, và danh sách giao dịch.
- **Kiến trúc:** 1 Thread phụ (Flask Server) chạy UI và giữ alive, 1 Asyncio Event Loop (aiogram) chạy Bot Polling.

## 2. PROJECT TECH STACK & DEPENDENCIES
Hệ thống sử dụng các thư viện với phiên bản cốt lõi như sau (trích từ `requirements.txt`):
```text
aiogram>=3.0.0
aiosqlite>=0.19.0
google-genai>=1.0.0
python-dotenv>=1.0.0
flask>=3.0.0
asyncpg>=0.29.0
```

## 3. CẤU TRÚC THƯ MỤC CỦA DỰ ÁN (Project Structure)
```text
telegram-fin-gpt/
├── main.py                 # (Entry Point: Khởi chạy Flask ở thread phụ và chạy bot asyncio)
├── requirements.txt
├── README.md
├── .env.example
├── Dockerfile
└── src/                    # (Thư mục mã nguồn chính)
    ├── __init__.py
    ├── config.py           # (Load biến môi trường .env)
    ├── constants.py        # (Các file hằng số như Danh mục, Type, Actions)
    ├── models.py           # (Định nghĩa Dataclass cho hệ thống)
    ├── handlers/
    │   └── messages.py     # (Router xử lý tin nhắn của aiogram)
    ├── services/
    │   ├── ai.py           # (Gọi Google Gemini Pro AI thực hiện phân tách NLP/OCR)
    │   └── database.py     # (Thao tác với PostgreSQL DB thông qua asyncpg)
    └── web/
        ├── routes.py       # (Flask Blueprints cho giao diện Dashboard)
        └── templates/      # (File HTML/Jinja2 cho UI)
```

## 4. CORE DATABASE & DATA MODELS (`src/models.py`)
Dưới đây là cấu trúc Entity mà toàn bộ dữ liệu luân chuyển trong dự án. Để tương tác với cơ sở dữ liệu hoặc AI, LLM nên tạo/trả về các objects này:

```python
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, List
from .constants import TransactionType, ActionType, ReportType

@dataclass
class Transaction:
    """Transaction model mapped to Database row."""
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

@dataclass
class AIAction:
    """Data object extracted from Natural Language (via Gemini AI)."""
    action: ActionType
    amount: Optional[float] = None
    category: Optional[str] = None
    tx_type: Optional[TransactionType] = None
    date_offset: int = 0  # 0=today, 1=yesterday, 2=day before
    time_of_day: Optional[str] = None  # sáng/trưa/chiều/tối
    target_date: Optional[date] = None
    transaction_id: Optional[int] = None
    keyword: Optional[str] = None
    report_type: Optional[ReportType] = None
    limit: int = 10
    message: Optional[str] = None
```

## Cách sử dụng Context này
- **Khi thêm tính năng vào Bot:** Sử dụng logic từ `src/handlers/messages.py` để hứng data từ `aiogram`, rồi đẩy qua `src/services/ai.py` để lấy `AIAction`. Tiếp đó, gọi hàm trong `src/services/database.py` để lưu/truy vấn dưới dạng `Transaction`.
- **Khi thêm chức năng vào Web:** Thêm route vào thư mục `src/web/routes.py` theo đúng cấu trúc Flask Blueprint (được attach ở `main.py`).
