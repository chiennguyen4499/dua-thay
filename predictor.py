"""
Predictor cho Sư Phụ Chạy Mau.

Thuật toán 2 tầng, tự chọn theo lượng data:
  1. Individual stats — shrinkage phân tầng (bội → tên → tên×bội), ≥10 trận
  2. Odds-implied     — dùng khi chưa có dữ liệu (odds thấp = xác suất cao hơn)

(Tầng "pattern cùng combo" đã bỏ 2026-07-20: 199 pattern key / 279 trận nên
gần như không bao giờ đủ mẫu — walk-forward chỉ kích hoạt 6/269 lần và tắt đi
logloss không đổi, ROI còn tăng. Về nguyên lý cũng không có cơ chế game nào
khiến TỔ HỢP TÊN (bỏ qua bội) mang tín hiệu vượt trên từng con + bội của nó.)
"""

from database import get_all_rounds_with_winner
from config import (
    INDIVIDUAL_PRIOR_STRENGTH,
    ODDS_CALIB_STRENGTH, NAME_ODDS_STRENGTH,
    canonical_name, display_name,
)


class HistoryStats:
    """Thống kê lịch sử tính SẴN 1 lần từ danh sách trận (winner IS NOT NULL).

    Trước đây predictor gọi ~6 hàm DB (mỗi hàm 1 query UNION 4 slot) cho mỗi
    lượt dự đoán, và backtest/tune/LOO phải tráo `db.DATABASE_PATH` toàn cục +
    ghi hàng trăm trận vào DB tạm (nguồn của hazard rò dữ liệu). Giờ mọi thống
    kê được gộp trong 1 lượt duyệt Python trên list trận truyền vào — predict
    trở thành PURE FUNCTION (không đụng DB), backtest/tune/LOO chỉ cần cắt list.

    Với <1000 trận, dựng lại object này mỗi lượt dự đoán chỉ tốn micro-giây.
    """

    __slots__ = ("total", "_mn", "_tn", "_mo", "_to", "_mno", "_tno")

    def __init__(self, rounds: list[dict]):
        self.total = len(rounds)
        mn, tn = {}, {}      # tên -> [appeared, won]
        mo, to = {}, {}      # bội (int) -> [appeared, won]
        mno, tno = {}, {}    # (tên, bội) -> [appeared, won]

        def bump(d, key, won):
            cell = d.get(key)
            if cell is None:
                d[key] = [1, won]
            else:
                cell[0] += 1
                cell[1] += won

        for r in rounds:
            w = r["winner"]
            for i in range(1, 5):
                nm = r[f"monster{i}_name"]
                od = int(round(r[f"monster{i}_multiplier"]))
                won = 1 if w == f"monster{i}" else 0
                bump(mn, nm, won)
                bump(mo, od, won)
                bump(mno, (nm, od), won)
            tnm = r["teacher_name"]
            tod = int(round(r["teacher_multiplier"]))
            twon = 1 if w == "teacher" else 0
            bump(tn, tnm, twon)
            bump(to, tod, twon)
            bump(tno, (tnm, tod), twon)
        self._mn, self._tn = mn, tn
        self._mo, self._to = mo, to
        self._mno, self._tno = mno, tno

    def name_stat(self, name: str, is_teacher: bool) -> dict:
        a = (self._tn if is_teacher else self._mn).get(name, (0, 0))
        return {"appeared": a[0], "won": a[1]}

    def odds_stat(self, odds: int, is_teacher: bool) -> dict:
        a = (self._to if is_teacher else self._mo).get(odds, (0, 0))
        return {"appeared": a[0], "won": a[1]}

    def name_odds_stat(self, name: str, odds: int, is_teacher: bool) -> dict:
        a = (self._tno if is_teacher else self._mno).get((name, odds), (0, 0))
        return {"appeared": a[0], "won": a[1]}


def _implied_prob(odds_dict: dict[str, float]) -> dict[str, float]:
    """Chuyển odds → xác suất implied (normalize 1/odds)."""
    inv = {name: 1.0 / max(o, 0.01) for name, o in odds_dict.items()}
    total = sum(inv.values())
    return {name: v / total for name, v in inv.items()}


