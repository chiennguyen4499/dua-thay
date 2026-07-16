"""
Streamlit Web UI — Sư Phụ Chạy Mau Predictor
Chạy: streamlit run web_app.py
"""

import streamlit as st
import os

# Trên Streamlit Community Cloud, secrets khai báo ở Settings > Secrets nằm
# trong st.secrets, không tự thành biến môi trường. Đẩy sang os.environ TRƯỚC
# khi import config/database (2 module đó đọc env bằng os.getenv lúc import).
try:
    for _k in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        if _k in st.secrets and not os.getenv(_k):
            os.environ[_k] = st.secrets[_k]
except Exception:
    pass  # Chạy local không có secrets.toml -> bỏ qua, dùng .env như thường

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

import database as db
import predictor as pred
from config import KNOWN_MONSTERS, KNOWN_TEACHERS, display_name

@st.cache_resource
def _init_db_once():
    db.init_db()

_init_db_once()


# ─── Cache các truy vấn đọc dùng chung nhiều nơi ─────────────────────────
# Streamlit chạy lại TOÀN BỘ script (cả 5 tab, kể cả tab không hiển thị)
# trên MỌI tương tác (chọn 1 yêu quái, kéo slider...). Không cache thì mỗi
# lần đó bắn lại ~12 round-trip mạng tới Turso dù không có gì thay đổi.
# Cache vô thời hạn (không TTL) — luôn ĐÚNG vì được `.clear()` thủ công
# ngay sau các thao tác ghi/xóa (update_winner, save_round, import, dọn rác).
@st.cache_data(show_spinner=False)
def _cached_overall_stats():
    return db.get_overall_stats()


@st.cache_data(show_spinner=False)
def _cached_all_competitor_stats():
    return db.get_all_competitor_stats()


@st.cache_data(show_spinner=False)
def _cached_odds_calibration_data():
    return db.get_odds_calibration_data()


@st.cache_data(show_spinner=False)
def _cached_recent_rounds(limit: int):
    return db.get_recent_rounds(limit)


@st.cache_data(show_spinner=False)
def _cached_sa_rounds():
    import strategy_analysis as sa
    return sa.load_rounds()


@st.cache_data(show_spinner=False)
def _cached_high_odds_appearances(min_odds: int = 9):
    return db.get_high_odds_appearances(min_odds)


def _bust_data_cache():
    """Gọi ngay sau khi ghi/xóa dữ liệu để các bảng/biểu đồ thống kê load
    lại số liệu mới thay vì trả về cache cũ."""
    _cached_overall_stats.clear()
    _cached_all_competitor_stats.clear()
    _cached_odds_calibration_data.clear()
    _cached_recent_rounds.clear()
    _cached_sa_rounds.clear()
    _cached_high_odds_appearances.clear()


@st.cache_data(show_spinner="Đang tính ROI mô hình (leave-one-out, ~chục giây)...")
def _cached_model_picks(n_rounds_with_winner: int):
    """LOO replay (TỐN KÉM ~chục giây) — cache theo SỐ TRẬN.

    `n_rounds_with_winner` chỉ là khoá cache (phiên bản dữ liệu): khi thêm trận
    mới, số này đổi → tự tính lại; nếu không, mọi rerun (kéo slider, chuyển tab,
    reload) dùng lại kết quả cũ TỨC THÌ thay vì tính lại từ đầu.
    """
    import strategy_analysis as sa
    return sa.compute_model_picks()

st.set_page_config(
    page_title="Sư Phụ Chạy Mau - Dự đoán",
    page_icon="🎮",
    layout="wide",
)

# ─── Sidebar: tổng quan + hướng dẫn nhanh ───────────────────
with st.sidebar:
    st.header("📊 Tổng quan")
    _ov = _cached_overall_stats()
    st.metric("Tổng trận có kết quả", _ov["total"])
    _sc = st.columns(2)
    _sc[0].metric("Thầy thoát", f"{_ov['teacher_win_rate']:.0f}%")
    _sc[1].metric("Chờ nhập KQ", _ov["pending"])

    # Dọn các trận chưa nhập kết quả (winner=NULL) — có xác nhận
    if _ov["pending"] > 0:
        if not st.session_state.get("confirm_clean"):
            if st.button(f"🧹 Dọn {_ov['pending']} trận chưa có KQ", width="stretch"):
                st.session_state["confirm_clean"] = True
                st.rerun()
        else:
            st.warning(f"Xóa vĩnh viễn **{_ov['pending']}** trận chưa nhập kết quả?")
            _cc = st.columns(2)
            if _cc[0].button("✅ Xóa", type="primary", width="stretch"):
                n = db.delete_pending_rounds()
                _bust_data_cache()
                st.session_state.pop("confirm_clean", None)
                st.session_state.pop("pred", None)        # tránh trỏ tới trận đã xóa
                st.session_state.pop("last_round_id", None)
                st.toast(f"Đã xóa {n} trận chưa có KQ.")
                st.rerun()
            if _cc[1].button("Hủy", width="stretch"):
                st.session_state.pop("confirm_clean", None)
                st.rerun()

    st.divider()
    st.markdown(
        "**Dùng nhanh**\n\n"
        "1. Chọn tên + chạm bội cho 2 con **thấp** & 2 con **cao**\n"
        "2. Nhập bội Thầy → bấm **🔮 Dự đoán**\n"
        "3. Sau trận, bấm nút **người thắng** ngay dưới kết quả\n"
        "4. Bấm **🆕 Nhập trận mới** để nhập trận kế\n\n"
        "→ Bot học dần, dự đoán tốt hơn."
    )

# ─── Helper: render 1 kết quả dự đoán (sống qua rerun + ghi KQ tại chỗ) ──
METHOD_INFO = {
    "pattern":    ("🟢", "Pattern lịch sử"),
    "individual": ("🟡", "Thống kê cá nhân"),
    "multiplier": ("🔴", "Ước tính theo odds"),
}
CONF_BADGE = {"cao": "🟢 Tin cậy cao", "trung binh": "🟡 Tin cậy TB", "thap": "🔴 Tin cậy thấp"}


