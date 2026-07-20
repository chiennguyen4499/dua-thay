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

from database import (
    get_monster_stats_batch, get_teacher_stats,
    get_total_rounds_with_winner,
    get_monster_odds_winrate, get_teacher_odds_winrate,
    get_monster_name_odds_stats_batch, get_teacher_name_odds_stats,
)
from config import (
    INDIVIDUAL_PRIOR_STRENGTH,
    ODDS_CALIB_STRENGTH, NAME_ODDS_STRENGTH,
    canonical_name, display_name,
)


def _implied_prob(odds_dict: dict[str, float]) -> dict[str, float]:
    """Chuyển odds → xác suất implied (normalize 1/odds)."""
    inv = {name: 1.0 / max(o, 0.01) for name, o in odds_dict.items()}
    total = sum(inv.values())
    return {name: v / total for name, v in inv.items()}


def _hist_stats_for_chars(all_chars: list[dict], is_teacher_fn) -> dict[str, dict]:
    """Lấy stats lịch sử cho mỗi nhân vật — 1 query gộp cho tất cả yêu quái
    (thay vì 1 query/con) + 1 query cho thầy, tránh N+1 round-trip Turso."""
    monster_names = [c["name"] for c in all_chars if not is_teacher_fn(c)]
    result = get_monster_stats_batch(monster_names)
    for c in all_chars:
        if is_teacher_fn(c):
            result[c["name"]] = get_teacher_stats(c["name"])
    return result


def predict(monsters: list[dict], teacher: dict) -> dict:
    """
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
    # Chuẩn hóa tên về canonical để khớp với dữ liệu đã lưu trong DB.
    monsters = [{**m, "name": canonical_name(m["name"])} for m in monsters]
    teacher = {**teacher, "name": canonical_name(teacher["name"])}

    all_chars = monsters + [teacher]
    teacher_name = teacher["name"]

    odds_map = {c["name"]: c["multiplier"] for c in all_chars}
    q = _implied_prob(odds_map)  # odds-implied probabilities

    is_teacher = lambda c: c["name"] == teacher_name

    # ── Tầng 1: Thống kê cá nhân + blend với odds ─────────────
    total_db = get_total_rounds_with_winner()
    stats = _hist_stats_for_chars(all_chars, is_teacher)

    # Số lần xuất hiện ít nhất của bất kỳ nhân vật nào trong trận này
    min_appearances = min(s["appeared"] for s in stats.values())

    if total_db >= 10:
        probs, details = _from_individual(all_chars, stats, q, is_teacher, odds_map)
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


def _from_individual(all_chars, stats, q, is_teacher_fn, odds_map) -> tuple[dict, dict]:
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
    """
    # Bảng calibration GỘP theo giá trị bội (1 truy vấn mỗi loại, dùng chung).
    monster_odds_tbl = get_monster_odds_winrate()
    teacher_odds_tbl = get_teacher_odds_winrate()

    # Tầng 3 (tên × bội): 1 query gộp cho tất cả yêu quái thay vì 1 query/con.
    monster_name_odds = get_monster_name_odds_stats_batch(
        [c["name"] for c in all_chars if not is_teacher_fn(c)]
    )

    est = {}
    details = {}
    for c in all_chars:
        name = c["name"]
        o = int(round(odds_map[name]))
        is_t = is_teacher_fn(c)
        s = stats[name]

        # Tầng 1: win-rate gộp theo giá trị bội này, shrink về 1/odds.
        odds_tbl = teacher_odds_tbl if is_t else monster_odds_tbl
        ostat = odds_tbl.get(o, {"appeared": 0, "won": 0})
        p_odds = _shrink(ostat["won"], ostat["appeared"], q[name], ODDS_CALIB_STRENGTH)

        # Tầng 2: win-rate theo tên, shrink về tầng giá trị bội.
        p_name = _shrink(s["won"], s["appeared"], p_odds, INDIVIDUAL_PRIOR_STRENGTH)

        # Tầng 3: win-rate theo (tên × bội), shrink về tầng tên.
        if is_t:
            no = get_teacher_name_odds_stats(name, o)
        else:
            no = monster_name_odds.get((name, o), {"appeared": 0, "won": 0})
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
            "multiplier": odds_map.get(best_ev_name, 1.0),
            "probability": probs[best_ev_name],
            "expected_value": ev_map[best_ev_name],
        },
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
    ev = pred["best_value"]

    lines.append("\n─── Khuyến nghị (ưu tiên EV) ───")
    if ev["expected_value"] > 1.05:
        lines.append(
            f"💰 *Kèo giá trị*: {display_name(ev['name'])} ({ev['multiplier']:g}x) — "
            f"EV={ev['expected_value']:.2f}, thắng ~{ev['probability']*100:.1f}%"
        )
        lines.append("   _EV>1 = lời về dài hạn, nhưng thắng ít → variance cao, cược nhẹ._")
    else:
        lines.append(
            f"💰 _Không có kèo +EV nổi bật (EV tốt nhất chỉ {ev['expected_value']:.2f}). "
            f"Cân nhắc bỏ qua trận này._"
        )
    lines.append(
        f"🛡 Kèo an toàn (xác suất cao nhất): {display_name(rec['name'])} — "
        f"{rec['probability']*100:.1f}% _(ROI dài hạn thường âm)_"
    )

    lines.append(f"\n💬 _{pred['message']}_")

    if pred.get("confidence") == "thap" or pred.get("top_gap", 1) < 0.06:
        lines.append(
            "\n⚠️ _Dữ liệu còn ít / các lựa chọn sát nhau — kết quả game có yếu "
            "tố ngẫu nhiên cao, hãy xem đây là tham khảo, đừng cược nặng._"
        )
    return "\n".join(lines)
