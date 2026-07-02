# Tài liệu Yêu cầu — Hệ thống Dự đoán "Sư Phụ Chạy Mau" (Đua Thầy)

> Tài liệu này mô tả **bài toán và yêu cầu**. Phần kiến trúc & cách hệ thống
> đang giải quyết nằm ở [SOLUTION.md](SOLUTION.md).

---

## 1. Bối cảnh

"Sư Phụ Chạy Mau" (Đua Thầy) là một mini-game tiếng Việt. Mỗi **trận (round)**:

- Có **4 yêu quái** (monsters) đuổi theo **1 Sư Phụ** (teacher — `Duong_tang` / Đường Tăng).
- Mỗi nhân vật (4 yêu quái + thầy) có một **bội số / odds** (số nhân tiền cược nếu nhân vật đó thắng). Bội số trong game là **số nguyên** (vd: 3, 5, 9, 12, 20).
- Kết quả trận: **đúng 1 trong 5 nhân vật thắng** — hoặc một yêu quái bắt được thầy, hoặc **thầy thoát**.
- Người chơi cược vào nhân vật mình nghĩ sẽ thắng. Bội số càng cao = game cho rằng xác suất thắng càng thấp.

Mục tiêu của hệ thống: **dự đoán nhân vật nào có khả năng thắng cao nhất** dựa trên dữ liệu lịch sử + bội số, để hỗ trợ quyết định cược.

## 2. Người dùng

- Một người dùng duy nhất (chủ sở hữu), chơi trên điện thoại, chạy hệ thống **local trên PC Windows**.
- Không cần multi-user, không cần deploy cloud, không cần auth phức tạp (chỉ giới hạn theo Telegram Chat ID là đủ).

## 3. Yêu cầu chức năng

### FR1 — Dự đoán theo pattern lịch sử
- Khi **cùng một combo 4 yêu quái + thầy** xuất hiện lại (bất kể bội số thay đổi), dùng kết quả các trận lịch sử cùng combo để dự đoán.
- Pattern được nhận diện theo **tên nhân vật, KHÔNG theo bội số** (bội số mỗi trận có thể khác nhau nhưng combo vẫn là một).

### FR2 — Dự đoán khi chưa có pattern
- Nếu chưa đủ số mẫu cùng combo, dùng **thống kê thắng/xuất hiện của từng nhân vật** trong toàn bộ lịch sử.
- Nếu vẫn chưa đủ dữ liệu, dùng **bội số** để ước tính (bội số thấp → xác suất cao hơn).

### FR3 — Đầu ra dự đoán
Với mỗi trận, hệ thống trả về:
- **Xác suất % cho từng nhân vật** (5 nhân vật, tổng = 100%).
- **Khuyến nghị**: nhân vật có xác suất cao nhất.
- **Giá trị kỳ vọng (Expected Value = xác suất × bội số)** và nhân vật có EV tốt nhất.
- Phương pháp đã dùng (pattern / cá nhân / odds) + số mẫu.
- Lịch sử [thắng/xuất hiện] của từng nhân vật.

### FR4 — Bot Telegram
- Gửi **ảnh chụp màn hình** trận → OCR đọc 4 yêu quái + thầy + bội số → xác nhận/sửa → trả dự đoán.
- `/manual` — nhập tay từng nhân vật + bội số nếu không muốn dùng ảnh.
- `/result <tên>` hoặc bấm nút inline — ghi lại **ai đã thắng** sau khi trận kết thúc.
- `/stats` — xem thống kê tổng quát.
- `/cancel` — hủy thao tác.

### FR5 — Web UI (Streamlit)
- Tab **Dự đoán**: nhập tay 4 yêu quái + thầy + bội số, HOẶC upload ảnh để OCR.
- Tab **Nhập kết quả**: chọn trận đang chờ và ghi nhận người thắng.
- Tab **Thống kê**: tỷ lệ thắng tổng quát, theo từng nhân vật, và biểu đồ **Odds Calibration** (so sánh tỷ lệ thắng thực tế vs xác suất implied từ bội số).
- Tab **Lịch sử**: bảng các trận đã ghi.
- Tab **Import CSV**: upload file CSV lịch sử từ trình duyệt.

### FR6 — Dữ liệu & học dần
- Import **dữ liệu lịch sử từ CSV** (format `Round_id, Competitor, Odds, Is_winner`). Dữ liệu gốc: 63 trận.
- Mỗi lần dự đoán xong và biết kết quả, người dùng phản hồi → hệ thống **tích lũy dữ liệu mới** và dự đoán ngày càng tốt hơn.
- **Không import trùng**: cùng `Round_id` từ cùng nguồn chỉ import một lần.

## 4. Yêu cầu phi chức năng

- **Nền tảng**: Windows 11, Python 3.12, terminal PowerShell (cp1252 — **không in emoji trong `print()`**, dùng ASCII).
- **Lưu trữ**: SQLite local (`data/rounds.db`), không cần DB server.
- **Chạy đồng thời**: Telegram bot + Web UI trong cùng tiến trình (`python main.py`).
- **Bảo mật**: token Telegram để trong `.env` (không commit). Có thể giới hạn `ALLOWED_CHAT_ID`.
- **Tiếng Việt**: toàn bộ UI và OCR hỗ trợ tiếng Việt. Tên nhân vật lưu dạng không dấu, gạch dưới (vd `Bach_nhan_quan`).

## 5. Ràng buộc dữ liệu

- Mỗi trận **đúng 4 yêu quái + đúng 1 thầy** (`Duong_tang`). Trận không đủ 4 yêu quái hoặc thiếu thầy bị bỏ qua khi import.
- Danh sách 18 yêu quái đã biết + 1 thầy nằm trong [config.py](config.py) (`KNOWN_MONSTERS`, `KNOWN_TEACHERS`).
- Một trận có thể được lưu **trước khi có kết quả** (`winner = NULL`) — trận đó không ảnh hưởng tới thống kê cho tới khi nhập kết quả. Bỏ ngang không gây lỗi.

## 6. Ngoài phạm vi (Out of scope)

- Không tự động đặt cược / không thao tác tiền thật.
- Không dự đoán thời gian thực trong khi trận đang chạy.
- Không multi-user / không cloud / không mobile app riêng.
- Không đảm bảo thắng — đây là công cụ hỗ trợ thống kê, kết quả game có yếu tố ngẫu nhiên.

## 7. Trạng thái hiện tại (tính đến 2026-06-12)

- ✅ Database, predictor, Telegram bot, Web UI, import CSV: hoạt động.
- ✅ Đã import 63 trận. Một số thống kê: `Bach_nhan_quan` ~56% thắng, `Thanh_nguu` ~53%, thầy `Duong_tang` chỉ ~9.5%.
- ⏳ Cần đặt `ALLOWED_CHAT_ID` trong `.env` (lấy từ @userinfobot).
- ⏳ Token Telegram từng bị lộ trong log → nên revoke qua @BotFather và thay token mới.
- ⏳ OCR (EasyOCR) chưa test với ảnh game thật (cần `pip install easyocr`).