def render_prediction(state):
    prediction = state["prediction"]
    monsters = state["monsters"]
    teacher = state["teacher"]
    round_id = state["round_id"]

    emoji, label = METHOD_INFO.get(prediction["method"], ("⚪", prediction["method"]))
    st.info(f"{emoji} **{label}** — {prediction['sample_count']} mẫu  |  "
            f"{CONF_BADGE.get(prediction.get('confidence', 'thap'), '')}  |  {prediction['message']}")
    if prediction.get("confidence") == "thap" or prediction.get("top_gap", 1) < 0.06:
        st.warning("⚠️ Dữ liệu còn ít hoặc các lựa chọn quá sát nhau. Game ngẫu nhiên cao "
                   "— hãy coi là tham khảo, đừng cược nặng.")

    # Khuyến nghị — ưu tiên EV (value betting)
    rec = prediction["recommendation"]
    ev_rec = prediction["best_value"]
    col1, col2 = st.columns(2)
    with col1:
        if ev_rec["expected_value"] > 1.05:
            st.success(f"💰 **Kèo giá trị (ưu tiên)**\n\n"
                       f"### {ev_rec['name']}\n"
                       f"**{ev_rec['multiplier']:g}x** · EV **{ev_rec['expected_value']:.2f}** · "
                       f"thắng ~{ev_rec['probability']*100:.0f}%")
            st.caption("⚠️ Thắng ít, trả cao → variance lớn. Cược nhẹ.")
        else:
            st.warning(f"💰 **Không có kèo +EV nổi bật**\n\n"
                       f"EV tốt nhất chỉ **{ev_rec['expected_value']:.2f}** (<1) → cân nhắc **bỏ qua** trận.")
    with col2:
        st.info(f"🛡 **Kèo an toàn**\n\n"
                f"### {rec['name']}\n"
                f"**{rec['multiplier']:g}x** · {rec['probability']*100:.0f}% _(ROI dài hạn thường âm)_")

    # Bảng chi tiết + biểu đồ
    probs = prediction["probabilities"]
    odds_map = {m["name"]: m["multiplier"] for m in monsters}
    odds_map[teacher["name"]] = teacher["multiplier"]
    rows = []
    # Ưu tiên EV: sắp xếp từ kỳ vọng cao → thấp (EV = xác suất × bội).
    sorted_chars = sorted(
        probs.items(),
        key=lambda x: x[1] * odds_map.get(x[0], 1.0),
        reverse=True,
    )
    for rank, (name, prob) in enumerate(sorted_chars, 1):
        is_t = name == teacher["name"]
        detail = prediction["details"].get(name, {})
        appeared = detail.get("appeared", 0)
        won = detail.get("won", 0)
        owr = detail.get("odds_win_rate")
        oapp = detail.get("odds_appeared", 0)
        rows.append({
            "#": rank,
            "Nhân vật": f"{'👨‍🏫' if is_t else '👹'} {name}",
            "Odds": f"{odds_map.get(name, 0):g}x",
            "Lịch sử": f"{won}/{appeared}" if appeared > 0 else "—",
            "Bội này về": f"{owr*100:.0f}% ({oapp})" if owr is not None else "—",
            "EV": prob * odds_map.get(name, 1.0),
            "Xác suất (%)": prob * 100,
        })
    df = pd.DataFrame(rows)
    st.dataframe(
        df, width="stretch", hide_index=True,
        column_config={
            "Xác suất (%)": st.column_config.ProgressColumn(
                "Xác suất", format="%.0f%%", min_value=0, max_value=100,
            ),
            "EV": st.column_config.NumberColumn("EV", format="%.2f", help="Kỳ vọng = xác suất × bội số. >1 là +EV"),
            "Bội này về": st.column_config.TextColumn(
                "Bội này về",
                help="Tỷ lệ thắng THỰC TẾ của mọi nhân vật từng mang đúng giá trị bội này (số trong ngoặc = số mẫu). Tín hiệu calibration theo bội số.",
            ),
        },
    )
    with st.expander("📈 Biểu đồ xác suất"):
        fig = px.bar(df, x="Nhân vật", y="Xác suất (%)",
                     color="Xác suất (%)", color_continuous_scale="RdYlGn")
        st.plotly_chart(fig, width="stretch")

    # Tín hiệu tên×bội: để riêng (tham khảo) vì mẫu thường rất ít và CHƯA tác
    # động vào dự đoán (trọng số tầng = 0 cho tới khi tune_shrinkage.py bật).
    with st.expander("🔍 Tín hiệu tên×bội (tham khảo — chưa tác động dự đoán)"):
        no_rows = []
        for rank, (name, prob) in enumerate(sorted_chars, 1):
            detail = prediction["details"].get(name, {})
            napp = detail.get("name_odds_appeared", 0)
            nwr = detail.get("name_odds_win_rate")
            no_rows.append({
                "Nhân vật": f"{'👨‍🏫' if name == teacher['name'] else '👹'} {name}",
                "Bội": f"{odds_map.get(name, 0):g}x",
                "Con này tại bội này về": f"{nwr*100:.0f}% ({detail.get('name_odds_won',0)}/{napp})" if napp > 0 else "— (chưa có mẫu)",
            })
        st.dataframe(pd.DataFrame(no_rows), width="stretch", hide_index=True)
        st.caption(
            "Tỷ lệ thắng THỰC TẾ của đúng nhân vật này khi mang đúng bội này. "
            "Mẫu thường rất ít nên chỉ để tham khảo — tầng này sẽ **tự bật** vào dự "
            "đoán khi `tune_shrinkage.py` xác nhận đủ tin (auto-ready)."
        )

    # ── Ghi kết quả NGAY tại đây (không cần đổi tab) ──
    st.divider()
    if state.get("recorded"):
        st.success(f"✅ Đã lưu trận #{round_id}: **{state['recorded_label']}**. Bot đã học! 📈")
    else:
        st.markdown(f"**Trận #{round_id} — đua xong rồi, ai thắng?** (bấm để lưu)")
        labels = [(f"monster{i+1}", m["name"]) for i, m in enumerate(monsters)]
        labels.append(("teacher", "🏃 Thầy thoát"))
        btn_cols = st.columns(len(labels))
        for (slot, nm), bc in zip(labels, btn_cols):
            if bc.button(nm, key=f"rec_{round_id}_{slot}", width="stretch"):
                db.update_winner(round_id, slot)
                _bust_data_cache()
                state["recorded"] = slot
                state["recorded_label"] = "Thầy thoát" if slot == "teacher" else nm
                st.rerun()

    if st.button("🔄 Dự đoán trận mới", key=f"clear_{round_id}"):
        st.session_state.pop("pred", None)
        st.rerun()


