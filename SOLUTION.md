# Tài liệu Giải pháp & Kiến trúc — Đua Thầy Predictor

> Mô tả **hệ thống đang giải quyết bài toán như thế nào**. Yêu cầu gốc ở
> [REQUIREMENTS.md](REQUIREMENTS.md). Tài liệu này đủ để nạp context khi mở session mới.

---

## 1. Tổng quan kiến trúc

```
                         ┌──────────────┐
   Ảnh / Nhập tay  ───►  │ ocr_module   │ (EasyOCR, tùy chọn)
                         └──────┬───────┘
                                ▼
  Telegram bot  ─┐      ┌──────────────┐      ┌──────────────┐
                 ├────► │  predictor   │ ───► │   Kết quả     │
  Web UI (Streamlit) ─┘ │ (3 tầng blend)│     │ %, EV, gợi ý  │
                        └──────┬───────┘      └──────────────┘
                               ▼
                        ┌──────────────┐
                        │  database    │ SQLite: data/rounds.db
                        │ (bảng rounds)│
                        └──────────────┘
```

Tất cả module dùng chung `database.py` và `predictor.py`. `main.py` chạy bot
(daemon thread) + Web UI (main thread) đồng thời.

## 2. Danh sách file

| File | Vai trò |
|------|---------|
| [config.py](config.py) | Cấu hình, đọc `.env`; `KNOWN_MONSTERS` (18), `KNOWN_TEACHERS`, `TEACHER_DEFAULT="Duong_tang"`, `MIN_SAMPLES_FOR_PATTERN=3`. **`DATABASE_PATH` neo theo `__file__`** (thư mục dự án), không theo CWD → chạy/di chuyển thư mục từ đâu cũng đúng DB |
| [database.py](database.py) | SQLite: schema, lưu/đọc trận, thống kê, pattern key |
| [predictor.py](predictor.py) | Thuật toán dự đoán 3 tầng + format text Telegram |
| [ocr_module.py](ocr_module.py) | EasyOCR đọc ảnh → (tên, bội số); fuzzy match với known names |
| [telegram_bot.py](telegram_bot.py) | Bot: luồng ảnh, nhập tay, `/result`, `/stats` |
| [web_app.py](web_app.py) | Streamlit UI 5 tab |
| [import_csv.py](import_csv.py) | Import CSV lịch sử (CLI + dùng lại trong web) |
| [backtest.py](backtest.py) | Walk-forward backtest: đo predictor vs odds-only vs uniform (logloss/brier/top-1) |
| [tune_shrinkage.py](tune_shrinkage.py) | Quét tham số K (ODDS_CALIB / NAME_ODDS / INDIVIDUAL) theo **ROI walk-forward (EV tối đa)** để retune khi có thêm dữ liệu; ghi `tuned_params.json` kèm `optimized_by="roi_ev"` |
| [strategy_analysis.py](strategy_analysis.py) | Kiểm chứng heuristic cược (bội to/nhỏ, thầy bội cao) + ROI từng chiến lược; **ROI "theo mô hình" tính leave-one-out** (`compute_model_picks` + `aggregate_model_strategies`) |
| [main.py](main.py) | Entry point chạy bot + web |
| `.env` / [.env.example](.env.example) | Token & cấu hình |
| `data/rounds.db` | Database SQLite (tự tạo) |
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
source                TEXT          -- 'manual'|'web'|'csv_import'|'telegram_ocr'|'telegram_manual'
notes                 TEXT          -- vd 'original_id:62' để chống import trùng
```

**Quy ước quan trọng:**
- `winner` lưu **slot** (`monster1`...), không lưu tên — vì cùng tên có thể ở slot khác nhau giữa các trận.
- Mọi query thống kê đều có `WHERE winner IS NOT NULL` → trận chưa có kết quả **không** ảnh hưởng thống kê/dự đoán. Bỏ ngang an toàn.
- **Chuẩn hóa tên**: `save_round` và `predict` đều gọi `config.canonical_name()` → mọi nguồn (web/telegram tay/OCR/CSV) quy về cùng tên canonical, tránh phân mảnh ("Bach nhan quan" = "bach_nhan_quan" = `Bach_nhan_quan`).
- **Đồng thời**: `get_conn` bật `PRAGMA journal_mode=WAL` + `busy_timeout=5000` để bot (thread) và web (process riêng) không bị "database is locked".

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

- **Ảnh**: `MessageHandler(filters.PHOTO)` → tải ảnh → `extract_game_info` (OCR) → hiển thị + nút inline "Đúng rồi / Nhập lại" (`ConversationHandler` state `CONFIRM_OCR`) → dự đoán.
- **Nhập tay** `/manual`: 2 state — `SELECT_MONSTERS` (inline keyboard chọn 4 yêu quái từ `KNOWN_MONSTERS`, bấm toggle, đánh số 1️⃣–4️⃣, nút ✅ Xong chỉ hiện khi đủ 4) → `ENTER_MULTS` (nhập **5 bội số trên 1 dòng**: 4 yêu quái theo thứ tự + Thầy, vd `3 4 10 12 20`). Thầy mặc định `TEACHER_DEFAULT`. Chọn từ danh sách nên tên luôn canonical, không tạo biến thể mới.
- Sau dự đoán: lưu trận (`winner=NULL`), nhớ `last_round_id`, hiện nút inline chọn người thắng (`result|<id>|<slot>`).
- **Ghi kết quả**: nút inline (`result_callback`) hoặc `/result <tên>` (`result_command`, map tên→slot, "thầy/sư phụ/teacher" → `teacher`).
- **`/cancel`**: đăng ký **toàn cục** (không chỉ là fallback của conversation) nên hủy được cả sau khi đã dự đoán xong. Khi hủy, nếu trận vừa dự đoán chưa có kết quả thì `db.delete_round(last_id)` xóa luôn trận rác (`only_pending=True` → không bao giờ xóa nhầm trận đã ghi KQ).
- Hiển thị tên: mọi tên đi vào tin nhắn Markdown đều qua `display_name()` (đổi `_`→khoảng trắng) — tránh lỗi Telegram "can't find end of the entity" do gạch dưới bị hiểu là dấu in nghiêng.
- `_check_allowed`: nếu có `ALLOWED_CHAT_ID` thì chỉ chat đó được dùng.
- **`/stats`**: tổng quan (tổng trận, % thầy thoát, chờ KQ, 5 trận gần nhất) + **phần "Mô hình & EV"** (`_stats_ev_section`) — CHỈ đọc `tuned_params.json` (tham số + ROI walk-forward) và `model_picks_cache.json` (ROI leave-one-out, báo "mở web tính lại" nếu lỗi thời) + tổng hợp nhanh bội số +EV theo lịch sử (`compute_odds_winrate`/`compute_teacher_by_odds`). Không predict/replay → nhẹ, không đụng race với DB.

## 6. Web UI (web_app.py)

**Sidebar** luôn hiển thị tổng quan (tổng trận, % thầy thoát, số chờ KQ) + **nút 🧹 dọn trận chưa nhập KQ** (`db.delete_pending_rounds()`, có bước xác nhận 2 lần) + hướng dẫn 3 bước.

5 tab:
- **Dự đoán**: nhập tay (selectbox KNOWN_MONSTERS hoặc tên khác, bội số số nguyên) hoặc upload ảnh OCR. Kết quả render qua hàm `render_prediction(state)` và **lưu trong `st.session_state["pred"]` nên sống qua mọi rerun** (không biến mất khi bấm nút khác). Dẫn đầu bằng **kèo giá trị EV** (ưu tiên value betting) + kèo an toàn; bảng dùng `ProgressColumn`, biểu đồ trong expander. **Nút ghi người thắng nằm ngay dưới kết quả** (không cần đổi tab). Chặn 4 yêu quái trùng tên.
- **Nhập kết quả**: fallback cho trận cũ còn sót (dropdown → radio).
- **Thống kê**: metric + pie + bar + bảng + **Odds Calibration** (≥20 trận) + **Phân tích chiến lược cược (ROI)** (≥15 trận): so ROI các chiến lược (bội nhỏ/to, thầy theo ngưỡng odds) + bảng win-rate/EV theo từng giá trị bội (qua `strategy_analysis.compute_*`), kèm cảnh báo variance. **2 dòng "Theo mô hình"** (LOO + lọc theo ngưỡng EV qua slider): leave-one-out tốn ~chục giây nên kết quả `compute_model_picks()` được **lưu ra đĩa** `model_picks_cache.json` (`save/load_model_picks_cache`, gắn `n_rounds` + tham số tune). Mở web → nếu cache còn khớp thì **LOAD ngay như tuned_params** (không tính lại, kể cả sau khi restart app); chỉ khi thêm trận / đổi tham số (cache lỗi thời) mới hiện nút bấm tính lại 17s rồi ghi đè cache. Kéo slider chỉ re-`aggregate` (tức thì). **Tinh chỉnh mô hình**: nút chạy [tune_shrinkage.py](tune_shrinkage.py) (subprocess, thanh tiến độ) + hiển thị `ind_k/odds_k/name_odds_k` và **ROI/trận** từ `tuned_params.json`.
- **Lịch sử**: bảng 200 trận gần nhất.
- **Import CSV**: upload + tùy chọn xóa DB / bỏ qua trùng.

> UI dùng `width="stretch"` (API mới, Streamlit ≥1.40). Test headless bằng `streamlit.testing.v1.AppTest` (chạy script body + mô phỏng click, bắt exception).

## 7. Import dữ liệu (import_csv.py)

- CSV format: `Round_id, Competitor, Odds, Is_winner`. 5 dòng/trận (4 yêu quái + `Duong_tang`), `Is_winner=1` là người thắng.
- Nhóm theo `Round_id`, tách thầy ra khỏi monsters, xác định `winner_slot`, lưu kèm `notes="original_id:<rid>"`.
- Chống trùng: `source_round_id_exists(rid, "csv_import")` ([database.py:243](database.py#L243)).
- CLI: `python import_csv.py path.csv [--clear] [--quiet]`. Cùng hàm `parse_rounds`/`import_rounds` được web tab 5 tái sử dụng.

## 8. Chạy hệ thống

```powershell
pip install -r requirements.txt          # lần đầu (easyocr tùy chọn, nặng)
python import_csv.py Duathay.csv          # nạp lịch sử (1 lần)
python main.py                            # bot + web
python main.py --web                      # chỉ web (http://localhost:8501)
python main.py --bot                      # chỉ bot
```
Dừng tiến trình trong terminal: **Ctrl+C**.

## 9. Quyết định thiết kế & các lỗi đã xử lý (lessons learned)

- **Pattern key chỉ theo tên (bỏ odds)** — bản đầu gồm odds khiến pattern gần như không bao giờ trùng. Đổi sang tên-only để tích lũy nhanh.
- **Windows cp1252**: `print()` có emoji gây `UnicodeEncodeError`. → mọi `print()` trong `main.py`/`import_csv.py` dùng ASCII (`[OK]`, `->`). Emoji **chỉ** dùng trong message Telegram/Streamlit (UTF-8), không dùng trong stdout.
- **asyncio trong thread**: bot chạy ở daemon thread cần `asyncio.set_event_loop(asyncio.new_event_loop())` đầu `run_bot()` ([main.py:20](main.py#L20)).
- **Telegram Conflict** (`terminated by other getUpdates`): chỉ được chạy **một** instance bot. Nếu lỗi → tắt tiến trình Python thừa.
- **f-string lồng** trong web_app: tách biến slot trước (`slot = r["winner"]`) thay vì lồng quote escape.
- **per_message trong ConversationHandler**: KHÔNG đặt `per_message=True` cho OCR handler (gây warning). Giữ mặc định.
- **Replay DB tạm rò vào DB thật**: các hàm replay (`compute_model_picks`, `tune_shrinkage`, `backtest`) tạm gán `db.DATABASE_PATH = tmp` toàn cục rồi `save_round` vào DB tạm. Vì web + bot daemon **dùng chung module `database`**, biến toàn cục này là điểm tranh chấp → từng làm 99 trận `source='sa'` lọt vào DB thật. Cách phòng (đã áp dụng trong `compute_model_picks`): source tag tạm DUY NHẤT (`_REPLAY_SOURCE`), `threading.Lock`, và `finally` luôn `DELETE FROM rounds WHERE source=<tag>` trên DB thật làm lưới an toàn.
- **DATABASE_PATH theo CWD là footgun**: bản cũ `data/rounds.db` tương đối theo thư mục chạy → chạy nhầm chỗ / di chuyển thư mục sẽ tạo DB rỗng mới ("mất hết data"). Đã sửa: neo theo `os.path.dirname(__file__)` ([config.py](config.py)), path tuyệt đối trong `.env` vẫn được tôn trọng.
- **ROI mô hình = leave-one-out, KHÔNG in-sample**: dự đoán mỗi trận bằng tất cả trận khác (bỏ đúng trận đó). In-sample (gồm cả chính nó) bị lookahead → ROI thổi phồng (đo thực: +225% in-sample vs +52% LOO vs +77% walk-forward). LOO không thổi phồng, phản ánh chất lượng mô hình với lượng data hiện tại.

## 10. Việc còn lại / hướng mở rộng

- Đặt `ALLOWED_CHAT_ID` trong `.env` (lấy từ @userinfobot).
- Revoke + thay token Telegram (token cũ từng lộ trong log) qua @BotFather.
- Test OCR với ảnh game thật; heuristic tách thầy trong `extract_game_info` còn đơn giản (lấy pair thứ 5) — có thể cần tinh chỉnh theo layout thật.
- Tích lũy thêm dữ liệu để pattern tier kích hoạt nhiều hơn (hiện chủ yếu chạy tier "individual"; ~157 trận tính đến 2026-06).