def predict(monsters: list[dict], teacher: dict, rounds: list[dict] | None = None) -> dict:
    """
    Dự đoán 1 trận. `rounds` = danh sách trận lịch sử (mỗi dict như 1 row DB,
    winner IS NOT NULL). Nếu None thì tự đọc toàn bộ lịch sử từ DB — dùng cho
    lời gọi đơn lẻ (bot); web nên truyền list ĐÃ CACHE để khỏi round-trip Turso;
    backtest/tune/LOO truyền list đã cắt (chỉ trận quá khứ / bỏ trận đang xét).

    Trả về dict:
    {
        method: str,
        sample_count: int,
        probabilities: {name: float, ...},   # sum = 1
        recommendation: {name, multiplier, probability},
        best_value: {name, multiplier, probability, expected_value},
        message: str,
        details: {name: {appeared, won, win_rate, implied_prob}, ...}
    }
    """
    if rounds is None:
        rounds = get_all_rounds_with_winner()
    hist = HistoryStats(rounds)

    # Chuẩn hóa tên về canonical để khớp với dữ liệu đã lưu trong DB.
    monsters = [{**m, "name": canonical_name(m["name"])} for m in monsters]
    teacher = {**teacher, "name": canonical_name(teacher["name"])}

    all_chars = monsters + [teacher]
    teacher_name = teacher["name"]

    odds_map = {c["name"]: c["multiplier"] for c in all_chars}
    q = _implied_prob(odds_map)  # odds-implied probabilities

    is_teacher = lambda c: c["name"] == teacher_name

    # ── Tầng 1: Thống kê cá nhân + blend với odds ─────────────
    total_db = hist.total
    stats = {c["name"]: hist.name_stat(c["name"], is_teacher(c)) for c in all_chars}

    # Số lần xuất hiện ít nhất của bất kỳ nhân vật nào trong trận này
    min_appearances = min(s["appeared"] for s in stats.values())

    if total_db >= 10:
        probs, details = _from_individual(all_chars, stats, q, is_teacher, odds_map, hist)
        msg = f"Dựa trên {total_db} trận lịch sử (theo tên + giá trị bội)"
        if min_appearances < 5:
            msg += " (một số nhân vật ít dữ liệu, blend với odds)"
        return _build_result(
            monsters, teacher, probs, "individual", total_db, details, msg
        )

    # ── Tầng 2: Chỉ dùng odds ─────────────────────────────────
    details = {}
    for c in all_chars:
        s = stats[c["name"]]
        details[c["name"]] = {
            "appeared": s["appeared"], "won": s["won"],
            "win_rate": None, "implied_prob": q[c["name"]]
        }
    return _build_result(
        monsters, teacher, q, "multiplier", total_db, details,
        "Chưa đủ dữ liệu, ước tính theo odds (odds thấp = xác suất cao hơn)"
    )


def _shrink(won, appeared, prior, strength) -> float:
    """Beta-Binomial shrinkage: kéo tỷ lệ thắng quan sát về `prior`.

    appeared >> strength -> tin dữ liệu;  appeared << strength -> gần prior.
    strength <= 0 -> bỏ qua tầng này (trả về prior).
    """
    if strength <= 0:
        return prior
    return (won + prior * strength) / (appeared + strength)


