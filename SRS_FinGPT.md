# Software Requirements Specification (SRS) - FinGPT V2

## 1. Tổng quan dự án (Project Overview)
**FinGPT V2** là một hệ thống quản lý tài chính cá nhân toàn diện, cho phép người dùng nhập liệu thu/chi qua giao diện chat tự nhiên trên Telegram (sử dụng trí tuệ nhân tạo để bóc tách thông tin) và theo dõi, thống kê trực quan thông qua một Web Dashboard. 

Mục tiêu của dự án là loại bỏ sự phức tạp và cứng nhắc của các ứng dụng nhập liệu tài chính truyền thống, mang lại trải nghiệm "nhắn tin như với một người trợ lý" và được lưu trữ đồng bộ lên Cloud Database.

## 2. Các chức năng chính (Functional Requirements)

### 2.1. Telegram Bot (Giao diện nhập liệu chính)
*   **Xử lý ngôn ngữ tự nhiên (NLP for Finance)**: Người dùng có thể nhập các câu lệnh tự nhiên (VD: "Nay đổ xăng 50k", "Hôm qua đi chợ 200k", "Lương tháng này 10 củ").
*   **Nhận diện hình ảnh (OCR)**: Cho phép gửi ảnh hóa đơn/bill chuyển khoản để bóc tách tự động các thông tin chuyển tiền.
*   **Xử lý các loại hành động (Actions)**:
    *   **Thêm (Insert)**: Thêm giao dịch (thu/chi).
    *   **Sửa (Update)**: Điều chỉnh lại thông tin nếu Bot nhận diện sai (VD: "À nhầm, chỉ 30k thôi").
    *   **Xóa (Delete)**: Hủy bỏ một giao dịch.
    *   **Truy vấn (Query/Report)**: Hỏi hoặc lập báo cáo nhanh (VD: "Tháng này tiêu bao nhiêu rồi?").
    *   **Undo/Clear**: Quản lý lịch sử thao tác.

### 2.2. Xử lý AI (AI Parser)
*   Nhận phân tích tin nhắn text hoặc hình ảnh thông qua API **Google Gemini Pro**.
*   Trích xuất dữ liệu thô thành Data JSON với các trường:
    *   `action`: Hành động cần thực hiện (thêm, sửa, xoá...).
    *   `amount`: Số tiền (tự động convert k/tr/củ/điểm thành số thật).
    *   `category`: Danh mục (thuộc hệ thống category đã định trước).
    *   `type`: Loại (thu/chi). Mặc định là chi, nếu có các từ khóa như "lương", "thưởng", "được cho"... thì là thu.
    *   `ngày/thực gian`: Tính toán offset (hôm nay, hôm qua, hôm kia) và buổi (sáng/trưa/chiều/tối).

### 2.3. Web Dashboard
*   **Authentication**: Yêu cầu người dùng đăng nhập để xem thông tin an toàn.
*   **Tổng quan (Overview)**: Hiển thị các block thống kê thu nhập, chi tiêu, số dư hiện tại.
*   **Biểu đồ (Charts)**: Trực quan hóa chi tiêu theo các danh mục và trục thời gian.
*   **Quản lý giao dịch**: Xem dạng danh sách toàn bộ các lịch sử giao dịch (bao gồm tính năng lọc và phân trang).

## 3. Kiến trúc kỹ thuật & Công nghệ (Tech Stack)

*   **Ngôn ngữ lập trình**: Python 3.10+
*   **Telegram Bot Framework**: `aiogram` >= 3.0.0 (Xử lý request bất đồng bộ từ Telegram).
*   **Web Framework**: `flask` >= 3.0.0 (Dùng để serve Dashboard UI và chạy Background Server giữ Bot hoạt động).
*   **AI Engine**: `google-genai` (Sử dụng model của Gemini để phân tích text & image).
*   **Cơ sở dữ liệu**:
    *   Sử dụng **PostgreSQL** (chủ yếu host trên Supabase).
    *   Giao tiếp Database bằng `asyncpg`.
*   **Kiến trúc chương trình chính**: Chạy song song – một Thread phục vụ Flask Web App (Web Dashboard + HealthCheck), và thread chính `asyncio` để chạy Bot Polling.

## 4. Cấu trúc Database (Data Models)

### Bảng Transactions
Lưu trữ thông tin chi tiết từng giao dịch thành công.
*   `id` (int): Khóa chính
*   `user_id` (int): Mã người dùng Telegram
*   `amount` (float): Số tiền
*   `category` (str): Danh mục chi tiêu/thu nhập
*   `note` (str): Ghi chú (Thường là giữ luôn câu chat nguyên gốc của người dùng)
*   `type` (Enum): Loại `THU` (Income) hoặc `CHI` (Expense)
*   `transaction_date` (date): Ngày thực tế phát sinh giao dịch
*   `created_at`, `updated_at` (datetime): Thời gian hệ thống
*   `is_deleted` (bool): Soft delete cho tính năng xóa/undo

### AI Action Model (Tham khảo)
Model tạm thời (in-memory) ứng với JSON trả về từ Google Gemini.
*   Chứa thông tin bóc tách: Hành động (action), khoản tiền, danh mục, Offset ngày (0 = hôm nay, 1 = hôm qua), Keyword tìm kiếm, và Message (phản hồi text để Bot gửi lại user).

## 5. Quy trình cấu hình và cài đặt (Deployment)
*   Biến môi trường cần thiết (`.env`):
    *   `TELEGRAM_BOT_TOKEN`
    *   `GEMINI_API_KEY`
    *   `DATABASE_URL` (PostgreSQL Connection String)
    *   `DASHBOARD_SECRET`
*   Khởi chạy: Chỉ cần `python main.py` là cả hệ sinh thái Bot và Web sẽ cùng Start.

---
*Tài liệu này được trích xuất dựa trên source code hiện tại, được dùng để cung cấp context cho các phiên lập trình mà không cần phải thực hiện rà soát lại toàn bộ dự án.*