# `st.tabs` chạy code của CẢ 5 tab trên mọi rerun (kể cả tab không hiển thị,
# chỉ ẩn bằng CSS) — dù cache đã giảm số round-trip Turso, code CPU + logic
# của tab khác vẫn chạy thừa mỗi lần. Dùng `st.radio` (server biết đang chọn
# gì) + if/elif để CHỈ chạy code của tab đang xem — tương tác ở tab nào chỉ
# tác động tab đó.
TAB_LABELS = [
    "🔮 Dự đoán",
    "📝 Nhập kết quả",
    "📊 Thống kê",
    "🎯 Soi cầu",
    "📋 Lịch sử",
]
active_tab = st.radio("Chọn mục:", TAB_LABELS, horizontal=True, label_visibility="collapsed")


# ─── Tab 1: Dự đoán ──────────────────────────────────────────

if active_tab == TAB_LABELS[0]:
    st.header("Dự đoán trận mới")

    with st.expander("📌 Ghi nhớ: heuristic thủ công tốt nhất (lọc từ ~120 chiến lược)"):
        st.caption("Ảnh chụp dữ liệu 237 trận (13/07/2026). Xếp theo **cận dưới CI 95%** — "
                   "ưu tiên chiến lược bền, phạt ăn may. Cập nhật lại khi data tăng nhiều.")
        st.markdown(
            "**🥇 Lõi bền nhất — yêu quái bội 5:** cược mỗi khi có.  \n"
            "→ thắng **55/143 (38%)** vs bội hàm ý 27%, ROI **+92%**, CI [+50%, +134%].\n\n"
            "**🥈 Edge thứ 2 — yêu quái bội 9:** thắng **23%** vs hàm ý ~14%, ROI **+103%**, "
            "CI [+26%, +192%] (thật, nhưng variance lớn hơn bội 5).\n\n"
            "**🔗 Chiến lược lai tốt nhất:** Thầy **≥18 → Thầy**; còn lại → **bội 5** "
            "(nếu không có 5 thì bội 9). ROI **+91%**, CI [+22%, +167%].\n\n"
            "**🤖 Model (LOO) để so:** ROI ~+41–60%, CI vượt 0."
        )
        st.markdown(
            "**🏷️ Theo TÊN (đã kiểm soát bội, vẫn thật — CI vượt 0):**  \n"
            "🥇 **Cược mạnh mọi lúc:** **Bach_nhan_quan** (+117%, thắng 27%), "
            "**Bach_tuong** (+66%, thắng **45%** — bền nhất), **Hoang_mi_vuong** (+73%), "
            "**Hong_hai_nhi** (+61%).  \n"
            "🎯 **Kèo vàng khi ở bội 5:** **Hoang_mi_vuong** (56%), **Dai_bang_kim_si** (55%).  \n"
            "☠️ **Không bao giờ cược:** **Duong_dai_tien** (9%), **Tieu_toan_phong** (7%), "
            "**Mac_lan** (7%) — lỗ chắc."
        )
        st.warning(
            "☠️ **TRÁNH bội 10:** chỉ **3/69 (4%)**, ROI **−57%** — tệ nhất.  \n"
            "⚠️ **Bội 3 vẫn lỗ nhẹ** (−4%) dù hay thắng — bội thấp, thắng không bù đủ.  \n"
            "⚠️ Các bội 4, 6, 7, 8, 11, 12 ROI **CI chạm âm** → không đáng tin, "
            "chỉ \"ăn ké\" khi nằm chung dải với bội 5. Chỉ **bội 5 và 9** là tín hiệu thật.  \n"
            "⚠️ **ĐÃ HẠ CẤP — \"Thầy ≥18\":** giờ CI **[−1%, +187%]** (chạm âm), hết đáng tin, "
            "variance quá lớn (dễ thua 15-20 trận liền).  \n"
            "⚠️ **ĐÃ HẠ CẤP — Thanh_nguu@5** (59%→**42%**) & **Hac_hung_tinh@9** (mẫu 9) — "
            "ăn may/mẫu nhỏ, đã hồi quy.",
            icon="⚠️",
        )

    # Nhập ĐÚNG cấu trúc game: 2 yêu quái bội THẤP (3–5) — luôn là 1 trong 8 con
    # cố định — + 2 yêu quái bội CAO (6–12) — 10 con còn lại. Dropdown tên đã lọc
    # sẵn theo nhóm nên không phải dò cả 18 con. Tên trong cùng nhóm không được trùng
    # (callback tự đẩy con còn lại sang tên khác), nhưng BỘI được phép trùng
    # (thực tế vẫn có trường hợp 2 con cùng bội 5, hoặc 2 con cùng bội 9).
    # Không bọc st.form: các truy vấn nặng đã @st.cache_data ở trên, nên rerun
    # mỗi lần chọn không tốn round-trip Turso — không cần chặn rerun nữa.
    LOW_MONSTERS = sorted(["Bach_tuong", "Thanh_nguu", "Loc_dai_tien", "Dai_bang_kim_si",
                           "Hong_hai_nhi", "Lao_ban", "Thanh_su", "Xich_vy_ma_hat", "Hoang_mi_vuong"])
    HIGH_MONSTERS = [m for m in sorted(KNOWN_MONSTERS) if m not in LOW_MONSTERS]
    LOW_BOI, HIGH_BOI = [3, 4, 5], [6, 7, 8, 9, 10, 11, 12]
    MONSTER_KEYS = ("lo0_name", "lo0_boi", "lo1_name", "lo1_boi",
                    "hi0_name", "hi0_boi", "hi1_name", "hi1_boi", "t_mult")

    def _avoid_clash(primary_key, secondary_key, options):
        """2 ô cùng nhóm (tên hoặc bội) lỡ chọn trùng -> tự đẩy ô còn lại sang giá trị khác."""
        p, s = st.session_state.get(primary_key), st.session_state.get(secondary_key)
        if p is not None and p == s:
            remaining = [o for o in options if o != p]
            if remaining:
                st.session_state[secondary_key] = remaining[0]

    st.subheader("👹 2 con bội THẤP (3–5)")
    cols_lo = st.columns(2)
    with cols_lo[0]:
        lo0_name = st.segmented_control("Tên (thấp 1)", LOW_MONSTERS, format_func=display_name,
                                        key="lo0_name", on_change=_avoid_clash,
                                        args=("lo0_name", "lo1_name", LOW_MONSTERS))
        lo0_boi = st.segmented_control("Bội (thấp 1)", LOW_BOI, default=5, key="lo0_boi")
    with cols_lo[1]:
        lo1_name_opts = [m for m in LOW_MONSTERS if m != st.session_state.get("lo0_name")]
        lo1_name = st.segmented_control("Tên (thấp 2)", lo1_name_opts, format_func=display_name, key="lo1_name")
        lo1_boi = st.segmented_control("Bội (thấp 2)", LOW_BOI, default=3, key="lo1_boi")

    st.subheader("👺 2 con bội CAO (6–12)")
    cols_hi = st.columns(2)
    with cols_hi[0]:
        hi0_name = st.segmented_control("Tên (cao 1)", HIGH_MONSTERS, format_func=display_name,
                                        key="hi0_name", on_change=_avoid_clash,
                                        args=("hi0_name", "hi1_name", HIGH_MONSTERS))
        hi0_boi = st.segmented_control("Bội (cao 1)", HIGH_BOI, default=9, key="hi0_boi")
    with cols_hi[1]:
        hi1_name_opts = [m for m in HIGH_MONSTERS if m != st.session_state.get("hi0_name")]
        hi1_name = st.segmented_control("Tên (cao 2)", hi1_name_opts, format_func=display_name, key="hi1_name")
        hi1_boi = st.segmented_control("Bội (cao 2)", HIGH_BOI, default=6, key="hi1_boi")

    monsters = [
        {"name": lo0_name or "", "multiplier": float(lo0_boi or 5)},
        {"name": lo1_name or "", "multiplier": float(lo1_boi or 3)},
        {"name": hi0_name or "", "multiplier": float(hi0_boi or 9)},
        {"name": hi1_name or "", "multiplier": float(hi1_boi or 6)},
    ]

    st.subheader("👨‍🏫 Sư Phụ")
    st.caption("Thầy luôn là **Duong_tang** — chỉ cần chọn bội (14–26).")
    TEACHER_BOI = list(range(14, 27))
    t_mult = st.segmented_control("Bội Thầy", TEACHER_BOI, default=18, key="t_mult")
    teacher = {"name": "Duong_tang", "multiplier": float(t_mult or 18)}

    st.divider()
    submitted = st.button("🔮 Dự đoán ngay!", type="primary", width="stretch")

    if submitted:
        from config import canonical_name
        names_norm = [canonical_name(m["name"]) for m in monsters]
        if not all(m["name"].strip() for m in monsters):
            st.error("Vui lòng chọn đủ tên cho cả 4 yêu quái!")
        elif len(set(names_norm)) < 4:
            st.error("⚠️ 4 yêu quái phải khác nhau — đang có tên bị trùng.")
        else:
            sig = (
                tuple(sorted((names_norm[i], monsters[i]["multiplier"]) for i in range(4))),
                canonical_name(teacher["name"]), teacher["multiplier"],
            )
            prev = st.session_state.get("pred")
            # Bấm lại y hệt trận vừa dự đoán (chưa ghi KQ) → tái dùng round_id
            # cũ thay vì tạo bản ghi "chờ KQ" mới mỗi lần bấm (tránh rác DB).
            if prev and prev.get("sig") == sig and prev.get("recorded") is None:
                round_id = prev["round_id"]
            else:
                round_id = db.save_round(monsters, teacher, winner=None, source="web")
                _bust_data_cache()  # trận mới -> đổi "Chờ nhập KQ" / danh sách trận
            with st.spinner("Đang tính toán..."):
                prediction = pred.predict(monsters, teacher)
            st.session_state["pred"] = {
                "prediction": prediction, "monsters": monsters, "sig": sig,
                "teacher": teacher, "round_id": round_id, "recorded": None,
            }
            st.session_state["last_round_id"] = round_id

    # Kết quả sống qua mọi rerun (không biến mất khi bấm nút khác)
    if "pred" in st.session_state:
        st.divider()
        render_prediction(st.session_state["pred"])
        if st.button("🆕 Nhập trận mới", width="stretch"):
            for _k in MONSTER_KEYS:
                st.session_state.pop(_k, None)
            st.session_state.pop("pred", None)
            st.session_state.pop("last_round_id", None)
            st.rerun()