def _from_individual(all_chars, stats, q, is_teacher_fn, odds_map, hist) -> tuple[dict, dict]:
    """
    Ước lượng xác suất bằng shrinkage Beta-Binomial PHÂN TẦNG, từ thô đến tinh:

        1/odds (q)
          └─► win-rate GỘP theo giá trị bội   (K = ODDS_CALIB_STRENGTH)
                └─► win-rate theo TÊN nhân vật (K = INDIVIDUAL_PRIOR_STRENGTH)
                      └─► win-rate theo (TÊN × giá trị bội) (K = NAME_ODDS_STRENGTH)

    Mỗi tầng là prior cho tầng tinh hơn kế tiếp; tầng nào đủ mẫu thì lấn át,
    tầng nào mỏng mẫu thì tự rơi về tầng thô hơn. Nhờ đó:
      - "bội 5 hay về", "thầy bội cao hay thoát"  -> bắt được qua tầng giá trị bội
        (gộp mọi tên nên nhiều mẫu, ổn định).
      - đặc thù từng nhân vật                      -> qua tầng tên.
      - đặc thù tên-tại-bội-cụ-thể                 -> qua tầng cuối, nhưng K cao
        nên gần như chỉ kích hoạt khi đã có nhiều dữ liệu (tránh overfit).

    Mọi bảng thống kê lấy từ `hist` (HistoryStats) — không đụng DB.
    """
    est = {}
    details = {}
    for c in all_chars:
        name = c["name"]
        o = int(round(odds_map[name]))
        is_t = is_teacher_fn(c)
        s = stats[name]

        # Tầng 1: win-rate gộp theo giá trị bội này, shrink về 1/odds.
        ostat = hist.odds_stat(o, is_t)
        p_odds = _shrink(ostat["won"], ostat["appeared"], q[name], ODDS_CALIB_STRENGTH)

        # Tầng 2: win-rate theo tên, shrink về tầng giá trị bội.
        p_name = _shrink(s["won"], s["appeared"], p_odds, INDIVIDUAL_PRIOR_STRENGTH)

        # Tầng 3: win-rate theo (tên × bội), shrink về tầng tên.
        no = hist.name_odds_stat(name, o, is_t)
        p_final = _shrink(no["won"], no["appeared"], p_name, NAME_ODDS_STRENGTH)

        est[name] = p_final

        odds_wr = (ostat["won"] / ostat["appeared"]) if ostat["appeared"] > 0 else None
        # Tín hiệu "tên × bội": con này TẠI bội này về bao nhiêu % (số mẫu thường ít).
        name_odds_wr = (no["won"] / no["appeared"]) if no["appeared"] > 0 else None
        details[name] = {
            "appeared": s["appeared"], "won": s["won"],
            "win_rate": (s["won"] / s["appeared"]) if s["appeared"] > 0 else None,
            "implied_prob": q[name],
            "odds_value": o,
            "odds_win_rate": odds_wr,
            "odds_appeared": ostat["appeared"],
            "name_odds_won": no["won"],
            "name_odds_appeared": no["appeared"],
            "name_odds_win_rate": name_odds_wr,
        }

    total = sum(est.values())
    probs = {k: v / total for k, v in est.items()}
    return probs, details


