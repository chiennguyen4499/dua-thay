# Tài liệu Giải pháp & Kiến trúc — Đua Thầy Predictor

> Mô tả **hệ thống đang giải quyết bài toán như thế nào**. Yêu cầu gốc ở
> [REQUIREMENTS.md](REQUIREMENTS.md). Tài liệu này đủ để nạp context khi mở session mới.

---

## 1. Tổng quan kiến trúc

```
  Telegram bot (PC, python main.py --bot)      Web UI (Streamlit Community Cloud)
         │                                              │
         │            ┌──────────────┐                  │
         ├──────────► │  predictor   │ ◄────────────────┤
         │            │ (3 tầng blend)│                  │
         │            └──────┬───────┘                  │
         │                   ▼                          │
         │            ┌──────────────┐                  │
         └──────────► │  database.py │ ◄────────────────┘
                       │ get_conn():  │
                       │ local hay    │
                       │ remote?      │
                       └──────┬───────┘
                    ┌─────────┴─────────┐
                    ▼                   ▼
           SQLite local             Turso (libSQL)
           data/rounds.db           — nguồn dữ liệu SỐNG dùng
           (dev/backtest/           chung giữa bot (PC) và
            tune/rollback)          Web UI (Cloud)
```

Bot chạy trên PC (`python main.py` hoặc `--bot`), Web UI chạy **độc lập** trên
Streamlit Community Cloud (`web_app.py`, tự redeploy khi push code). Cả hai
cùng đọc/ghi **Turso** — không còn chung 1 process/máy như trước. Tất cả module
vẫn dùng chung `database.py` và `predictor.py`; `database.py.get_conn()` tự
quyết định local hay remote (xem mục 3 và 9).

## 2. Danh sách file

| File | Vai trò |
|------|---------|
| [config.py](config.py) | Cấu hình, đọc `.env`; `KNOWN_MONSTERS` (18), `KNOWN_TEACHERS`, `TEACHER_DEFAULT="Duong_tang"`, `MIN_SAMPLES_FOR_PATTERN=3`. **`DATABASE_PATH` neo theo `__file__`** (thư mục dự án), không theo CWD → chạy/di chuyển thư mục từ đâu cũng đúng DB. `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` (để trống = chỉ dùng SQLite local) |
| [database.py](database.py) | Schema, lưu/đọc trận, thống kê, pattern key. `get_conn()` tự chọn SQLite local hay Turso (remote) — xem mục 3 và 9 |
| [predictor.py](predictor.py) | Thuật toán dự đoán 3 tầng + format text Telegram |
| [telegram_bot.py](telegram_bot.py) | Bot: nhập tay (`/manual`), `/result`, `/stats` |
| [web_app.py](web_app.py) | Streamlit UI 4 tab |
| [backtest.py](backtest.py) | Walk-forward backtest: đo predictor vs odds-only vs uniform (logloss/brier/top-1) |
| [tune_shrinkage.py](tune_shrinkage.py) | Quét tham số K (ODDS_CALIB / NAME_ODDS / INDIVIDUAL) theo **ROI walk-forward (EV tối đa)** để retune khi có thêm dữ liệu; ghi `tuned_params.json` kèm `optimized_by="roi_ev"` |
| [strategy_analysis.py](strategy_analysis.py) | Kiểm chứng heuristic cược (bội to/nhỏ, thầy bội cao) + ROI từng chiến lược; **ROI "theo mô hình" tính leave-one-out** (`compute_model_picks` + `aggregate_model_strategies`) |
| [main.py](main.py) | Entry point chạy bot + web |
| `.env` / [.env.example](.env.example) | Token & cấu hình (Telegram, Turso) |
| `data/rounds.db` | Database SQLite local — dev/backtest/tune/rollback. **Kể từ khi có Turso, không còn là nguồn dữ liệu sống chính** (xem mục 9) |
| [requirements.txt](requirements.txt) | Dependencies |

## 3. Mô hình dữ liệu

