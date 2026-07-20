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
| [config.py](config.py) | Cấu hình, đọc `.env`; `KNOWN_MONSTERS` (18), `KNOWN_TEACHERS`, `TEACHER_DEFAULT="Duong_tang"`. **`DATABASE_PATH` neo theo `__file__`** (thư mục dự án), không theo CWD → chạy/di chuyển thư mục từ đâu cũng đúng DB. `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` (để trống = chỉ dùng SQLite local) |
| [database.py](database.py) | Schema (bảng `rounds` + `meta`), lưu/đọc trận, `get_meta`/`set_meta` (KV dùng chung PC↔Cloud). `get_conn()` tự chọn SQLite local hay Turso — xem mục 3 và 9 |
| [predictor.py](predictor.py) | Thuật toán dự đoán 2 tầng (shrinkage phân tầng / odds) + format text Telegram |
| [telegram_bot.py](telegram_bot.py) | Bot: nhập tay (`/manual`), `/result`, `/stats` |
| [web_app.py](web_app.py) | Streamlit UI 4 tab |
| [backtest.py](backtest.py) | Walk-forward backtest: đo predictor vs odds-only vs uniform (logloss/brier/top-1) |
| [tune_shrinkage.py](tune_shrinkage.py) | Quét tham số K (ODDS_CALIB / NAME_ODDS / INDIVIDUAL) theo **logloss walk-forward** (từ 2026-07-20, trước đó là ROI — bị winner's curse, xem mục 9); ghi `tuned_params.json` kèm ROI tham khảo + **CI 95% bootstrap** (`_meta.roi_ci95`), `optimized_by="logloss"` |
| [strategy_analysis.py](strategy_analysis.py) | Kiểm chứng heuristic cược (bội to/nhỏ, thầy bội cao) + ROI từng chiến lược; **ROI "theo mô hình" tính leave-one-out** (`compute_model_picks` + `aggregate_model_strategies`) |
| [main.py](main.py) | Entry point chạy bot + web |
| `.env` / [.env.example](.env.example) | Token & cấu hình (Telegram, Turso) |
| `data/rounds.db` | Database SQLite local — dev/backtest/tune/rollback. **Kể từ khi có Turso, không còn là nguồn dữ liệu sống chính** (xem mục 9) |
| `tuned_params.json` | Tham số K đã tune (default offline cho config lúc import). Nguồn dùng chung thật sự là `meta['tuned_params']` trên Turso — 2 nơi đồng bộ lúc khởi động. `model_picks_cache.json` **đã bỏ** (chuyển vào `meta['model_picks']`) |
| [requirements.txt](requirements.txt) | Dependencies |

## 3. Mô hình dữ liệu

Bảng chính `rounds` ([database.py](database.py)) + bảng phụ `meta(key TEXT PK, value TEXT)` (kho key-value JSON dùng chung PC↔Cloud: `tuned_params`, `tuned_at_rounds`, `model_picks` — xem mục 9):

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
→ Chỉ gồm **tên** (đã sort), **không gồm bội số**. Vẫn được lưu vào DB khi `save_round` làm **metadata** (kèm index), nhưng **predictor không còn dùng** — tầng pattern đã bỏ 2026-07-20 (xem mục 9).

## 4. Thuật toán dự đoán (predictor.py)

2 tầng, tự chọn theo lượng dữ liệu. Hàm chính: `predict(monsters, teacher, rounds=None)` → dict gồm `method`, `probabilities`, `recommendation`, `best_value` (EV), `confidence`, `details`, `message`. **`predict` là PURE FUNCTION** (2026-07-20): nhận danh sách trận lịch sử `rounds` (list dict, mỗi dict như 1 row DB); nếu `None` thì tự đọc DB 1 lần. Mọi thống kê tính qua class `HistoryStats` (gộp appeared/won theo tên, theo bội, theo tên×bội trong 1 lượt duyệt Python) — không còn gọi hàm SQL nào trong lúc predict. Web truyền list đã cache; backtest/tune/LOO truyền list đã cắt.

### Bước nền: xác suất implied từ bội số
`_implied_prob`: chuẩn hóa `1/odds` trên toàn bộ 5 nhân vật → `q[name]`. Bội số thấp → q cao.

### Tầng 1 — Thống kê cá nhân + calibration theo BỘI SỐ (`method="individual"`)
Điều kiện: tổng trận có kết quả trong DB ≥ 10. **Shrinkage Beta-Binomial PHÂN TẦNG**, từ thô đến tinh (mỗi tầng là prior cho tầng kế, kéo về nhau theo số mẫu — hàm `_shrink`):

```
1/odds (q)
  └─► win-rate GỘP theo giá trị bội   (K = ODDS_CALIB_STRENGTH)
        └─► win-rate theo TÊN nhân vật (K = INDIVIDUAL_PRIOR_STRENGTH)
              └─► win-rate theo (TÊN × bội) (K = NAME_ODDS_STRENGTH)
```

> Giá trị K mặc định trong [config.py](config.py) (25 / 3 / 0) bị `tuned_params.json`
> ghi đè nếu có. Tune theo **logloss walk-forward** (đổi từ ROI 2026-07-20, xem
> mục 9) — chạy `python tune_shrinkage.py` hoặc nút trên web sau khi thêm data.

- **Tầng giá trị bội** (mới) gộp mọi tên cùng một giá trị bội → đủ mẫu, ổn định. Đây là chỗ bắt trực giác "bội X hay/không hay về": vd bội 5 thắng ~38%, bội 10 thắng 0/21, **thầy bội ≥18 hay thoát** (1/odds không thấy được điều này). Bảng lấy từ `get_monster_odds_winrate` / `get_teacher_odds_winrate`.
- **Tầng tên** kéo về tầng giá trị bội (thay vì kéo thẳng về odds như bản cũ): nhân vật ít mẫu sẽ mượn calibration của bội số nó đang mang.
- **Tầng (tên × bội)**: mặc định TẮT (`NAME_ODDS_STRENGTH=0`) — ô (tên×bội) chỉ ~2–4 mẫu nên bật vào làm logloss tệ hơn. Vẫn nằm trong lưới quét ("auto-ready"): khi data đủ dày để tầng này cải thiện logloss, tuner tự bật. (Giai đoạn tune-theo-ROI 2026-06→07 nó từng bị bật `NAME_ODDS=10` — hoá ra là overfit theo nhiễu ROI, xem mục 9.)
- Đã xác nhận qua [backtest.py](backtest.py): tầng calibration theo bội giảm logloss so với bản chỉ-theo-tên và tốt hơn odds-only. Quét K bằng [tune_shrinkage.py](tune_shrinkage.py).
- Hiển thị: cột **"Bội này về"** (web) và "· bội này về X%" (Telegram) cho thấy tỷ lệ thắng thực tế của giá trị bội đó.

> **Tiêu chí tune = logloss walk-forward** ([tune_shrinkage.py](tune_shrinkage.py)):
> chọn bộ K có logloss thấp nhất khi mỗi trận chỉ học từ các trận trước. ROI
> (cược 1 đơn vị/trận vào con EV dự đoán cao nhất) vẫn được tính và ghi vào
> `_meta.roi_ev` kèm **CI 95% bootstrap** (`_meta.roi_ci95`) nhưng CHỈ để tham
> khảo. Lịch sử: 2026-06 đổi logloss→ROI với lý do "logloss không đo lợi nhuận";
> 2026-07-20 đổi ngược lại vì phát hiện winner's curse (mục 9).

### Tầng 2 — Chỉ odds (`method="multiplier"`)
Khi DB < 10 trận: dùng thẳng `q` (xác suất implied từ bội số).

### Đầu ra phụ
- **Recommendation** = argmax xác suất.
- **Best value (EV)** = argmax `prob × odds`. Kèm **khoảng bất định + mức cược gợi ý**: `_wilson_ci(p, n)` cho khoảng khả dĩ của xác suất (n = số lần con đó xuất hiện → mẫu ít thì khoảng rộng) → suy ra `ev_low/high`; `_kelly_fraction(p, mult)` = **¼-Kelly** (¼·(EV−1)/(bội−1)) chặn trần 5% vốn, =0 khi không có lợi thế. Hiển thị ở web + Telegram.
- **Confidence** (`cao`/`trung binh`/`thap`): theo số mẫu của **tầng bội** — mức tin cậy do nhân vật mỏng mẫu nhất quyết định (thường là thầy: mỗi giá trị bội thầy chỉ ~10–30 trận). `trung binh` khi min(odds_appeared) ≥ 15 và tổng ≥ 100 trận; `cao` khi ≥ 50 và ≥ 300 (chưa đạt được với data hiện tại — chủ đích, nhãn phải trung thực).
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
- **Dự đoán**: nhập tay theo đúng cấu trúc game — **2 con bội THẤP (3–5) + 2 con bội CAO (6–12)** tách 2 nhóm cột. **Tên yêu quái** dùng lưới nút `st.button` qua helper `_name_selector(label, options, key, sibling_key)` — số nút LUÔN cố định (9 con/nhóm, `LOW_MONSTERS`/`HIGH_MONSTERS` xếp ABC), con đã chọn ở slot cùng nhóm bị **`disabled`** (mờ) thay vì bị ẩn → **không nhảy layout** (bug cũ: lọc bớt option làm số nút đổi, layout giật). Đây là lý do KHÔNG dùng `st.segmented_control` cho tên: Streamlit không cho disable từng option của segmented_control. Nút chọn được lưu ở `st.session_state[key]`; 2 slot cùng nhóm disable giá trị của nhau nên không bao giờ trùng tên (bỏ hẳn `_avoid_clash` cũ). **Bội** vẫn dùng `st.segmented_control` (cho phép trùng). **Bội Thầy** segmented_control (14–26), tên Thầy cố định `Duong_tang`. Nút **🆕 Nhập trận mới** xoá key widget. Kết quả render qua `render_prediction(state)`, lưu trong `st.session_state["pred"]` (sống qua rerun). Dẫn đầu bằng **kèo giá trị EV** kèm **khoảng khả dĩ (Wilson CI) cho xác suất & EV** + **mức cược gợi ý ¼-Kelly (trần 5% vốn)** (xem mục 4 "Đầu ra phụ") + kèo an toàn. **Nút ghi người thắng nằm ngay dưới kết quả**. **Chống bấm trùng tạo rác** qua `sig`. Đầu tab có expander **"📌 Ghi nhớ: kèo heuristic theo bội"** — TỰ TÍNH từ data sống qua `strategy_analysis.compute_heuristic_summary()` (ROI + bootstrap CI mỗi giá trị bội, xếp theo cận dưới CI; đánh dấu kèo CI>0 là "đáng tin") thay cho ghi chú viết tay hay lỗi thời.
- **Nhập kết quả**: fallback cho trận cũ còn sót (dropdown → radio).
- **Thống kê**: metric + pie + bar + bảng + **Odds Calibration** (≥20 trận) + **Phân tích chiến lược cược (ROI)** (≥15 trận): so ROI các chiến lược + bảng win-rate/EV theo từng giá trị bội, kèm cảnh báo variance. **2 dòng "Theo mô hình"** (LOO + lọc ngưỡng EV qua slider): sau khi predictor thành pure function, `compute_model_picks()` chỉ **~1.2s** nên TỰ TÍNH luôn (không còn nút bấm). Cache 2 tầng: **bảng `meta` trên Turso** (`save/load_model_picks_cache` giờ đọc/ghi `meta['model_picks']` — dùng chung PC↔Cloud, khớp `n_rounds`+tham số thì load ngay kể cả từ máy khác/sau restart) + `@st.cache_data` trong phiên. Kéo slider chỉ re-`aggregate`. **Tinh chỉnh mô hình**: nút chạy [tune_shrinkage.py](tune_shrinkage.py) (subprocess, qua helper `_run_tune_with_progress`) + **AUTO-RETUNE tại mốc 50 trận** — khi tổng trận ≥ (mốc tune gần nhất `meta['tuned_at_rounds']` + 50) thì tự chạy tinh chỉnh 1 lần (banner + chống chạy chồng bằng cờ phiên). Hiển thị `ind_k/odds_k/name_odds_k`, logloss (tiêu chí chọn), **ROI/trận kèm CI 95%** (tham khảo); cảnh báo nếu `optimized_by` cũ.
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

- **Bỏ tầng pattern + đổi tiêu chí tune ROI→logloss (review thống kê 2026-07-20, trên 279 trận):**
  - **Tune theo ROI bị winner's curse**: ROI mỗi cược có độ lệch chuẩn ~3 đơn vị (thắng bội 9 = +8, thua = −1) → sai số chuẩn của ROI trên ~270 trận là ~±0.2, LỚN HƠN chênh lệch ROI giữa 80 combo trong lưới quét (sd ~0.11). Chọn max ROI = chọn nhiễu. Bằng chứng: bộ (40/2/10) chọn theo ROI cho logloss walk-forward **1.4707, tệ hơn cả odds-only (1.4552)**; mọi combo `name_odds=0` đều tốt hơn (~1.442–1.444). Con số "ROI +54.6%" từng hiển thị là ước lượng thổi phồng (bias chọn lọc + không có CI). → Đổi tiêu chí về **logloss** (ổn định, đo calibration trực tiếp); ROI chỉ report kèm **CI 95% bootstrap**.
  - **Tầng pattern vô dụng cả lý thuyết lẫn thực nghiệm**: 199 pattern key khác nhau / 279 trận, walk-forward chỉ kích hoạt 6/269 lần; tắt đi logloss không đổi, ROI còn tăng (+0.47→+0.54). Với cấu trúc 2 thấp + 2 cao có C(9,2)²=1296 combo khả dĩ → pattern ≥3 mẫu mãi mãi hiếm, và không có cơ chế game nào khiến tổ hợp TÊN (bỏ qua bội) mang tín hiệu vượt trên từng con + bội. → Xoá tier khỏi predictor (`pattern_key` vẫn lưu DB làm metadata); rule confidence đổi từ "pattern n≥5 = cao" (chỗ kém tin cậy nhất lại nhãn cao nhất) sang theo số mẫu tầng bội.
  - **Tín hiệu thật nằm ở tầng BỘI**: bội 5 về 36% vs implied 20% (z=5.3); bội 9 về 21% vs 11% (z=2.95); thầy 14–17 **0/76 thoát**, toàn bộ 16 lần thoát đều ở bội ≥18. Overround trung bình 0.81 (<1): paytable trả hào phóng hơn 1/odds, bù bằng thầy-bội-thấp không bao giờ thoát. Tín hiệu theo TÊN đa phần ăn ké hiệu ứng bội (các con z~2.5 đều hay mang bội 5; 18 tên = multiple comparisons).
  - **Dọn 3 trận dữ liệu lỗi** (id 328: 4 con cùng bội 5; id 163: sai cấu trúc; id 67: double-submit trùng id 66 cùng giây) → còn 276 trận sạch.
- **Pattern key chỉ theo tên (bỏ odds)** — bản đầu gồm odds khiến pattern gần như không bao giờ trùng. Đổi sang tên-only để tích lũy nhanh. (Từ 2026-07-20 chỉ còn là metadata — xem mục trên.)
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
  - Lý do lịch sử của việc phân nhánh: xưa `backtest.py`/`tune_shrinkage.py`/`strategy_analysis.compute_model_picks()` tạm gán `db.DATABASE_PATH = tempfile.mktemp()` để replay local cho nhanh, `get_conn()` cần nhận ra path bị tráo để dùng local. **Từ 2026-07-20, 3 file này không còn tráo path nữa** (predictor thành pure function, replay chạy thuần Python trên list — xem mục 10). Guard `_CONFIGURED_DB_PATH` giữ lại vô hại (giờ `DATABASE_PATH` luôn == `_CONFIGURED_DB_PATH` nên `_is_remote()` chỉ còn kiểm tra `TURSO_DATABASE_URL`).
  - **Bug phát hiện khi test**: `tune_shrinkage.py` gọi `db.get_all_rounds_with_winner()` (đọc lịch sử thật) **trước khi** tráo path tạm, và làm vậy **80 lần** (1 lần/tổ hợp tham số trong lưới quét) — trước khi có Turso, đây là đọc file local rẻ nên không sao, nhưng sau khi có Turso mỗi lần là 1 round-trip mạng → chậm và từng crash vì lỗi mạng thoáng qua (DNS timeout). Đã sửa: lấy lịch sử thật **1 lần duy nhất** trước vòng lặp, truyền vào `eval_combo()` thay vì để hàm tự đọc lại mỗi lần.
  - Response từ Turso client (`libsql`) trả tuple thô (không có tên cột như `sqlite3.Row`) → `database.py` có lớp bọc `_RemoteRow`/`_RemoteCursor`/`_RemoteConn` dựa vào `cursor.description` để tái tạo cách truy cập `row["ten_cot"]` mà 18 hàm sẵn có đang dùng — nhờ vậy không phải sửa SQL nào trong các hàm đó.
  - **Bug: `db.init_db()` chạy lại mỗi rerun Streamlit → mỗi phím gõ = 3 round-trip Turso**: `init_db()` (tạo bảng + 2 index nếu chưa có) được gọi ở top-level `web_app.py`, mà Streamlit chạy lại toàn bộ script mỗi lần người dùng tương tác với widget (kể cả gõ ô nhập tay). Với SQLite local việc này rẻ, nhưng với Turso mỗi lệnh SQL là 1 request mạng → cảm giác "loading lâu" mỗi lần nhập. Đã sửa: bọc bằng `@st.cache_resource` để chỉ chạy 1 lần/phiên server.
  - **Đã sửa — cả các tab chạy lại trên MỌI tương tác**: `st.tabs` của Streamlit chạy code của **TẤT CẢ tab** mỗi rerun, kể cả tab không hiển thị (chỉ ẩn bằng CSS phía client, server vẫn thực thi toàn bộ) — nên chỉ chọn 1 yêu quái ở tab Dự đoán cũng kéo theo toàn bộ code tab Thống kê/Lịch sử chạy lại (bao gồm ~12 round-trip Turso). Bước 1 (cache) chỉ giảm số round-trip chứ chưa giải quyết gốc: code CPU/logic của tab khác vẫn chạy thừa. Bước 2 (sửa dứt điểm): thay `st.tabs` bằng `st.radio(horizontal=True)` lưu tab đang chọn vào biến `active_tab`, rồi chuyển toàn bộ `with tabN:` thành `if/elif active_tab == TAB_LABELS[i]:` — nhờ vậy **chỉ code của tab đang xem mới chạy**, tương tác ở tab nào chỉ tác động tab đó. Vẫn giữ các hàm cache đọc DB ở trên (không hại gì, và vẫn hữu ích khi tương tác nhiều lần trong CÙNG 1 tab, vd. kéo slider ở tab Thống kê). Verify qua preview: chuyển sang tab Thống kê chỉ còn 4 metric (đúng của tab đó) thay vì 8 như trước (khi tab ẩn vẫn âm thầm render).
  - **N+1 query trong `predictor.predict()` (lịch sử — nay đã vượt qua bằng pure-function refactor)**: bản đầu, mỗi lượt dự đoán gọi ~19 query tuần tự tới `get_conn()`; bước 1 gộp thành các hàm `*_batch` (1 query `WHERE name IN (...)`); bước 2 (2026-07-20) bỏ hẳn mọi query trong predict — `HistoryStats` tính tất cả từ list trận đã load 1 lần, nên predict giờ **0 round-trip Turso** khi web truyền list cache. Các hàm `*_batch` đó đã bị xoá vì không còn ai dùng.
  - **Đã sửa — `created_at` lệch múi giờ khi ghi qua Turso**: cột `created_at` dùng default SQL `datetime('now','localtime')` ([database.py](database.py)) — `'localtime'` được **máy chủ chạy câu lệnh đó** tính, không phải máy người dùng. Với SQLite local (bot chạy trên PC VN) thì đúng giờ VN, nhưng khi ghi qua Turso, máy chủ Turso chạy UTC → lệch **-7 tiếng** so với giờ VN thực tế (10h thực tế ghi thành 3h trong DB). Đã sửa: `save_round()` tự tính `created_at` bằng `datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))` trong Python rồi truyền tường minh vào INSERT, không phụ thuộc múi giờ máy chủ DB nữa (đã thêm `tzdata` vào `requirements.txt` để `zoneinfo` chạy được trên môi trường không có sẵn IANA tz database). **Dữ liệu cũ đã lệch giờ (từ lúc chuyển sang Turso) được giữ nguyên theo yêu cầu** — chỉ ảnh hưởng hiển thị `created_at`, không ảnh hưởng thuật toán dự đoán (không dùng `created_at` để tính xác suất).
  - Đã migrate 175 trận từ `data/rounds.db` sang Turso, giữ nguyên `id`/`created_at`; verify khớp 100% cả về `COUNT`/`SUM(id)` lẫn kết quả các hàm thống kê phức tạp (`get_all_competitor_stats`, `get_monster_odds_winrate`,...). File `data/rounds.db` **giữ nguyên, không xóa** — là lưới an toàn rollback.
  - **Rollback KHÔNG đồng nghĩa "khôi phục dữ liệu mới nhất"**: bỏ `TURSO_DATABASE_URL` trong `.env` chỉ khiến bot chuyển sang đọc/ghi `data/rounds.db` — **file này đứng yên từ lúc migrate**, không tự có các trận đã nhập qua Turso/Web Cloud sau đó. Dữ liệu trên Turso **không bị mất/xóa** khi rollback, chỉ là tạm thời không ai đọc/ghi vào đó nữa. Hệ quả: nếu rollback rồi bot ghi trận mới vào local, 2 nguồn (Turso và local) sẽ **phân mảnh** (mỗi bên có những trận riêng không có ở bên kia) — cần merge tay (theo mẫu `migrate_to_turso.py`, đảo chiều nguồn/đích) nếu muốn gộp lại sau khi Turso ổn định trở lại. Rollback chỉ nên coi là biện pháp tạm thời để bot còn dùng được khi Turso/mạng gặp sự cố, không phải thao tác "undo".

## 10. Việc còn lại / hướng mở rộng

(Đã xong 2026-07-20: revoke token Telegram, đặt `ALLOWED_CHAT_ID`, tune theo logloss, bỏ pattern tier.)

- ✅ **Refactor "load 1 lần, predictor pure function" (XONG 2026-07-20)**: `predict(monsters, teacher, rounds)` giờ là pure function — class `HistoryStats` gộp mọi thống kê từ list trận trong 1 lượt duyệt Python; backtest/tune/LOO chỉ **cắt list** (`real[:i]`) truyền vào, không đụng DB. Đã xoá 6 hàm SQL UNION dành cho predictor (`get_monster_stats_batch`, `get_teacher_stats`, `get_monster_odds_winrate`, `get_teacher_odds_winrate`, `get_monster_name_odds_stats_batch`, `get_teacher_name_odds_stats`) + `clear_rounds_by_source` + toàn bộ cơ chế chống-rò (`_REPLAY_SOURCE`/`_REPLAY_LOCK`/safety-net). **Hết hẳn hazard replay-leak.** LOO 17s→1.2s, tune grid ~13ph→~1ph. Verify behavior-preserving: backtest logloss giữ đúng 1.4378, LOO ROI +53%. Web truyền list đã cache (`_cached_pred_rounds`) → predict 0 round-trip Turso; bot để `rounds=None` (tự load 1 lần).
- ✅ **Bảng `meta` key-value trên Turso + auto-retune (XONG 2026-07-20)**: `meta(key,value)` (`db.get_meta`/`set_meta`, value JSON) dùng chung PC↔Cloud. `model_picks_cache.json` (file) đã **bỏ hẳn** — LOO ~1.2s nên tính lại rẻ, cache ở `meta['model_picks']`. `tuned_params` đồng bộ qua `meta['tuned_params']`: tune ghi cả file local (default offline cho config) lẫn meta; Web (`_sync_tuned_params_once`) + bot (`main._sync_tuned_params_from_meta`) kéo meta→file+reload lúc khởi động → 2 nơi luôn cùng bộ tham số, không cần redeploy. **Auto-retune tại mốc 50 trận**: tổng trận ≥ `meta['tuned_at_rounds']`+50 → web tự chạy tune 1 lần. (config vẫn đọc FILE local lúc import — KHÔNG đọc Turso lúc import — để `importlib.reload` trong tune không bắn round-trip mạng.)
- ✅ **Kelly + khoảng bất định (XONG 2026-07-20)**: `predict` trả trong `best_value` thêm `stake_fraction` (¼-Kelly = ¼·(EV−1)/(bội−1), trần 5% vốn) + `prob_low/high`, `ev_low/high` (Wilson CI theo số lần con đó xuất hiện). Web + Telegram hiển thị "mức cược gợi ý ~X% vốn" và "khoảng khả dĩ EV a–b · thắng c–d%" cho kèo giá trị. Mẫu ít → khoảng rộng (thành thật).
- Theo dõi hạn mức free tier Turso (hiện dùng không đáng kể so với hạn mức) và tình trạng project Streamlit Cloud có bị "ngủ" sau 12h không truy cập hay không.
- Theo dõi edge bội 5 (đang suy giảm: nửa đầu data ROI +125%, nửa sau +42%) — nếu game đổi paytable/cân bằng lại, các tín hiệu theo bội sẽ dịch chuyển.
