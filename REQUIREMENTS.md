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

- Một người dùng duy nhất (chủ sở hữu), chơi trên điện thoại. Bot Telegram chạy **local trên PC Windows**; Web UI chạy **online (Streamlit Community Cloud)** nên mở được từ điện thoại/bất kỳ đâu, kể cả khi PC tắt — và vẫn nhập/ghi được kết quả trận từ xa (không chỉ xem).
- Không cần multi-user, không cần auth phức tạp (chỉ giới hạn theo Telegram Chat ID là đủ).

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
- `/manual` — nhập tay từng nhân vật + bội số → trả dự đoán.
- `/result <tên>` hoặc bấm nút inline — ghi lại **ai đã thắng** sau khi trận kết thúc.
- `/stats` — xem thống kê tổng quát.
- `/cancel` — hủy thao tác.

### FR5 — Web UI (Streamlit)
- Tab **Dự đoán**: nhập tay 4 yêu quái + thầy + bội số.
- Tab **Nhập kết quả**: chọn trận đang chờ và ghi nhận người thắng.
- Tab **Thống kê**: tỷ lệ thắng tổng quát, theo từng nhân vật, và biểu đồ **Odds Calibration** (so sánh tỷ lệ thắng thực tế vs xác suất implied từ bội số).
- Tab **Lịch sử**: bảng các trận đã ghi.

### FR6 — Dữ liệu & học dần
- Mỗi lần dự đoán xong và biết kết quả, người dùng phản hồi → hệ thống **tích lũy dữ liệu mới** và dự đoán ngày càng tốt hơn.

## 4. Yêu cầu phi chức năng

- **Nền tảng**: bot Telegram chạy trên Windows 11, Python 3.12, terminal PowerShell (cp1252 — **không in emoji trong `print()`**, dùng ASCII). Web UI chạy trên Streamlit Community Cloud (Linux, tự redeploy khi push code).
- **Lưu trữ**: database online dùng chung (Turso/libSQL) là nguồn dữ liệu sống, để bot (PC) và Web UI (Cloud) luôn thấy cùng dữ liệu. SQLite local (`data/rounds.db`) vẫn giữ lại làm fallback khi chưa cấu hình Turso, cho backtest/tune tham số (chạy nhanh, không qua mạng), và làm lưới an toàn rollback.
- **Chạy đồng thời**: Telegram bot chạy trên PC (`python main.py` hoặc `--bot`); Web UI chạy như một deployment **riêng biệt** trên Streamlit Cloud, không cùng tiến trình/máy với bot nữa — đồng bộ qua database dùng chung.
- **Bảo mật**: token Telegram để trong `.env` (không commit). Turso URL/token để trong `.env` (local) và Streamlit Cloud Secrets (Web). Có thể giới hạn `ALLOWED_CHAT_ID`.
- **Tiếng Việt**: toàn bộ UI hỗ trợ tiếng Việt. Tên nhân vật lưu dạng không dấu, gạch dưới (vd `Bach_nhan_quan`).

## 5. Ràng buộc dữ liệu

- Mỗi trận **đúng 4 yêu quái + đúng 1 thầy** (`Duong_tang`). Trận không đủ 4 yêu quái hoặc thiếu thầy bị bỏ qua khi import.
- Danh sách 18 yêu quái đã biết + 1 thầy nằm trong [config.py](config.py) (`KNOWN_MONSTERS`, `KNOWN_TEACHERS`).
- Một trận có thể được lưu **trước khi có kết quả** (`winner = NULL`) — trận đó không ảnh hưởng tới thống kê cho tới khi nhập kết quả. Bỏ ngang không gây lỗi.

## 6. Ngoài phạm vi (Out of scope)

- Không tự động đặt cược / không thao tác tiền thật.
- Không dự đoán thời gian thực trong khi trận đang chạy.
- Không multi-user / không mobile app riêng.
- Không đảm bảo thắng — đây là công cụ hỗ trợ thống kê, kết quả game có yếu tố ngẫu nhiên.

## 7. Trạng thái hiện tại (tính đến 2026-07-02)

- ✅ Database, predictor, Telegram bot, Web UI: hoạt động.
- ✅ Web UI deploy lên Streamlit Community Cloud, dùng chung database Turso với bot Telegram trên PC — ghi/đọc từ xa hoạt động đúng (test: ghi kết quả qua Web Cloud, đọc thấy ngay từ script khác dùng cùng database).
- ⏳ Cần đặt `ALLOWED_CHAT_ID` trong `.env` (lấy từ @userinfobot).
- ⏳ Token Telegram từng bị lộ trong log → nên revoke qua @BotFather và thay token mới.
- ℹ️ Chức năng **upload ảnh (OCR)** và **import CSV** đã được gỡ bỏ (2026-07-13) — chỉ còn nhập tay. Dữ liệu nhập qua Web/Telegram thủ công.