Một bảng duy nhất `rounds` ([database.py:13](database.py#L13)):

```
id                    INTEGER PK
created_at            TEXT (localtime)
monster1_name..4_name TEXT          -- 4 yêu quái
monster1_multiplier..4 REAL         -- bội số tương ứng
teacher_name          TEXT          -- thường 'Duong_tang'
teacher_multiplier    REAL
winner                TEXT NULL      -- 'monster1'..'monster4' | 'teacher' | NULL (chưa có kết quả)
pattern_key           TEXT          -- key combo, có index
source                TEXT          -- 'manual'|'web'|'telegram_manual' (giá trị cũ 'csv_import'/'telegram_ocr' còn trong data lịch sử)
notes                 TEXT          -- vd 'original_id:62' để chống import trùng
```

**Quy ước quan trọng:**
- `winner` lưu **slot** (`monster1`...), không lưu tên — vì cùng tên có thể ở slot khác nhau giữa các trận.
- Mọi query thống kê đều có `WHERE winner IS NOT NULL` → trận chưa có kết quả **không** ảnh hưởng thống kê/dự đoán. Bỏ ngang an toàn.
- **Chuẩn hóa tên**: `save_round` và `predict` đều gọi `config.canonical_name()` → mọi nguồn (web/telegram nhập tay) quy về cùng tên canonical, tránh phân mảnh ("Bach nhan quan" = "bach_nhan_quan" = `Bach_nhan_quan`).
- **Đồng thời (local)**: khi dùng SQLite local, `get_conn` bật `PRAGMA journal_mode=WAL` + `busy_timeout=5000` để bot (thread) và web (process riêng) không bị "database is locked".
- **Đồng thời (remote)**: khi đã cấu hình Turso, bot (PC) và Web UI (Streamlit Cloud) là 2 tiến trình/máy hoàn toàn khác nhau, đọc/ghi cùng 1 database Turso — Turso tự lo concurrency phía server, không cần WAL/busy_timeout (2 PRAGMA này chỉ chạy ở nhánh local).

### Pattern key ([database.py:45](database.py#L45))
```python
sorted_names = sorted(m["name"] for m in monsters)
return "|".join(sorted_names) + f"|T:{teacher['name']}"
```
→ Chỉ gồm **tên** (đã sort), **không gồm bội số**. Cùng combo dù bội số khác nhau = cùng pattern. Đây là quyết định thiết kế then chốt để pattern tích lũy nhanh.

## 4. Thuật toán dự đoán (predictor.py)

3 tầng, tự chọn theo lượng dữ liệu. Hàm chính: `predict(monsters, teacher)` → dict gồm `method`, `probabilities`, `recommendation`, `best_value` (EV), `details`, `message`.

### Bước nền: xác suất implied từ bội số
`_implied_prob`: chuẩn hóa `1/odds` trên toàn bộ 5 nhân vật → `q[name]`. Bội số thấp → q cao.

### Tầng 1 — Pattern (`method="pattern"`)
Điều kiện: số trận **cùng pattern_key** ≥ `MIN_SAMPLES_FOR_PATTERN` (=3).
- Đếm số lần mỗi tên thắng trong các trận cùng combo.
- **Bayesian smoothing** với prior = bội số: `prob = (observed_wins + q*pseudo) / (n + pseudo)`, `pseudo=3`. Rồi normalize.

### Tầng 2 — Thống kê cá nhân + calibration theo BỘI SỐ (`method="individual"`)
Điều kiện: tổng trận có kết quả trong DB ≥ 10. **Shrinkage Beta-Binomial PHÂN TẦNG**, từ thô đến tinh (mỗi tầng là prior cho tầng kế, kéo về nhau theo số mẫu — hàm `_shrink`):

```
1/odds (q)
  └─► win-rate GỘP theo giá trị bội   (K = ODDS_CALIB_STRENGTH)
        └─► win-rate theo TÊN nhân vật (K = INDIVIDUAL_PRIOR_STRENGTH)
              └─► win-rate theo (TÊN × bội) (K = NAME_ODDS_STRENGTH)
```

> Giá trị K mặc định trong [config.py](config.py) (25 / 3 / 0) bị `tuned_params.json`
> ghi đè nếu có. Bộ đang dùng (tune theo **ROI** 2026-06): `INDIVIDUAL=40`,
> `ODDS=2`, `NAME_ODDS=10` — xem [tune_shrinkage.py](tune_shrinkage.py).

- **Tầng giá trị bội** (mới) gộp mọi tên cùng một giá trị bội → đủ mẫu, ổn định. Đây là chỗ bắt trực giác "bội X hay/không hay về": vd bội 5 thắng ~38%, bội 10 thắng 0/21, **thầy bội ≥18 hay thoát** (1/odds không thấy được điều này). Bảng lấy từ `get_monster_odds_winrate` / `get_teacher_odds_winrate`.
- **Tầng tên** kéo về tầng giá trị bội (thay vì kéo thẳng về odds như bản cũ): nhân vật ít mẫu sẽ mượn calibration của bội số nó đang mang.
- **Tầng (tên × bội)**: từng mặc định TẮT (`NAME_ODDS_STRENGTH=0`) vì ở ~77 trận tối ưu logloss thì bật vào bị overfit. Nhưng từ khi **tiêu chí tune đổi sang ROI** (xem dưới), tuner tự bật (`NAME_ODDS=10`) vì nó cải thiện ROI — cơ chế auto-ready hoạt động đúng như thiết kế.
- Đã xác nhận qua [backtest.py](backtest.py): tầng calibration theo bội giảm logloss 1.532 → **1.504** (so odds-only 1.571), brier/pwin/top1 đều tốt hơn bản chỉ-theo-tên. Quét K bằng [tune_shrinkage.py](tune_shrinkage.py).
- Hiển thị: cột **"Bội này về"** (web) và "· bội này về X%" (Telegram) cho thấy tỷ lệ thắng thực tế của giá trị bội đó.

> **Tiêu chí tune đổi từ logloss → ROI/EV** ([tune_shrinkage.py](tune_shrinkage.py)): mỗi
> tổ hợp K được mô phỏng cược 1 đơn vị/trận vào nhân vật có EV cao nhất (walk-forward),
> chọn bộ **ROI cao nhất** thay vì logloss thấp nhất. Lý do: logloss đo calibration,
> không đo trực tiếp lợi nhuận — bộ logloss tốt nhất có thể KHÔNG phải bộ kiếm tiền
> tốt nhất. `tuned_params.json` ghi thêm `_meta.roi_ev` và `optimized_by="roi_ev"`.

### Tầng 3 — Chỉ odds (`method="multiplier"`)
Khi DB < 10 trận: dùng thẳng `q` (xác suất implied từ bội số).

### Đầu ra phụ
- **Recommendation** = argmax xác suất.
- **Best value (EV)** = argmax `prob × odds`. Nếu khác recommendation thì hiển thị riêng (cơ hội "giá trị" — xác suất vừa phải nhưng bội số cao).
- `format_prediction_text()` dựng Markdown cho Telegram (bar chart `█`, `[won/appeared]`).

## 5. Luồng Telegram (telegram_bot.py)

- **Nhập tay** `/manual`: 2 state — `SELECT_MONSTERS` (inline keyboard chọn 4 yêu quái từ `KNOWN_MONSTERS`, bấm toggle, đánh số 1️⃣–4️⃣, nút ✅ Xong chỉ hiện khi đủ 4) → `ENTER_MULTS` (nhập **5 bội số trên 1 dòng**: 4 yêu quái theo thứ tự + Thầy, vd `3 4 10 12 20`). Thầy mặc định `TEACHER_DEFAULT`. Chọn từ danh sách nên tên luôn canonical, không tạo biến thể mới.
- Sau dự đoán: lưu trận (`winner=NULL`), nhớ `last_round_id`, hiện nút inline chọn người thắng (`result|<id>|<slot>`).
- **Ghi kết quả**: nút inline (`result_callback`) hoặc `/result <tên>` (`result_command`, map tên→slot, "thầy/sư phụ/teacher" → `teacher`).
- **`/cancel`**: đăng ký **toàn cục** (không chỉ là fallback của conversation) nên hủy được cả sau khi đã dự đoán xong. Khi hủy, nếu trận vừa dự đoán chưa có kết quả thì `db.delete_round(last_id)` xóa luôn trận rác (`only_pending=True` → không bao giờ xóa nhầm trận đã ghi KQ).
- Hiển thị tên: mọi tên đi vào tin nhắn Markdown đều qua `display_name()` (đổi `_`→khoảng trắng) — tránh lỗi Telegram "can't find end of the entity" do gạch dưới bị hiểu là dấu in nghiêng.
- `_check_allowed`: nếu có `ALLOWED_CHAT_ID` thì chỉ chat đó được dùng.
- **`/stats`**: tổng quan (tổng trận, % thầy thoát, chờ KQ, 5 trận gần nhất) + **phần "Mô hình & EV"** (`_stats_ev_section`) — CHỈ đọc `tuned_params.json` (tham số + ROI walk-forward) và `model_picks_cache.json` (ROI leave-one-out, báo "mở web tính lại" nếu lỗi thời) + tổng hợp nhanh bội số +EV theo lịch sử (`compute_odds_winrate`/`compute_teacher_by_odds`). Không predict/replay → nhẹ, không đụng race với DB.

## 6. Web UI (web_app.py)

**Sidebar** luôn hiển thị tổng quan (tổng trận, % thầy thoát, số chờ KQ) + **nút 🧹 dọn trận chưa nhập KQ** (`db.delete_pending_rounds()`, có bước xác nhận 2 lần) + hướng dẫn 3 bước.

5 "tab" (thực chất là `st.radio(horizontal=True)` + `if/elif`, KHÔNG dùng `st.tabs` — lý do ở mục 9 "cả 5 tab chạy lại trên MỌI tương tác"; chỉ code của mục đang chọn mới thực thi mỗi rerun):
- **Dự đoán**: nhập tay theo đúng cấu trúc game — **2 con bội THẤP (3–5) + 2 con bội CAO (6–12)** tách 2 nhóm cột. Cả **tên lẫn bội đều dùng `st.segmented_control` (nút 1 chạm, không dropdown)** để nhập nhanh nhất. Tên yêu quái: options đã LỌC SẴN theo nhóm — `LOW_MONSTERS` (9 con cố định, **sắp xếp ABC**: Bach_tuong, Dai_bang_kim_si, Hoang_mi_vuong, Hong_hai_nhi, Lao_ban, Loc_dai_tien, Thanh_nguu, Thanh_su, Xich_vy_ma_hat) cho 2 slot thấp, phần bù `HIGH_MONSTERS` (9 con còn lại, suy ra bằng list-comprehension để không lặp danh sách, cũng **theo ABC** vì lấy từ `sorted(KNOWN_MONSTERS)`) cho 2 slot cao — đỡ dò cả 18 con, hiển thị qua `format_func=display_name` (bỏ gạch dưới) thay vì hằng số thô. Cả tên và bội đều **không cho trùng trong cùng nhóm** bằng chung 1 cơ chế: hàm `_avoid_clash(primary_key, secondary_key, options)` — slot phụ (lo1/hi1) lọc bỏ giá trị slot chính (lo0/hi0) khỏi options trước khi render, còn callback `on_change` gắn ở slot chính tự đẩy slot phụ sang giá trị khác nếu người dùng đổi slot chính trùng slot phụ (tránh crash "value not in options" khi Streamlit rerun) — dùng chung cho cả 4 cặp (tên thấp, bội thấp, tên cao, bội cao). + **bội Thầy** `number_input` (tên Thầy cố định `Duong_tang`, đã bỏ selectbox chọn tên). Đã **bỏ `st.form`**: các query nặng dùng ở đầu trang đã `@st.cache_data` nên rerun mỗi lần chọn không tốn round-trip Turso — không cần chặn rerun bằng form nữa, đổi lại đủ khả năng lọc option động theo lựa chọn của widget khác (form chặn điều này vì widget trong form không rerun tới khi submit). Nút **🆕 Nhập trận mới** xoá các key widget để nhập trận kế nhanh. Trang cũng bỏ `st.title()` ở đầu (chiếm nhiều diện tích, sidebar + `st.header` mỗi tab đã đủ ngữ cảnh). Kết quả render qua hàm `render_prediction(state)` và **lưu trong `st.session_state["pred"]` nên sống qua mọi rerun** (không biến mất khi bấm nút khác). Dẫn đầu bằng **kèo giá trị EV** (ưu tiên value betting) + kèo an toàn; bảng dùng `ProgressColumn`, biểu đồ trong expander. **Nút ghi người thắng nằm ngay dưới kết quả** (không cần đổi tab). Chặn 4 yêu quái trùng tên. **Chống bấm trùng tạo rác**: bấm "Dự đoán ngay" lại với đúng combo (4 yêu quái+bội+thầy) như lần trước và trận đó chưa ghi KQ → tái dùng `round_id` cũ thay vì `save_round` thêm bản ghi "chờ KQ" mới (so khớp qua `sig` lưu trong `st.session_state["pred"]`).
- **Nhập kết quả**: fallback cho trận cũ còn sót (dropdown → radio).
- **Thống kê**: metric + pie + bar + bảng + **Odds Calibration** (≥20 trận) + **Phân tích chiến lược cược (ROI)** (≥15 trận): so ROI các chiến lược (bội nhỏ/to, thầy theo ngưỡng odds) + bảng win-rate/EV theo từng giá trị bội (qua `strategy_analysis.compute_*`), kèm cảnh báo variance. **2 dòng "Theo mô hình"** (LOO + lọc theo ngưỡng EV qua slider): leave-one-out tốn ~chục giây nên kết quả `compute_model_picks()` được **lưu ra đĩa** `model_picks_cache.json` (`save/load_model_picks_cache`, gắn `n_rounds` + tham số tune). Mở web → nếu cache còn khớp thì **LOAD ngay như tuned_params** (không tính lại, kể cả sau khi restart app); chỉ khi thêm trận / đổi tham số (cache lỗi thời) mới hiện nút bấm tính lại 17s rồi ghi đè cache. Kéo slider chỉ re-`aggregate` (tức thì). **Tinh chỉnh mô hình**: nút chạy [tune_shrinkage.py](tune_shrinkage.py) (subprocess, thanh tiến độ) + hiển thị `ind_k/odds_k/name_odds_k` và **ROI/trận** từ `tuned_params.json`.
- **Soi cầu**: thống kê kiểu xổ số cho yêu quái **bội cao (≥9)** + **Thầy**. Yêu quái: `db.get_high_odds_appearances(9)` — 1 query UNION 4 slot trả **mọi lần ra sân ở bội≥9** kèm `won` + `round_id`, `ORDER BY round_id` (thứ tự thời gian). Thầy (**trường hợp đặc biệt**): `db.get_teacher_appearances()` — Thầy có mặt **MỌI trận** và bội ở thang riêng (**14–26**) nên KHÔNG áp bộ lọc bội≥9 lẫn ngưỡng mẫu; đơn vị "đang khan"/"chu kỳ" của Thầy là **số TRẬN** (yêu quái là **số lần ra sân bội≥9**). Cả 2 bọc `@st.cache_data` (`_cached_high_odds_appearances`, `_cached_teacher_appearances`, đã thêm vào `_bust_data_cache`). Công thức chung ở helper `_soi_cau_metrics(apps)` (chuỗi lần xuất hiện đã sắp thời gian → đang khan/chu kỳ/trạng thái). Radio **phạm vi bội** (Gộp 9–12 / Chỉ 9 / Chỉ 10–12) chỉ **lọc yêu quái** ở Python (không đụng Thầy) — KHÔNG tách bảng theo tên×bội (255 trận ÷ 36 ô quá thưa để "chu kỳ về" có nghĩa; luôn gom **theo tên**). Với mỗi nhân vật: **Đang khan** = số lần SAU lần về/thoát gần nhất (chưa lặp lại), **Chu kỳ TB** = Xuất hiện ÷ Về (trung bình bao nhiêu lần thì về 1), **Lần về gần nhất** (ngày). Trạng thái 🔥 quá hạn (khan ≥ chu kỳ) / 🟡 tới hạn (≥70%) / ⚪ bình thường / ❓ chưa từng về. **Thầy luôn đứng đầu bảng** (đơn vị khác nên không trộn thứ hạng); yêu quái (đủ `min_app` lần) sort **khan giảm dần**. `ProgressColumn` cho tỉ lệ về. Có cảnh báo rõ đây là **gambler's fallacy** (game ngẫu nhiên, "quá hạn" không đảm bảo về) — chỉ tham khảo.
- **Lịch sử**: bảng 200 trận gần nhất.

> UI dùng `width="stretch"` (API mới, Streamlit ≥1.40). Test headless bằng `streamlit.testing.v1.AppTest` (chạy script body + mô phỏng click, bắt exception).

## 7. Nhập dữ liệu

- Dữ liệu vào DB **chỉ qua nhập tay** (Web tab "Dự đoán" + `/manual` trên Telegram) rồi ghi kết quả. Chức năng **upload ảnh (OCR)** và **import CSV** đã gỡ bỏ (2026-07-13) — xem mục 9.
- `save_round()` chuẩn hóa tên về canonical tại một điểm vào duy nhất nên mọi nguồn nhập đều thống nhất.

## 8. Chạy hệ thống

```powershell
pip install -r requirements.txt          # lần đầu
python main.py                            # bot + web (local, dùng Turso nếu .env có cấu hình)
python main.py --web                      # chỉ web (http://localhost:8501)
python main.py --bot                      # chỉ bot (chạy trên PC, LUÔN chạy kiểu này kể cả sau khi có Web Cloud)
```
Dừng tiến trình trong terminal: **Ctrl+C**.

**Web UI trên Streamlit Community Cloud** (cập nhật khi push code):
- Deploy lần đầu: share.streamlit.io → New app → chọn repo `chiennguyen4499/dua-thay` → **Main file path = `web_app.py`** → nhập secrets (`TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`) ở Settings → Secrets.
- Cập nhật sau này: chỉ cần `git push` lên `main` — Streamlit Cloud tự redeploy.
- Bot Telegram **không** deploy lên Cloud, luôn chạy trên PC (`python main.py` hoặc `--bot`).

## 9. Quyết định thiết kế & các lỗi đã xử lý (lessons learned)

- **Pattern key chỉ theo tên (bỏ odds)** — bản đầu gồm odds khiến pattern gần như không bao giờ trùng. Đổi sang tên-only để tích lũy nhanh.
- **Windows cp1252**: `print()` có emoji gây `UnicodeEncodeError`. → mọi `print()` trong `main.py`/`tune_shrinkage.py` dùng ASCII (`[OK]`, `->`). Emoji **chỉ** dùng trong message Telegram/Streamlit (UTF-8), không dùng trong stdout.
- **asyncio trong thread**: bot chạy ở daemon thread cần `asyncio.set_event_loop(asyncio.new_event_loop())` đầu `run_bot()` ([main.py:20](main.py#L20)).
- **Telegram Conflict** (`terminated by other getUpdates`): chỉ được chạy **một** instance bot. Nếu lỗi → tắt tiến trình Python thừa.
- **f-string lồng** trong web_app: tách biến slot trước (`slot = r["winner"]`) thay vì lồng quote escape.
- **Gỡ upload ảnh (OCR) & import CSV (2026-07-13)**: phân tích cho thấy 2 chức năng ít dùng và OCR kéo theo `easyocr`/`torch` rất nặng. Đã xóa `ocr_module.py`, `import_csv.py`; gỡ tab "Import CSV" + lựa chọn "Upload ảnh" khỏi `web_app.py`; gỡ luồng `MessageHandler(filters.PHOTO)` + state `CONFIRM_OCR` khỏi `telegram_bot.py`; bỏ `easyocr` khỏi `requirements.txt`. Dữ liệu giờ chỉ vào qua nhập tay. Các hàm DB `clear_all_rounds`/`source_round_id_exists` giữ lại (không còn dùng nhưng vô hại).
- **Replay DB tạm rò vào DB thật**: các hàm replay (`compute_model_picks`, `tune_shrinkage`, `backtest`) tạm gán `db.DATABASE_PATH = tmp` toàn cục rồi `save_round` vào DB tạm. Vì web + bot daemon **dùng chung module `database`**, biến toàn cục này là điểm tranh chấp → từng làm 99 trận `source='sa'` lọt vào DB thật. Cách phòng (đã áp dụng trong `compute_model_picks`): source tag tạm DUY NHẤT (`_REPLAY_SOURCE`), `threading.Lock`, và `finally` luôn `DELETE FROM rounds WHERE source=<tag>` trên DB thật làm lưới an toàn.
- **DATABASE_PATH theo CWD là footgun**: bản cũ `data/rounds.db` tương đối theo thư mục chạy → chạy nhầm chỗ / di chuyển thư mục sẽ tạo DB rỗng mới ("mất hết data"). Đã sửa: neo theo `os.path.dirname(__file__)` ([config.py](config.py)), path tuyệt đối trong `.env` vẫn được tôn trọng.
- **ROI mô hình = leave-one-out, KHÔNG in-sample**: dự đoán mỗi trận bằng tất cả trận khác (bỏ đúng trận đó). In-sample (gồm cả chính nó) bị lookahead → ROI thổi phồng (đo thực: +225% in-sample vs +52% LOO vs +77% walk-forward). LOO không thổi phồng, phản ánh chất lượng mô hình với lượng data hiện tại.
- **Web UI lên Streamlit Cloud + database dùng chung online (2026-07)**: bot Telegram tiếp tục chạy trên PC, Web UI chuyển sang Streamlit Community Cloud (không có ổ đĩa bền) để dùng được từ điện thoại và vẫn ghi được kết quả từ xa. Chọn **Turso (libSQL)** thay vì Supabase/Postgres vì cùng cú pháp SQLite (không phải viết lại câu SQL), và free tier không tự "ngủ"/pause sau vài ngày không dùng (Supabase free tier pause sau 7 ngày — rủi ro thật với app ít traffic).
  - `database.py.get_conn()` phân nhánh: nếu `DATABASE_PATH` **chưa bị đổi** so với lúc import module (`_CONFIGURED_DB_PATH`) và đã cấu hình `TURSO_DATABASE_URL` → nối Turso; ngược lại dùng SQLite local như cũ.
  - Lý do phải phân nhánh (không nối Turso vô điều kiện): `backtest.py`, `tune_shrinkage.py`, `strategy_analysis.compute_model_picks()` đều tạm gán `db.DATABASE_PATH = tempfile.mktemp()` để chạy replay hàng trăm lần **local cho nhanh** rồi trả lại path gốc — 3 file này không cần sửa gì, `get_conn()` tự nhận ra path đã bị tráo tạm và luôn dùng local trong lúc đó.
  - **Bug phát hiện khi test**: `tune_shrinkage.py` gọi `db.get_all_rounds_with_winner()` (đọc lịch sử thật) **trước khi** tráo path tạm, và làm vậy **80 lần** (1 lần/tổ hợp tham số trong lưới quét) — trước khi có Turso, đây là đọc file local rẻ nên không sao, nhưng sau khi có Turso mỗi lần là 1 round-trip mạng → chậm và từng crash vì lỗi mạng thoáng qua (DNS timeout). Đã sửa: lấy lịch sử thật **1 lần duy nhất** trước vòng lặp, truyền vào `eval_combo()` thay vì để hàm tự đọc lại mỗi lần.
  - Response từ Turso client (`libsql`) trả tuple thô (không có tên cột như `sqlite3.Row`) → `database.py` có lớp bọc `_RemoteRow`/`_RemoteCursor`/`_RemoteConn` dựa vào `cursor.description` để tái tạo cách truy cập `row["ten_cot"]` mà 18 hàm sẵn có đang dùng — nhờ vậy không phải sửa SQL nào trong các hàm đó.
  - **Bug: `db.init_db()` chạy lại mỗi rerun Streamlit → mỗi phím gõ = 3 round-trip Turso**: `init_db()` (tạo bảng + 2 index nếu chưa có) được gọi ở top-level `web_app.py`, mà Streamlit chạy lại toàn bộ script mỗi lần người dùng tương tác với widget (kể cả gõ ô nhập tay). Với SQLite local việc này rẻ, nhưng với Turso mỗi lệnh SQL là 1 request mạng → cảm giác "loading lâu" mỗi lần nhập. Đã sửa: bọc bằng `@st.cache_resource` để chỉ chạy 1 lần/phiên server.
  - **Đã sửa — cả các tab chạy lại trên MỌI tương tác**: `st.tabs` của Streamlit chạy code của **TẤT CẢ tab** mỗi rerun, kể cả tab không hiển thị (chỉ ẩn bằng CSS phía client, server vẫn thực thi toàn bộ) — nên chỉ chọn 1 yêu quái ở tab Dự đoán cũng kéo theo toàn bộ code tab Thống kê/Lịch sử chạy lại (bao gồm ~12 round-trip Turso). Bước 1 (cache) chỉ giảm số round-trip chứ chưa giải quyết gốc: code CPU/logic của tab khác vẫn chạy thừa. Bước 2 (sửa dứt điểm): thay `st.tabs` bằng `st.radio(horizontal=True)` lưu tab đang chọn vào biến `active_tab`, rồi chuyển toàn bộ `with tabN:` thành `if/elif active_tab == TAB_LABELS[i]:` — nhờ vậy **chỉ code của tab đang xem mới chạy**, tương tác ở tab nào chỉ tác động tab đó. Vẫn giữ các hàm cache đọc DB ở trên (không hại gì, và vẫn hữu ích khi tương tác nhiều lần trong CÙNG 1 tab, vd. kéo slider ở tab Thống kê). Verify qua preview: chuyển sang tab Thống kê chỉ còn 4 metric (đúng của tab đó) thay vì 8 như trước (khi tab ẩn vẫn âm thầm render).
  - **Đã sửa — N+1 query trong `predictor.predict()`**: khi đủ data (≥10 trận, nhánh `_from_individual`), một lượt dự đoán từng gọi ~19 query tuần tự tới `get_conn()` (mỗi lần một kết nối Turso mới, không pool) vì vòng lặp qua từng nhân vật gọi riêng `get_monster_stats`/`get_monster_name_odds_stats`. Đã gộp thành `get_monster_stats_batch`/`get_monster_name_odds_stats_batch` ([database.py](database.py)) — 1 query `WHERE name IN (...)` cho cả 4 yêu quái thay vì 4 query riêng, dùng ở cả 3 nhánh `_hist_stats_for_chars`/`_from_pattern`/`_from_individual` trong [predictor.py](predictor.py). Thầy (chỉ 1 nhân vật/trận) vẫn dùng `get_teacher_stats`/`get_teacher_name_odds_stats` như cũ vì không có gì để gộp.
  - **Đã sửa — `created_at` lệch múi giờ khi ghi qua Turso**: cột `created_at` dùng default SQL `datetime('now','localtime')` ([database.py](database.py)) — `'localtime'` được **máy chủ chạy câu lệnh đó** tính, không phải máy người dùng. Với SQLite local (bot chạy trên PC VN) thì đúng giờ VN, nhưng khi ghi qua Turso, máy chủ Turso chạy UTC → lệch **-7 tiếng** so với giờ VN thực tế (10h thực tế ghi thành 3h trong DB). Đã sửa: `save_round()` tự tính `created_at` bằng `datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))` trong Python rồi truyền tường minh vào INSERT, không phụ thuộc múi giờ máy chủ DB nữa (đã thêm `tzdata` vào `requirements.txt` để `zoneinfo` chạy được trên môi trường không có sẵn IANA tz database). **Dữ liệu cũ đã lệch giờ (từ lúc chuyển sang Turso) được giữ nguyên theo yêu cầu** — chỉ ảnh hưởng hiển thị `created_at`, không ảnh hưởng thuật toán dự đoán (không dùng `created_at` để tính xác suất).
  - Đã migrate 175 trận từ `data/rounds.db` sang Turso, giữ nguyên `id`/`created_at`; verify khớp 100% cả về `COUNT`/`SUM(id)` lẫn kết quả các hàm thống kê phức tạp (`get_all_competitor_stats`, `get_monster_odds_winrate`,...). File `data/rounds.db` **giữ nguyên, không xóa** — là lưới an toàn rollback.
  - **Rollback KHÔNG đồng nghĩa "khôi phục dữ liệu mới nhất"**: bỏ `TURSO_DATABASE_URL` trong `.env` chỉ khiến bot chuyển sang đọc/ghi `data/rounds.db` — **file này đứng yên từ lúc migrate**, không tự có các trận đã nhập qua Turso/Web Cloud sau đó. Dữ liệu trên Turso **không bị mất/xóa** khi rollback, chỉ là tạm thời không ai đọc/ghi vào đó nữa. Hệ quả: nếu rollback rồi bot ghi trận mới vào local, 2 nguồn (Turso và local) sẽ **phân mảnh** (mỗi bên có những trận riêng không có ở bên kia) — cần merge tay (theo mẫu `migrate_to_turso.py`, đảo chiều nguồn/đích) nếu muốn gộp lại sau khi Turso ổn định trở lại. Rollback chỉ nên coi là biện pháp tạm thời để bot còn dùng được khi Turso/mạng gặp sự cố, không phải thao tác "undo".

## 10. Việc còn lại / hướng mở rộng

- Đặt `ALLOWED_CHAT_ID` trong `.env` (lấy từ @userinfobot).
- Revoke + thay token Telegram (token cũ từng lộ trong log) qua @BotFather.
- Tích lũy thêm dữ liệu để pattern tier kích hoạt nhiều hơn (hiện chủ yếu chạy tier "individual").
- Theo dõi hạn mức free tier Turso (hiện dùng không đáng kể so với hạn mức) và tình trạng project Streamlit Cloud có bị "ngủ" sau 12h không truy cập hay không.