# ─── Tab 2: Nhập kết quả ─────────────────────────────────────

elif active_tab == TAB_LABELS[1]:
    st.header("Nhập kết quả trận")
    st.caption("💡 Cách nhanh nhất: bấm nút người thắng **ngay dưới kết quả ở tab Dự đoán**. "
               "Tab này để nhập cho các trận cũ còn sót.")

    recent = _cached_recent_rounds(30)
    pending = [r for r in recent if r["winner"] is None]

    if not pending:
        st.success("✅ Không có trận nào đang chờ kết quả.")
    else:
        opts = {
            f"#{r['id']} {r['created_at'][:16]} — "
            f"{r['monster1_name']}, {r['monster2_name']}, {r['monster3_name']}, {r['monster4_name']} | "
            f"Thầy: {r['teacher_name']}": r
            for r in pending
        }
        selected_label = st.selectbox("Chọn trận:", list(opts.keys()))
        r = opts[selected_label]

        choices = []
        for slot in ["monster1", "monster2", "monster3", "monster4"]:
            choices.append((f"👹 {r[f'{slot}_name']} ({r[f'{slot}_multiplier']:.0f}x)", slot))
        choices.append((f"👨‍🏫 Thầy thoát ({r['teacher_name']})", "teacher"))

        winner_label = st.radio("Ai đã thắng?", [c[0] for c in choices])
        winner_slot = next(slot for label, slot in choices if label == winner_label)

        if st.button("✅ Lưu kết quả", type="primary"):
            db.update_winner(r["id"], winner_slot)
            _bust_data_cache()
            st.success(f"Đã lưu trận #{r['id']} → {winner_label} 📈")
            st.rerun()


