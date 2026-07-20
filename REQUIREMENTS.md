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

### FR1 — Dự đoán theo thống kê lịch sử
- Khi đã đủ dữ liệu (≥10 trận có kết quả), dùng **thống kê thắng/xuất hiện** của từng nhân vật + từng **giá trị bội** trong toàn bộ lịch sử để ước lượng xác suất.
- ~~Dự đoán theo pattern cùng combo 4 yêu quái~~ — **đã bỏ (2026-07-20)**: với 18 yêu quái, tổ hợp lặp lại quá hiếm để đủ mẫu (199 pattern khác nhau / 279 trận); đo thực tế tầng này không cải thiện dự đoán. `pattern_key` vẫn được lưu trong DB làm metadata.

### FR2 — Dự đoán khi chưa có dữ liệu
- Nếu chưa đủ dữ liệu lịch sử, dùng **bội số** để ước tính (bội số thấp → xác suất cao hơn).

### FR3 — Đầu ra dự đoán
Với mỗi trận, hệ thống trả về:
- **Xác suất % cho từng nhân vật** (5 nhân vật, tổng = 100%).
- **Khuyến nghị**: nhân vật có xác suất cao nhất.
- **Giá trị kỳ vọng (Expected Value = xác suất × bội số)** và nhân vật có EV tốt nhất.
- Phương pháp đã dùng (thống kê cá nhân / odds) + số mẫu + độ tin cậy (theo số mẫu của tầng bội).
- Lịch sử [thắng/xuất hiện] của từng nhân vật.

### FR4 — Bot Telegram
- `/manual` — nhập tay từng nhân vật + bội số → trả dự đoán.
- `/result <tên>` hoặc bấm nút inline — ghi lại **ai đã thắng** sau khi trận kết thúc.
- `/stats` — xem thống kê tổng quát.
- `/cancel` — hủy thao tác.

### FR5 — Web UI (Streamlit)
- Tab **Dự đoán**: nhập tay theo đúng cấu trúc game — **2 yêu quái bội THẤP (3–5) + 2 yêu quái bội CAO (6–12)** + bội Thầy (`Duong_tang` cố định, không cần chọn tên). Cả **tên lẫn bội đều chọn bằng nút 1 chạm** (không dropdown) — mỗi nhóm chỉ gợi ý đúng các con thuộc dải bội đó (9 con bội thấp/cao xếp ABC). Con đã chọn ở slot cùng nhóm bị **làm mờ (disable)** thay vì ẩn đi → không nhảy layout. Luật game: **cược tối đa 2 con/trận**, có thể **bỏ trận** (không cược). Kết quả hiện **một KHUYẾN NGHỊ CHÍNH = chính sách tổng-lợi tối ưu** (chứng minh qua backtest, xem SOLUTION.md mục 9): **kỷ luật bội 5 & 9** — chỉ cược yêu quái mang bội đã chứng minh +EV chắc (thực tế bội 5 & 9), favorite trước, tối đa 2 con; không có con nào đủ tin → **khuyên BỎ TRẬN**. Phần **"mô hình EV nghĩ gì"** hạ xuống mục **tham khảo** (thu gọn) vì đi theo EV mô hình dài hạn lãi ít hơn; **không bao giờ khuyến nghị cược Thầy** (mọi tầng bội Thầy đều −EV, kèo Thầy mô hình chấm cao thắng ~1/50). Mỗi con kèm **mức cược gợi ý (¼-Kelly)**. Có nút **Nhập trận mới**. Đầu tab có ghi nhớ **kèo heuristic theo bội tự tính từ data sống** (ROI + CI, tự cập nhật theo số trận).
- Tab **Nhập kết quả**: chọn trận đang chờ và ghi nhận người thắng.
- Tab **Thống kê**: tỷ lệ thắng tổng quát, theo từng nhân vật, và biểu đồ **Odds Calibration** (so sánh tỷ lệ thắng thực tế vs xác suất implied từ bội số).
- Tab **Soi cầu**: thống kê kiểu xổ số cho **yêu quái bội cao (≥9)** và **Thầy** — mỗi nhân vật đã **lâu bao nhiêu lần chưa về đích/thoát** ("đang khan") và **trung bình quá khứ cứ bao nhiêu lần thì về 1 lần** ("chu kỳ TB" = số lần xuất hiện ÷ số lần về). Gom **theo tên**, có bộ lọc phạm vi bội cho yêu quái (Gộp 9–12 / Chỉ 9 / Chỉ 10–12) và ngưỡng số lần xuất hiện tối thiểu. **Thầy là trường hợp đặc biệt**: có mặt mọi trận, bội thang riêng (14–26) → không lọc theo bội≥9, đơn vị tính theo **số trận** (yêu quái tính theo số lần ra sân bội≥9), luôn hiển thị đầu bảng. Đánh dấu 🔥 nhân vật "quá hạn" (đang khan ≥ chu kỳ TB). Có cảnh báo rõ đây là gambler's fallacy, chỉ tham khảo.
- Tab **Lịch sử**: bảng các trận đã ghi.

### FR6 — Dữ liệu & học dần
- Mỗi lần dự đoán xong và biết kết quả, người dùng phản hồi → hệ thống **tích lũy dữ liệu mới** và dự đoán ngày càng tốt hơn.
- **Tự tinh chỉnh tham số** khi tổng số trận vượt mỗi mốc 50 (không cần bấm tay); tham số + cache mô hình lưu trên database dùng chung nên bot (PC) và Web (Cloud) luôn đồng bộ.

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

## 7. Trạng thái hiện tại (tính đến 2026-07-20)

- ✅ Database, predictor, Telegram bot, Web UI: hoạt động.
- ✅ Web UI deploy lên Streamlit Community Cloud, dùng chung database Turso với bot Telegram trên PC — ghi/đọc từ xa hoạt động đúng (test: ghi kết quả qua Web Cloud, đọc thấy ngay từ script khác dùng cùng database).
- ✅ `ALLOWED_CHAT_ID` đã đặt trong `.env`.
- ✅ Token Telegram (từng bị lộ trong log) đã revoke và thay mới (2026-07-20).
- ✅ Review thống kê 2026-07-20: bỏ tầng pattern, đổi tiêu chí tune sang logloss, ROI hiển thị kèm CI 95% — xem SOLUTION.md mục 9.
- ℹ️ Chức năng **upload ảnh (OCR)** và **import CSV** đã được gỡ bỏ (2026-07-13) — chỉ còn nhập tay. Dữ liệu nhập qua Web/Telegram thủ công.
