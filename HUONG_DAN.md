# Hướng dẫn cài đặt và sử dụng

## Cài đặt

### Bước 1: Chạy setup
```
setup.bat
```
Hoặc cài tay:
```
pip install python-telegram-bot==21.3 streamlit pandas python-dotenv pillow matplotlib plotly numpy libsql==0.1.11 tzdata
```

### Bước 2: Tạo Telegram Bot
1. Mở Telegram, tìm **@BotFather**
2. Gõ `/newbot` → đặt tên → lấy **Token**
3. Tìm **@userinfobot**, nhắn `/start` → lấy **Chat ID** của bạn

### Bước 3: Cấu hình
Mở file `.env`, điền:
```
TELEGRAM_TOKEN=123456:ABC-DEF...
ALLOWED_CHAT_ID=987654321
```

### Bước 4: Chạy
```bash
# Chạy cả Telegram bot + Web UI
python main.py

# Chỉ Web UI (không cần Telegram)
python main.py --web

# Chỉ Telegram bot
python main.py --bot
```

Web UI mở tại: http://localhost:8501

---

## Cách sử dụng

### Telegram Bot
| Lệnh | Mô tả |
|------|-------|
| `/manual` | Nhập tay 4 yêu quái + thầy → dự đoán |
| `/result Hồ Ly` | Lưu kết quả (tên yêu quái thắng) |
| `/result thầy` | Lưu kết quả (thầy thoát) |
| `/stats` | Xem thống kê |
| `/cancel` | Hủy thao tác hiện tại |

### Web UI
- **Tab Dự đoán**: Nhập tay 4 yêu quái + thầy → nhận dự đoán + biểu đồ
- **Tab Nhập kết quả**: Chọn trận và nhập kết quả sau khi xem
- **Tab Thống kê**: Biểu đồ tỷ lệ thắng tổng quát và từng nhân vật
- **Tab Lịch sử**: Toàn bộ lịch sử các trận

---

## Cách bot dự đoán

Bot sử dụng 3 phương pháp (theo thứ tự ưu tiên):

1. **Pattern** (xanh): Tìm các trận có chính xác 4 yêu quái + thầy + bội số giống nhau → tính tỷ lệ thắng từ lịch sử
2. **Individual** (vàng): Dùng tỷ lệ thắng của từng nhân vật trong toàn bộ lịch sử
3. **Multiplier** (đỏ): Khi chưa có dữ liệu → ước tính dựa vào bội số (bội số thấp = xác suất cao hơn)

**Bot càng học nhiều trận, dự đoán càng chính xác!**

---

## Cấu trúc file
```
├── main.py          — Entry point
├── web_app.py       — Giao diện web (Streamlit)
├── telegram_bot.py  — Telegram bot
├── predictor.py     — Logic dự đoán
├── database.py      — Database (SQLite local / Turso online)
├── config.py        — Cấu hình
├── .env             — Token Telegram (tạo từ .env.example)
└── data/
    └── rounds.db    — Database SQLite (tự tạo khi chạy)
```