# ─── Tab 3: Thống kê ─────────────────────────────────────────

elif active_tab == TAB_LABELS[2]:
    st.header("Thống kê")

    overall = _cached_overall_stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tổng trận", overall["total"])
    c2.metric("Yêu quái thắng", overall["monster_wins"])
    c3.metric("Thầy thoát", overall["teacher_wins"])
    c4.metric("Tỷ lệ thầy thoát", f"{overall['teacher_win_rate']:.1f}%")

    if overall["total"] > 0:
        col_left, col_right = st.columns(2)

        with col_left:
            fig_pie = go.Figure(data=[go.Pie(
                labels=["Yêu quái thắng", "Thầy thoát"],
                values=[overall["monster_wins"], overall["teacher_wins"]],
                marker_colors=["#FF6B6B", "#4ECDC4"],
                hole=0.35,
            )])
            fig_pie.update_layout(title="Tỷ lệ thắng tổng quát", height=350)
            st.plotly_chart(fig_pie, width="stretch")

        # Thống kê từng nhân vật
        all_comp_stats = _cached_all_competitor_stats()
        non_teacher = [s for s in all_comp_stats if s["name"] != "Duong_tang" and s["appeared"] > 0]
        teacher_stat = next((s for s in all_comp_stats if s["name"] == "Duong_tang"), None)

        stat_rows = []
        for s in non_teacher:
            rate = s["won"] / s["appeared"] * 100
            stat_rows.append({
                "Yêu quái": s["name"],
                "Số lần xuất hiện": s["appeared"],
                "Số lần thắng": s["won"],
                "Tỷ lệ thắng (%)": round(rate, 1),
            })
        if teacher_stat and teacher_stat["appeared"] > 0:
            rate = teacher_stat["won"] / teacher_stat["appeared"] * 100
            stat_rows.append({
                "Yêu quái": f"👨‍🏫 {teacher_stat['name']}",
                "Số lần xuất hiện": teacher_stat["appeared"],
                "Số lần thắng": teacher_stat["won"],
                "Tỷ lệ thắng (%)": round(rate, 1),
            })

        stat_df = pd.DataFrame(stat_rows).sort_values("Tỷ lệ thắng (%)", ascending=False)

        with col_right:
            fig_bar = px.bar(
                stat_df[stat_df["Yêu quái"].str.startswith("👨‍🏫") == False],
                x="Yêu quái", y="Tỷ lệ thắng (%)",
                color="Tỷ lệ thắng (%)",
                color_continuous_scale="RdYlGn",
                title="Tỷ lệ thắng từng yêu quái",
                height=350,
            )
            fig_bar.update_xaxes(tickangle=45)
            st.plotly_chart(fig_bar, width="stretch")

        st.subheader("Chi tiết từng nhân vật")
        st.dataframe(stat_df, width="stretch", hide_index=True)

        # Odds Calibration — odds có dự đoán đúng không?
        st.subheader("📐 Phân tích Odds Calibration")
        st.caption("Kiểm tra xem odds của game có phản ánh đúng xác suất thắng thực tế không")

        calib_data = _cached_odds_calibration_data()
        if len(calib_data) >= 20:
            calib_df = pd.DataFrame(calib_data)
            # Nhóm odds theo bins
            calib_df["odds_bin"] = pd.cut(
                calib_df["odds"],
                bins=[0, 3, 5, 7, 10, 15, 100],
                labels=["1-3", "3-5", "5-7", "7-10", "10-15", "15+"]
            )
            calib_grouped = calib_df.groupby("odds_bin", observed=True).agg(
                actual_win_rate=("is_winner", "mean"),
                count=("is_winner", "count"),
            ).reset_index()
            calib_grouped["implied_prob_mid"] = [1/2, 1/4, 1/6, 1/8.5, 1/12.5, 1/20]

            fig_calib = go.Figure()
            fig_calib.add_trace(go.Bar(
                x=calib_grouped["odds_bin"].astype(str),
                y=calib_grouped["actual_win_rate"] * 100,
                name="Tỷ lệ thắng thực tế (%)",
                marker_color="#4ECDC4",
            ))
            fig_calib.add_trace(go.Scatter(
                x=calib_grouped["odds_bin"].astype(str),
                y=[p*100 for p in calib_grouped["implied_prob_mid"]],
                name="Xác suất implied từ odds (%)",
                mode="lines+markers",
                line=dict(color="#FF6B6B", dash="dash"),
            ))
            fig_calib.update_layout(
                title="Odds Calibration: Thực tế vs Implied",
                xaxis_title="Odds range",
                yaxis_title="%",
                height=400,
            )
            st.plotly_chart(fig_calib, width="stretch")
            st.caption(
                "Nếu 2 đường gần nhau → odds phản ánh chính xác xác suất. "
                "Nếu thanh xanh cao hơn đường đỏ ở vùng odds cao → cơ hội upset nhiều hơn game dự kiến."
            )
        else:
            st.info("Cần ít nhất 20 trận để phân tích odds calibration.")

        # ── Phân tích chiến lược cược (ROI) ──
        st.divider()
        st.subheader("💰 Phân tích chiến lược cược (ROI)")
        import strategy_analysis as sa
        sa_rounds = _cached_sa_rounds()
        if len(sa_rounds) < 15:
            st.info("Cần ít nhất 15 trận có kết quả để phân tích ROI.")
        else:
            st.caption(
                f"Mô phỏng cược 1 đơn vị/trận trên {len(sa_rounds)} trận. "
                "ROI > 0 = có lãi. **⚠️ Cảnh báo: dựa trên ít trận thắng → variance rất lớn, "
                "ROI có thể đảo dấu khi thêm dữ liệu. Đừng coi là chắc chắn.**"
            )

            # ROI "theo mô hình" (leave-one-out) là phần TỐN KÉM ~chục giây. Để mở
            # web KHÔNG phải tính lại: kết quả được LƯU RA ĐĨA (model_picks_cache.json)
            # kèm số trận. Khi mở web, nếu cache còn khớp số trận → LOAD ngay (như
            # tuned_params). Chỉ khi thêm trận mới (cache lỗi thời) mới cần bấm nút
            # tính lại 17s — lúc đó kết quả được ghi đè để các lần sau lại chỉ load.
            model_strats = []
            n_now = len(sa_rounds)
            picks = sa.load_model_picks_cache(n_now)

            if picks is None and st.session_state.get("show_model_roi"):
                # Cache lỗi thời/chưa có và người dùng đã bấm tính → replay rồi lưu.
                try:
                    picks = _cached_model_picks(n_now)
                    sa.save_model_picks_cache(picks, n_now)
                except Exception as _e:
                    st.caption(f"⚠️ Không tính được ROI mô hình leave-one-out: {_e}")
                st.session_state.pop("show_model_roi", None)

            # Slider LUÔN hiện (không biến mất khi data đổi / cache lỗi thời).
            ev_thr = st.slider(
                "Ngưỡng EV để lọc trận (cho dòng \"chỉ khi EV ≥ …\")",
                min_value=1.0, max_value=4.0, value=2.0, step=0.1,
                help="Mô hình chỉ cược khi con có EV dự đoán cao nhất ≥ ngưỡng này. "
                     "EV con tốt nhất trong dữ liệu hiện tại nằm khoảng 1.4–5.4 → "
                     "đặt ~2.0 trở lên mới thực sự lọc bớt trận.",
            )

            if picks is not None:
                # Lọc theo ngưỡng EV là tức thì nên kéo slider không lag.
                model_strats = sa.aggregate_model_strategies(picks, ev_threshold=ev_thr)
            else:
                # Cache lỗi thời (vừa thêm trận / đổi tham số) → nút tính lại; slider
                # vẫn ở trên, bấm xong sẽ áp dụng ngay.
                st.caption("⏳ Data vừa đổi — cần tính lại ROI mô hình (leave-one-out).")
                if st.button("▶️ Tính **ROI theo mô hình** (~chục giây, lưu lại cho lần sau)"):
                    st.session_state["show_model_roi"] = True
                    st.rerun()

            all_strats = model_strats + sa.compute_strategies(sa_rounds)

            strat_rows = [{
                "Chiến lược": s["label"],
                "Số cược": s["bets"],
                "Thắng": s["wins"],
                "Tỷ lệ thắng": s["hit_rate"] * 100,
                "ROI": s["roi"] * 100,
                "Lãi/lỗ (đơn vị)": s["profit"],
            } for s in all_strats]
            st.dataframe(
                pd.DataFrame(strat_rows), width="stretch", hide_index=True,
                column_config={
                    "Tỷ lệ thắng": st.column_config.NumberColumn(format="%.0f%%"),
                    "ROI": st.column_config.NumberColumn(format="%+.0f%%", help="Lợi nhuận / số tiền cược"),
                    "Lãi/lỗ (đơn vị)": st.column_config.NumberColumn(format="%+.1f"),
                },
            )
            if model_strats:
                st.caption(
                    "2 dòng **\"Theo mô hình\"** tính *leave-one-out* (mỗi trận dự đoán bằng "
                    f"tất cả trận khác, bỏ đúng trận đó) — KHÔNG bị thổi phồng. Dòng *\"chỉ khi EV ≥ {ev_thr:g}\"* "
                    "bỏ qua các trận EV thấp — kéo slider lên để xem lọc trận EV cao có cải thiện ROI không."
                )
                st.caption(
                    "ℹ️ *ROI ở đây (leave-one-out) khác với \"ROI/trận (walk-forward)\" ở phần "
                    "Tinh chỉnh bên dưới — hai thước đo khác nhau, lệch nhau là bình thường.*"
                )

            with st.expander("📊 Tỷ lệ thắng theo từng giá trị bội (yêu quái)"):
                ow_rows = [{
                    "Bội": d["odds"], "Xuất hiện": d["appeared"], "Thắng": d["won"],
                    "Win%": d["win_rate"] * 100, "Implied%": d["implied"] * 100,
                    "EV": d["ev"], "ROI": d["roi"] * 100,
                } for d in sa.compute_odds_winrate(sa_rounds)]
                st.dataframe(
                    pd.DataFrame(ow_rows), width="stretch", hide_index=True,
                    column_config={
                        "Win%": st.column_config.NumberColumn(format="%.0f%%"),
                        "Implied%": st.column_config.NumberColumn(format="%.0f%%"),
                        "EV": st.column_config.NumberColumn(format="%.2f"),
                        "ROI": st.column_config.NumberColumn(format="%+.0f%%"),
                    },
                )
                st.caption("EV = Win% × Bội. EV > 1 nghĩa là +EV (về lý thuyết có lãi).")

            with st.expander("🏃 Thầy thoát theo từng giá trị bội"):
                tb_rows = [{
                    "Bội thầy": d["odds"], "Xuất hiện": d["appeared"], "Thoát": d["escaped"],
                    "Thoát%": d["escape_rate"] * 100, "Implied%": d["implied"] * 100,
                    "EV": d["ev"], "ROI": d["roi"] * 100,
                } for d in sa.compute_teacher_by_odds(sa_rounds)]
                st.dataframe(
                    pd.DataFrame(tb_rows), width="stretch", hide_index=True,
                    column_config={
                        "Thoát%": st.column_config.NumberColumn(format="%.0f%%"),
                        "Implied%": st.column_config.NumberColumn(format="%.0f%%"),
                        "EV": st.column_config.NumberColumn(format="%.2f"),
                        "ROI": st.column_config.NumberColumn(format="%+.0f%%"),
                    },
                )
                st.caption("Thầy thường thoát ~9-10% thực tế nhưng odds chỉ hàm ý ~4-6% → bị định giá thấp.")
    else:
        st.info("Chưa có dữ liệu. Hãy nhập trận và kết quả thủ công.")

    # ── Tinh chỉnh mô hình (chạy tune_shrinkage.py) ──
    st.divider()
    st.subheader("⚙️ Tinh chỉnh mô hình")
    st.caption(
        "Chạy `tune_shrinkage.py`: quét lại các tham số shrinkage (ind_k, odds_k, "
        "name_odds_k), chọn bộ có **ROI cao nhất** (EV tối đa khi mỗi trận cược bằng "
        "nhau vào nhân vật có EV dự đoán cao nhất) và ghi vào `tuned_params.json`. "
        "Tầng **tên×bội** nằm trong lưới quét — khi đủ mẫu nó sẽ tự được bật. Nên "
        "chạy lại sau mỗi lần thêm/nhập dữ liệu mới."
    )
    st.caption(
        "📐 *Tinh chỉnh dùng ROI **walk-forward** (mỗi trận chỉ học từ trận trước) — "
        "đúng chuẩn chọn tham số cho chuỗi thời gian, tránh nhìn lén tương lai. Khác "
        "với bảng \"Theo mô hình\" phía trên dùng **leave-one-out** để báo cáo chất "
        "lượng — nên 2 con số ROI có thể lệch nhau, đó là bình thường.*"
    )

    import json as _json
    _params_path = os.path.join(os.path.dirname(__file__), "tuned_params.json")
    try:
        with open(_params_path, encoding="utf-8") as _f:
            _cur = _json.load(_f)
        _meta = _cur.get("_meta", {})
        pc1, pc2, pc3, pc4 = st.columns(4)
        pc1.metric("ind_k (theo tên)", _cur.get("INDIVIDUAL_PRIOR_STRENGTH", "—"))
        pc2.metric("odds_k (theo bội)", _cur.get("ODDS_CALIB_STRENGTH", "—"))
        pc3.metric(
            "name_odds_k (tên×bội)", _cur.get("NAME_ODDS_STRENGTH", "—"),
            help="0 = tầng tên×bội đang TẮT (chưa đủ mẫu). >0 = đã tự bật.",
        )
        _roi = _meta.get("roi_ev")
        pc4.metric(
            "ROI/trận (walk-forward)",
            f"{_roi:+.1%}" if isinstance(_roi, (int, float)) else "—",
            help="Lời/lỗ trung bình mỗi trận khi cược 1 đơn vị vào nhân vật có EV "
                 "dự đoán cao nhất, tính WALK-FORWARD. Đây là tiêu chí chọn bộ tham "
                 "số (khác ROI leave-one-out ở bảng phía trên). Lưu ý: rất nhiễu với "
                 "ít dữ liệu, không đảm bảo tương lai.",
        )
        if _meta:
            _roi_str = f"ROI {_roi:+.4f}/trận · " if isinstance(_roi, (int, float)) else ""
            st.caption(
                f"Bộ tham số hiện tại — quét trên {_meta.get('rounds_evaluated','?')} trận · "
                f"{_roi_str}(tiêu chí chọn: ROI cao nhất)."
            )
            st.caption(
                f"Chỉ số phụ — logloss {_meta.get('logloss','?')} · "
                f"brier {_meta.get('brier','?')} · "
                f"top1 {_meta.get('top1','?')}/{_meta.get('rounds_evaluated','?')}."
            )
    except (OSError, ValueError):
        st.info("Chưa có `tuned_params.json` — bấm nút dưới để tạo lần đầu (đang dùng giá trị mặc định).")

    if st.button("▶️ Chạy tinh chỉnh ngay", type="primary"):
        import subprocess, sys, importlib
        from tune_shrinkage import TOTAL_COMBOS

        progress = st.progress(0.0, text=f"Bắt đầu quét 0/{TOTAL_COMBOS} tổ hợp...")
        log_box = st.empty()
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(
            [sys.executable, "-u", "tune_shrinkage.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
        )
        lines, done = [], 0
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            tok = line.strip().split()
            # Mỗi dòng dữ liệu (1 tổ hợp) bắt đầu bằng số (cột indK).
            if tok and tok[0].lstrip("-").isdigit():
                done += 1
                progress.progress(
                    min(done / TOTAL_COMBOS, 1.0),
                    text=f"Đã quét {done}/{TOTAL_COMBOS} tổ hợp...",
                )
            log_box.code("\n".join(lines[-14:]), language="text")
        proc.wait()
        progress.empty()

        reloaded = False
        if proc.returncode == 0:
            try:  # Nạp lại tham số mới cho phiên đang chạy (best-effort).
                import config as _cfg
                importlib.reload(_cfg)
                importlib.reload(pred)
                reloaded = True
            except Exception:
                reloaded = False
        st.session_state["tune_result"] = {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": "\n".join(lines),
            "stderr": "",
            "reloaded": reloaded,
        }
        st.rerun()

    # Hiển thị kết quả lần tinh chỉnh gần nhất (giữ qua rerun để đọc được output).
    _tr = st.session_state.get("tune_result")
    if _tr:
        if _tr["ok"]:
            st.success("✅ Tinh chỉnh xong! `tuned_params.json` đã cập nhật (xem chỉ số phía trên).")
            st.caption(
                "Đã nạp tham số mới vào phiên hiện tại — dự đoán kế tiếp dùng ngay."
                if _tr["reloaded"]
                else "Đã ghi tham số mới. Khởi động lại app để áp dụng hoàn toàn."
            )
            with st.expander("Xem chi tiết bảng quét"):
                st.code(_tr["stdout"] or "(không có output)", language="text")
        else:
            st.error(f"❌ Tinh chỉnh lỗi (exit {_tr['code']}).")
            if _tr["stdout"]:
                st.code(_tr["stdout"], language="text")
            if _tr["stderr"]:
                st.code(_tr["stderr"], language="text")
        if st.button("Ẩn kết quả", key="hide_tune_result"):
            st.session_state.pop("tune_result", None)
            st.rerun()


# ─── Tab 4: Soi cầu ──────────────────────────────────────────

elif active_tab == TAB_LABELS[3]:
    st.header("🎯 Soi cầu — con lâu chưa về (bội cao)")
    st.caption(
        "Kiểu soi cầu xổ số: thống kê các yêu quái **bội cao (≥9)** đã lâu chưa "
        "**về đích** (thắng), và **trung bình quá khứ bao lâu mới về 1 lần**. "
        "Chỉ tính những lần con đó ra sân ở **bội ≥ 9**."
    )
    st.warning(
        "⚠️ Đây là soi cầu theo kiểu xổ số (**gambler's fallacy**): game vốn ngẫu "
        "nhiên, con \"quá hạn\" **KHÔNG** chắc chắn lần tới sẽ về. Chỉ để tham khảo.",
        icon="⚠️",
    )

    # Bội là BỘ LỌC phạm vi (không tách bảng theo tên×bội — 255 trận chia 36 ô
    # quá thưa để "chu kỳ về" có nghĩa). Luôn gom theo TÊN trong phạm vi đã lọc.
    c_scope, c_min = st.columns([3, 2])
    scope = c_scope.radio(
        "Phạm vi bội:",
        ["Gộp bội 9–12", "Chỉ bội 9", "Chỉ bội 10–12"],
        horizontal=True,
        help="Bội 9 thường là tín hiệu thật; bội 10 lịch sử tệ nhất. Tách ra để soi cho đúng.",
    )
    min_app = c_min.slider("Số lần xuất hiện tối thiểu:", 1, 15, 3,
                           help="Ẩn các con quá ít mẫu để tránh nhiễu.")

    appearances = _cached_high_odds_appearances(9)
    if scope == "Chỉ bội 9":
        appearances = [a for a in appearances if a["odds"] == 9]
    elif scope == "Chỉ bội 10–12":
        appearances = [a for a in appearances if a["odds"] >= 10]

    if not appearances:
        st.info("Chưa có dữ liệu cho phạm vi bội này.")
    else:
        # Gom theo tên, giữ thứ tự thời gian (query đã ORDER BY round_id).
        from collections import defaultdict
        by_name = defaultdict(list)
        for a in appearances:
            by_name[a["name"]].append(a)

        rows = []
        for name, apps in by_name.items():
            appeared = len(apps)
            if appeared < min_app:
                continue
            won = sum(a["won"] for a in apps)
            win_positions = [i for i, a in enumerate(apps) if a["won"]]
            if win_positions:
                last_win_pos = win_positions[-1]
                drought = appeared - 1 - last_win_pos   # số lần ra sân SAU lần về gần nhất
                last_win_date = apps[last_win_pos]["created_at"][:10]
            else:
                drought = appeared                       # chưa từng về -> khan = toàn bộ
                last_win_date = None
            avg_cycle = appeared / won if won else None  # ~ số lần ra sân / 1 lần về

            if won == 0:
                status = "❓ chưa từng về"
            elif drought >= avg_cycle:
                status = "🔥 quá hạn"
            elif drought >= 0.7 * avg_cycle:
                status = "🟡 tới hạn"
            else:
                status = "⚪ bình thường"

            rows.append({
                "Yêu quái": display_name(name),
                "Xuất hiện": appeared,
                "Về": won,
                "Tỉ lệ về": won / appeared,
                "Đang khan": drought,
                "Chu kỳ TB": round(avg_cycle, 1) if avg_cycle else None,
                "Lần về gần nhất": last_win_date or "— chưa từng về",
                "Trạng thái": status,
            })

        if not rows:
            st.info(f"Không con nào đạt tối thiểu {min_app} lần xuất hiện trong phạm vi này.")
        else:
            # Lâu chưa về nhất lên đầu; đồng hạng thì nhiều mẫu hơn trước.
            rows.sort(key=lambda r: (r["Đang khan"], r["Xuất hiện"]), reverse=True)
            df = pd.DataFrame(rows)
            st.dataframe(
                df, width="stretch", hide_index=True,
                column_config={
                    "Tỉ lệ về": st.column_config.ProgressColumn(
                        "Tỉ lệ về", format="percent", min_value=0.0, max_value=1.0),
                    "Đang khan": st.column_config.NumberColumn(
                        "Đang khan", help="Số lần ra sân (bội≥9) liên tiếp gần đây CHƯA về"),
                    "Chu kỳ TB": st.column_config.NumberColumn(
                        "Chu kỳ TB", help="Trung bình cứ bao nhiêu lần ra sân thì về 1 lần"),
                },
            )
            hot = [r["Yêu quái"] for r in rows if r["Trạng thái"] == "🔥 quá hạn"]
            if hot:
                st.info("🔥 **Đang quá hạn (khan ≥ chu kỳ TB):** " + ", ".join(hot))

        with st.expander("ℹ️ Cách đọc các cột"):
            st.markdown(
                "- **Xuất hiện / Về:** số lần con đó ra sân ở bội ≥ 9 / trong đó về đích mấy lần.\n"
                "- **Đang khan:** số lần ra sân gần đây **liên tiếp chưa về** (kể từ sau lần về gần nhất). "
                "Càng lớn = càng lâu chưa về.\n"
                "- **Chu kỳ TB:** trung bình quá khứ cứ **bao nhiêu lần ra sân thì về 1 lần** "
                "(= Xuất hiện ÷ Về). Ví dụ 5 nghĩa là trung bình ~5 lần mới về 1.\n"
                "- **Trạng thái:** 🔥 quá hạn (Đang khan ≥ Chu kỳ TB) · 🟡 tới hạn (≥ 70% chu kỳ) · "
                "⚪ bình thường · ❓ chưa từng về."
            )


# ─── Tab 5: Lịch sử ──────────────────────────────────────────

elif active_tab == TAB_LABELS[4]:
    st.header("Lịch sử trận đấu")

    all_rounds = _cached_recent_rounds(200)
    if not all_rounds:
        st.info("Chưa có dữ liệu.")
    else:
        df_rows = []
        for r in all_rounds:
            if r["winner"] == "teacher":
                winner_label = f"👨‍🏫 Thầy ({r['teacher_name']})"
            elif r["winner"]:
                slot = r["winner"]
                winner_label = f"👹 {r[f'{slot}_name']}"
            else:
                winner_label = "⏳ Chờ"
            df_rows.append({
                "ID": r["id"],
                "Thời gian": r["created_at"][:16],
                "YQ1": f"{r['monster1_name']} ({r['monster1_multiplier']:.0f}x)",
                "YQ2": f"{r['monster2_name']} ({r['monster2_multiplier']:.0f}x)",
                "YQ3": f"{r['monster3_name']} ({r['monster3_multiplier']:.0f}x)",
                "YQ4": f"{r['monster4_name']} ({r['monster4_multiplier']:.0f}x)",
                "Sư Phụ": f"{r['teacher_name']} ({r['teacher_multiplier']:.0f}x)",
                "Kết quả": winner_label,
                "Nguồn": r["source"],
            })
        st.dataframe(pd.DataFrame(df_rows), width="stretch", hide_index=True)