def _wilson_ci(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Khoảng tin cậy Wilson cho xác suất `p` dựa trên `n` mẫu nền.

    Mẫu ít -> khoảng RỘNG (thành thật về độ bất định của ước lượng); mẫu nhiều
    -> khoảng hẹp. Dùng để hiển thị "xác suất/EV nằm trong khoảng nào" thay vì
    một con số điểm giả vờ chính xác. `p` là xác suất mô hình đã blend; `n` là
    số mẫu thực tế đỡ cho ước lượng đó (số lần nhân vật xuất hiện)."""
    if n <= 0:
        return (0.0, 1.0)
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _kelly_fraction(p: float, mult: float, cap: float = 0.05, frac: float = 0.25) -> float:
    """Tỷ lệ vốn nên cược theo Kelly PHẦN (mặc định ¼-Kelly), có TRẦN `cap`.

    Kelly đầy đủ f* = (p·mult − 1) / (mult − 1) = (EV−1)/(net odds). Kelly đầy đủ
    dao động rất mạnh (dễ cụt vốn khi p ước lượng sai) nên nhân ¼ và chặn trần 5%
    vốn. Trả 0 nếu không có lợi thế (EV ≤ 1) hoặc bội ≤ 1."""
    if mult <= 1:
        return 0.0
    full = (p * mult - 1) / (mult - 1)
    if full <= 0:
        return 0.0
    return min(full * frac, cap)


def _build_result(monsters, teacher, probs, method, sample_count, details, message) -> dict:
    odds_map = {m["name"]: m["multiplier"] for m in monsters}
    odds_map[teacher["name"]] = teacher["multiplier"]

    # Best probability
    best_name = max(probs, key=probs.get)

    # Best expected value = prob * odds
    ev_map = {name: probs[name] * odds_map.get(name, 1.0) for name in probs}
    best_ev_name = max(ev_map, key=ev_map.get)

    # Độ tin cậy: theo số mẫu của TẦNG BỘI (tầng quyết định chính của ước lượng —
    # xem odds_appeared trong details). Nhân vật mỏng mẫu nhất (thường là thầy ở
    # giá trị bội cụ thể, mỗi giá trị chỉ ~10-30 trận) quyết định mức tin cậy.
    if method == "multiplier":
        confidence = "thap"
    else:
        odds_apps = [details.get(n, {}).get("odds_appeared", 0) for n in probs]
        min_odds_app = min(odds_apps) if odds_apps else 0
        if min_odds_app >= 50 and sample_count >= 300:
            confidence = "cao"
        elif min_odds_app >= 15 and sample_count >= 100:
            confidence = "trung binh"
        else:
            confidence = "thap"

    # Khoảng cách xác suất giữa hạng 1 và hạng 2 (gap nhỏ = khó phân định).
    sorted_p = sorted(probs.values(), reverse=True)
    top_gap = (sorted_p[0] - sorted_p[1]) if len(sorted_p) >= 2 else 0.0

    # Khoảng bất định + mức cược gợi ý cho kèo EV cao nhất.
    ev_mult = odds_map.get(best_ev_name, 1.0)
    ev_p = probs[best_ev_name]
    # Số mẫu nền cho ước lượng của con này (số lần nó từng xuất hiện).
    ev_n = details.get(best_ev_name, {}).get("appeared", 0) or 0
    p_lo, p_hi = _wilson_ci(ev_p, ev_n) if ev_n > 0 else (None, None)
    kelly = _kelly_fraction(ev_p, ev_mult)

    # ── Cổng TIN CẬY = chính sách cược tối ưu (backtest 2026-07-20) ──────────
    # Backtest walk-forward (276 trận) cho thấy: chỉ cần cược MỌI yêu quái mang
    # GIÁ TRỊ BỘI đã chứng minh +EV (tối đa 2/trận, favorite trước) là đạt tổng
    # lợi cao nhất & bền nhất — ROI ~+60→83%, CI luôn dương. Đây HƠN HẲN việc
    # đi theo EV mô hình (chỉ +32%, và kèo Thầy mô hình chọn thắng 1/50 = bẫy).
    #
    # Cổng data-driven, KHÔNG fit theo ROI (tránh winner's curse): cận DƯỚI CI
    # Wilson của win-rate tầng bội đó × bội. Tham số nguyên tắc, không dò:
    #   - LOẠI THẦY khỏi cổng (cấu trúc): Thầy là kèo xổ số 14–26x, thắng ~5%,
    #     MỌI tầng bội của Thầy có wr_lo×bội ≤ 0.96 (<1) — không bao giờ là kèo
    #     kỷ luật. Loại luôn để chặn rò khi 1 tầng Thầy trúng may lúc mẫu ít.
    #   - n_tier ≥ 20: đủ mẫu để tin, giảm rò tầng bội cao trúng may mẫu nhỏ.
    #   - biên 1.15: tách RÕ 2 edge thật (bội 5 = 1.48, bội 9 = 1.25) khỏi tầng
    #     hòa vốn (bội 4 = 1.01). Không nhận tầng chỉ nhỉnh hơn hòa vốn.
    # Trên data hiện tại chỉ bội 5 & 9 qua cổng; tự nới/siết khi data đổi.
    TRUSTED_MIN_N = 20
    TRUSTED_MARGIN = 1.15
    trusted = {}
    for name in probs:
        if name == teacher["name"]:
            trusted[name] = False  # Thầy không bao giờ là kèo kỷ luật.
            continue
        d = details.get(name, {})
        n_tier = d.get("odds_appeared", 0) or 0
        wr = d.get("odds_win_rate")
        o = odds_map.get(name, 1.0)
        if n_tier >= TRUSTED_MIN_N and wr is not None:
            wr_lo, _ = _wilson_ci(wr, n_tier)
            trusted[name] = (wr_lo * o) > TRUSTED_MARGIN
        else:
            trusted[name] = False

    def _bet_entry(name):
        m = odds_map.get(name, 1.0)
        p = probs[name]
        return {"name": name, "multiplier": m, "probability": p,
                "expected_value": p * m, "stake_fraction": _kelly_fraction(p, m)}

    # ── Lựa chọn 2 — KỶ LUẬT (bội 5 & 9): favorite trước (bội thấp trước) —
    # data cho thấy cược 2 favorite thắng ~72% (variance thấp).
    trusted_names = sorted([n for n in probs if trusted[n]], key=lambda n: odds_map[n])
    bet_pair = [_bet_entry(n) for n in trusted_names[:2]]

    # ── THAM KHẢO — MÔ HÌNH EV nghĩ gì (KHÔNG phải khuyến nghị cược) ─────────
    # Backtest cho thấy đi theo EV mô hình kém hơn kỷ luật bội 5&9 (ROI +32% vs
    # +60%), và khi mô hình xếp THẦY EV cao nhất thì gần như luôn thua (1/50 —
    # bội 14–26 variance cực lớn). Nên ở đây CHỈ hiển thị 2 yêu quái EV cao nhất
    # để tham khảo, KHÔNG bao giờ gợi ý cược Thầy. Nếu EV cao nhất toàn cục là
    # Thầy thì bật cờ cảnh báo để UI nhắc "bỏ / theo kỷ luật".
    teacher_name = teacher["name"]
    ranked = sorted(probs, key=lambda n: ev_map[n], reverse=True)
    model_top_is_teacher = bool(ranked and ranked[0] == teacher_name)
    top_monsters = [n for n in ranked if n != teacher_name][:2]
    model_pair = [_bet_entry(n) for n in top_monsters]

    return {
        "method": method,
        "sample_count": sample_count,
        "confidence": confidence,
        "top_gap": top_gap,
        "probabilities": probs,
        "details": details,
        "recommendation": {
            "name": best_name,
            "multiplier": odds_map.get(best_name, 1.0),
            "probability": probs[best_name],
        },
        "best_value": {
            "name": best_ev_name,
            "multiplier": ev_mult,
            "probability": ev_p,
            "expected_value": ev_map[best_ev_name],
            # Khoảng khả dĩ (Wilson) cho xác suất & EV — None nếu con này chưa
            # từng xuất hiện (không có mẫu nền để ước lượng độ rộng).
            "prob_low": p_lo,
            "prob_high": p_hi,
            "ev_low": (p_lo * ev_mult) if p_lo is not None else None,
            "ev_high": (p_hi * ev_mult) if p_hi is not None else None,
            # Mức cược gợi ý (¼-Kelly, trần 5% vốn); 0 nếu không có lợi thế.
            "stake_fraction": kelly,
        },
        # KHUYẾN NGHỊ CHÍNH — kỷ luật bội 5&9 (chính sách tổng-lợi tối ưu),
        # favorite trước, tối đa 2 con. Rỗng = nên BỎ TRẬN.
        "bet_pair": bet_pair,
        # THAM KHẢO — 2 yêu quái EV mô hình cao nhất (không phải khuyến nghị).
        "model_pair": model_pair,
        # Cờ: EV cao nhất toàn cục rơi vào Thầy (kèo bẫy) -> UI cảnh báo bỏ.
        "model_top_is_teacher": model_top_is_teacher,
        "trusted": trusted,
        "message": message,
    }


def format_prediction_text(pred: dict, monsters: list[dict], teacher: dict) -> str:
    """Text markdown cho Telegram."""
    lines = ["📊 *DỰ ĐOÁN KẾT QUẢ*\n"]

    method_labels = {
        "individual": "📉 Thống kê cá nhân",
        "multiplier": "⚖️ Ước tính theo odds",
    }
    lines.append(method_labels.get(pred["method"], pred["method"]))

    conf_label = {
        "cao": "🟢 Tin cậy: cao",
        "trung binh": "🟡 Tin cậy: trung bình",
        "thap": "🔴 Tin cậy: thấp",
    }.get(pred.get("confidence", "thap"), "")
    if pred["sample_count"] > 0:
        lines.append(f"🗂 Mẫu: {pred['sample_count']} trận  |  {conf_label}\n")
    else:
        lines.append(f"{conf_label}\n")

    lines.append("─── Xếp hạng theo EV (kỳ vọng) ───")
    probs = pred["probabilities"]
    odds_map = {m["name"]: m["multiplier"] for m in monsters}
    odds_map[teacher["name"]] = teacher["multiplier"]

    # Ưu tiên EV: sắp xếp từ kỳ vọng cao → thấp (EV = xác suất × bội).
    sorted_chars = sorted(
        probs.items(),
        key=lambda x: x[1] * odds_map.get(x[0], 1.0),
        reverse=True,
    )
    for rank, (name, prob) in enumerate(sorted_chars, 1):
        odds = odds_map.get(name, 1.0)
        ev = prob * odds
        bar = "█" * int(prob * 20)
        detail = pred["details"].get(name, {})
        appeared = detail.get("appeared", 0)
        won = detail.get("won", 0)
        is_teacher = name == teacher["name"]
        prefix = "👨‍🏫" if is_teacher else "👹"
        hist_str = f" [{won}/{appeared}]" if appeared > 0 else " [mới]"
        owr = detail.get("odds_win_rate")
        odds_str = f" · bội này về {owr*100:.0f}%" if owr is not None else ""
        # Tín hiệu tên×bội: "con NÀY tại bội NÀY về X% (n trận)".
        no_app = detail.get("name_odds_appeared", 0)
        no_wr = detail.get("name_odds_win_rate")
        no_str = f" · tên×bội {no_wr*100:.0f}% [{detail.get('name_odds_won',0)}/{no_app}]" if no_app > 0 else ""
        lines.append(
            f"{rank}. {prefix} {display_name(name)} ({odds:g}x): "
            f"EV *{ev:.2f}* · {prob*100:.1f}%{hist_str}{odds_str}{no_str} {bar}"
        )

    rec = pred["recommendation"]
    model_pair = pred.get("model_pair", [])
    disc_pair = pred.get("bet_pair", [])

    def _bet_line(b):
        stake = b.get("stake_fraction", 0) or 0
        stake_str = (f" · cược ~{stake*100:.1f}% vốn" if stake > 0 else " · cược nhẹ")
        return (f"   • *{display_name(b['name'])}* ({b['multiplier']:g}x) — "
                f"EV {b['expected_value']:.2f}, thắng ~{b['probability']*100:.0f}%{stake_str}")

    # ✅ KHUYẾN NGHỊ CHÍNH — kỷ luật bội 5 & 9 (chính sách tổng-lợi tối ưu).
    lines.append("\n─── ✅ KHUYẾN NGHỊ: Kỷ luật bội 5 & 9 ───")
    if disc_pair:
        for b in disc_pair:
            lines.append(_bet_line(b))
        if len(disc_pair) == 2:
            lines.append("   _Cược CẢ 2 favorite: thắng nếu 1 trong 2 về (~72%), ít chuỗi thua._")
        else:
            lines.append("   _Chỉ 1 con bội 5/9 đủ tin ở trận này._")
    else:
        lines.append("   🚫 _Không có con bội 5/9 đủ tin → NÊN BỎ TRẬN (để vốn trống hơn cược ép)._")

    # ℹ️ THAM KHẢO — EV mô hình (kém hơn kỷ luật; KHÔNG cược Thầy).
    lines.append("─── ℹ️ Tham khảo: mô hình EV nghĩ gì ───")
    for b in model_pair:
        lines.append(_bet_line(b))
    if pred.get("model_top_is_teacher"):
        lines.append("   ⚠️ _EV cao nhất rơi vào THẦY (bội cao) — lịch sử thua nặng, đừng đuổi._")
    lines.append("   _Chỉ tham khảo: đi theo EV mô hình dài hạn LÃI ÍT HƠN kỷ luật bội 5&9._")

    lines.append(
        f"🛡 _Xác suất cao nhất (tham khảo): {display_name(rec['name'])} "
        f"{rec['probability']*100:.0f}%._"
    )

    lines.append(f"\n💬 _{pred['message']}_")

    if pred.get("confidence") == "thap" or pred.get("top_gap", 1) < 0.06:
        lines.append(
            "\n⚠️ _Dữ liệu còn ít / các lựa chọn sát nhau — kết quả game có yếu "
            "tố ngẫu nhiên cao, hãy xem đây là tham khảo, đừng cược nặng._"
        )
    return "\n".join(lines)
