"""
Kiem chung gia thuyet cuoc cua nguoi choi tren du lieu that.

Gia thuyet:
  A. Cau truc: moi tran luon co nhom boi to (>6) va boi nho (<5).
  B. Boi to that to (10-12) dang cuoc (underdog hay ve).
  C. Thay boi cao (18, 26) dang cuoc.

Thuoc do quan trong nhat = ROI: cuoc 1 don vi/tran.
  - Thang: nhan lai odds (lai = odds-1).  Thua: mat 1.
  - ROI = tong_lai / so_lan_cuoc.  ROI > 0 = co lai.
"""

import database as db

# Cache LOO nay LUU TRONG TURSO (bang meta), KHONG con file JSON local (2026-07-20).
# Ly do: Streamlit Cloud khong co dia ben nen file local loi thoi/lech giua PC va
# Cloud. Meta dung chung -> ca 2 noi thay cung ket qua. LOO gio ~1.2s (predictor
# la pure function) nen tinh lai cung re; cache chi de khoi tinh lai moi rerun.
# Het han khi SO TRAN hoac THAM SO TUNE doi.


def _current_params():
    """3 tham số shrinkage đang dùng — để biết cache có khớp mô hình hiện tại."""
    import config
    return [config.INDIVIDUAL_PRIOR_STRENGTH, config.ODDS_CALIB_STRENGTH,
            config.NAME_ODDS_STRENGTH]


def save_model_picks_cache(picks, n_rounds):
    """Ghi kết quả LOO (picks) + số trận + tham số vào bảng meta (Turso, dùng chung)."""
    try:
        db.set_meta("model_picks", {"n_rounds": n_rounds,
                                    "params": _current_params(), "picks": picks})
    except Exception:
        pass


def load_model_picks_cache(n_rounds):
    """Đọc cache LOO từ meta nếu CÒN HỢP LỆ (đúng số trận VÀ đúng tham số). Lỗi thời → None."""
    try:
        d = db.get_meta("model_picks")
        if d and d.get("n_rounds") == n_rounds and d.get("params") == _current_params():
            return d["picks"]
    except Exception:
        pass
    return None


def load_rounds():
    rows = db.get_all_rounds_with_winner()
    rounds = []
    for r in rows:
        monsters = [
            {"name": r[f"monster{i}_name"], "odds": r[f"monster{i}_multiplier"], "slot": f"monster{i}"}
            for i in range(1, 5)
        ]
        teacher = {"name": r["teacher_name"], "odds": r["teacher_multiplier"], "slot": "teacher"}
        rounds.append({"monsters": monsters, "teacher": teacher, "winner": r["winner"]})
    return rounds


# ──────────────────────────────────────────────────────────────
# Hàm COMPUTE (trả về dữ liệu) — dùng chung cho CLI lẫn web UI.
# ──────────────────────────────────────────────────────────────

def compute_odds_winrate(rounds):
    """Theo từng giá trị bội (yêu quái): xuất hiện / thắng / winrate / implied / EV / ROI."""
    from collections import defaultdict
    appear, win = defaultdict(int), defaultdict(int)
    for rd in rounds:
        for m in rd["monsters"]:
            o = int(round(m["odds"]))
            appear[o] += 1
            if rd["winner"] == m["slot"]:
                win[o] += 1
    out = []
    for o in sorted(appear):
        a, w = appear[o], win[o]
        wr = w / a
        out.append({"odds": o, "appeared": a, "won": w, "win_rate": wr,
                    "implied": 1 / o, "ev": wr * o, "roi": wr * o - 1})
    return out


def compute_teacher_by_odds(rounds):
    """Theo từng giá trị bội của Thầy: xuất hiện / thoát / rate / implied / EV / ROI."""
    from collections import defaultdict
    appear, esc = defaultdict(int), defaultdict(int)
    for rd in rounds:
        o = int(round(rd["teacher"]["odds"]))
        appear[o] += 1
        if rd["winner"] == "teacher":
            esc[o] += 1
    out = []
    for o in sorted(appear):
        a, w = appear[o], esc[o]
        wr = w / a
        out.append({"odds": o, "appeared": a, "escaped": w, "escape_rate": wr,
                    "implied": 1 / o, "ev": wr * o, "roi": wr * o - 1})
    return out


def _run_strategy(rounds, pick_fn):
    """Chạy 1 chiến lược: cược 1 đơn vị/trận. pick_fn(rd) trả competitor hoặc None (bỏ qua)."""
    bets = wins = 0
    profit = 0.0
    for rd in rounds:
        cand = pick_fn(rd)
        if cand is None:
            continue
        bets += 1
        if rd["winner"] == cand["slot"]:
            wins += 1
            profit += cand["odds"] - 1
        else:
            profit -= 1
    return {
        "bets": bets, "wins": wins,
        "hit_rate": (wins / bets) if bets else 0.0,
        "roi": (profit / bets) if bets else 0.0,
        "profit": profit,
    }


def _pick_max_if(thr):
    def f(rd):
        c = max(rd["monsters"], key=lambda m: m["odds"])
        return c if c["odds"] >= thr else None
    return f


def _pick_teacher_if(thr):
    def f(rd):
        return rd["teacher"] if rd["teacher"]["odds"] >= thr else None
    return f


# (nhãn, hàm chọn) — danh sách chiến lược để so ROI
STRATEGIES = [
    ("Luôn cược con bội NHỎ nhất", lambda rd: min(rd["monsters"], key=lambda m: m["odds"])),
    ("Luôn cược con bội TO nhất",  lambda rd: max(rd["monsters"], key=lambda m: m["odds"])),
    ("Cược con bội to nhất, chỉ khi odds≥8", _pick_max_if(8)),
    ("Luôn cược THẦY",             lambda rd: rd["teacher"]),
    ("Cược THẦY khi odds≥18",      _pick_teacher_if(18)),
    ("Cược THẦY khi odds≥20",      _pick_teacher_if(20)),
]


def compute_strategies(rounds):
    """Trả về list dict: {label, bets, wins, hit_rate, roi, profit} cho mỗi chiến lược."""
    return [{"label": lbl, **_run_strategy(rounds, fn)} for lbl, fn in STRATEGIES]


def _bootstrap_ci(gains, b=2000, seed=13):
    """CI 95% cho ROI trung bình bằng bootstrap (ROI mỗi cược nhiễu mạnh)."""
    import random
    if not gains:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(gains)
    means = sorted(sum(gains[rng.randrange(n)] for _ in range(n)) / n for _ in range(b))
    return (means[int(0.025 * b)], means[int(0.975 * b)])


def compute_heuristic_summary(rounds, min_appear=20):
    """Tự TÍNH các kèo heuristic theo BỘI từ data sống (thay cho ghi chú viết tay
    hay bị lỗi thời). Trả dict:
      - `odds_edges`: mỗi giá trị bội yêu quái đủ mẫu → roi + bootstrap CI, xếp
        theo CẬN DƯỚI CI (ưu tiên kèo bền, phạt ăn may); `real=True` nếu CI>0.
      - `teacher`: kèo "Thầy ≥18 → Thầy" → roi + CI.
      - `n_rounds`.
    Chủ đích CHỈ theo bội (không theo tên): 18 tên = nhiều so sánh → tên đa phần
    chỉ ăn ké hiệu ứng bội, dễ ngộ nhận. Tín hiệu bội mới là thứ đáng tin."""
    from collections import defaultdict
    gains = defaultdict(list)
    for rd in rounds:
        for m in rd["monsters"]:
            o = int(round(m["odds"]))
            gains[o].append((o - 1) if rd["winner"] == m["slot"] else -1.0)
    odds_edges = []
    for o, g in gains.items():
        if len(g) < min_appear:
            continue
        roi = sum(g) / len(g)
        lo, hi = _bootstrap_ci(g)
        wins = sum(1 for x in g if x > 0)
        odds_edges.append({"odds": o, "n": len(g), "won": wins,
                           "win_rate": wins / len(g), "roi": roi,
                           "ci": (lo, hi), "real": lo > 0})
    odds_edges.sort(key=lambda d: d["ci"][0], reverse=True)

    tg = []
    for rd in rounds:
        if rd["teacher"]["odds"] >= 18:
            tg.append((rd["teacher"]["odds"] - 1) if rd["winner"] == "teacher" else -1.0)
    teacher = None
    if tg:
        lo, hi = _bootstrap_ci(tg)
        teacher = {"thr": 18, "n": len(tg), "won": sum(1 for x in tg if x > 0),
                   "win_rate": sum(1 for x in tg if x > 0) / len(tg),
                   "roi": sum(tg) / len(tg), "ci": (lo, hi), "real": lo > 0}
    return {"odds_edges": odds_edges, "teacher": teacher, "n_rounds": len(rounds)}


def compute_model_picks(min_history=10):
    """LEAVE-ONE-OUT (LOO): mỗi trận được dự đoán bằng TẤT CẢ trận khác, chỉ bỏ
    đúng trận đang xét. Trả về lựa chọn của mô hình cho mỗi trận.

    Tách riêng và KHÔNG phụ thuộc ngưỡng EV để web cache theo số trận — kéo slider
    ngưỡng không phải chạy lại. Lọc/tổng hợp theo ngưỡng bằng `aggregate_model_strategies`.

    Vì loại đúng trận đang xét nên KHÔNG có "biết trước đáp án" (không bị thổi
    phồng như in-sample). Đánh giá mọi trận bằng ~N-1 trận còn lại → phản ánh chất
    lượng mô hình với lượng data HIỆN TẠI (khác walk-forward dùng-trận-quá-khứ,
    vốn bị kéo xuống bởi các dự đoán đầu lúc data mỏng). Lưu ý: LOO dùng cả trận
    tương lai để ước lượng, nên về lý thuyết hơi lạc quan so với lúc chơi thật.

    Từ 2026-07-20 chạy THUẦN PYTHON trên list trận (predictor là pure function):
    mỗi trận predict bằng `real[:i] + real[i+1:]`. KHÔNG còn DB tạm / tráo
    `db.DATABASE_PATH` / lock / dọn rác → hết hazard rò dữ liệu, và nhanh hơn
    nhiều (không ghi/xoá hàng trăm lượt vào DB).

    `min_history`: chỉ đánh giá khi phần lịch sử còn lại ≥ ngần này trận.

    Trả về list dict: {"best_ev": float, "gain": float, "won": bool}.
    """
    import predictor

    real = sorted(db.get_all_rounds_with_winner(), key=lambda r: r["id"])

    def _unpack(row):
        monsters = [{"name": row[f"monster{i}_name"], "multiplier": row[f"monster{i}_multiplier"]} for i in range(1, 5)]
        teacher = {"name": row["teacher_name"], "multiplier": row["teacher_multiplier"]}
        wname = teacher["name"] if row["winner"] == "teacher" else row[f"{row['winner']}_name"]
        return monsters, teacher, wname

    picks = []
    for i, row in enumerate(real):
        history = real[:i] + real[i + 1:]  # bỏ ĐÚNG trận đang xét
        if len(history) < min_history:
            continue
        monsters, teacher, wname = _unpack(row)
        P = predictor.predict(monsters, teacher, history)["probabilities"]
        odds_map = {c["name"]: c["multiplier"] for c in monsters + [teacher]}
        pick = max(P, key=lambda nm: P[nm] * odds_map.get(nm, 1.0))
        won = pick == wname
        picks.append({
            "best_ev": P[pick] * odds_map[pick],
            "gain": (odds_map[pick] - 1) if won else -1,
            "won": won,
        })
    return picks


def aggregate_model_strategies(picks, ev_threshold=1.05):
    """Tổng hợp ROI từ `picks` (compute_model_picks) — RẺ, lọc theo ngưỡng EV.

    Trả về 2 dict (cùng schema _run_strategy + 'label'):
      - luôn cược con EV cao nhất;  - chỉ cược khi EV tốt nhất ≥ ngưỡng.
    """
    def _agg(rows, label):
        b = len(rows)
        wins = sum(1 for r in rows if r["won"])
        profit = sum(r["gain"] for r in rows)
        return {"label": label, "bets": b, "wins": wins,
                "hit_rate": (wins / b) if b else 0.0,
                "roi": (profit / b) if b else 0.0, "profit": profit}

    return [
        _agg(picks, "Theo mô hình (EV cao nhất, leave-one-out)"),
        _agg([r for r in picks if r["best_ev"] >= ev_threshold],
             f"Theo mô hình, chỉ khi EV ≥ {ev_threshold:g}"),
    ]


def compute_model_strategies(ev_threshold=1.05, min_history=10):
    """Tiện ích (CLI/test): replay rồi tổng hợp trong 1 lần gọi.

    Web nên gọi `compute_model_picks` (cache) + `aggregate_model_strategies` riêng
    để kéo slider ngưỡng không phải replay lại.
    """
    return aggregate_model_strategies(compute_model_picks(min_history), ev_threshold)


def section(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def hyp_A_structure(rounds):
    section("A. CAU TRUC TRAN — bao nhieu yeu quai boi >6 va <5?")
    from collections import Counter
    big = Counter()   # so con co odds > 6
    small = Counter()
    for rd in rounds:
        odds = [m["odds"] for m in rd["monsters"]]
        big[sum(o > 6 for o in odds)] += 1
        small[sum(o < 5 for o in odds)] += 1
    print("So con BOI TO (>6) moi tran:  ", dict(sorted(big.items())))
    print("So con BOI NHO (<5) moi tran: ", dict(sorted(small.items())))
    n = len(rounds)
    print(f"=> 'Luon co 2 to / 2 nho' dung khong? "
          f"2-to: {big[2]}/{n} tran, 2-nho: {small[2]}/{n} tran")


def winrate_by_odds(rounds):
    section("B1. TY LE THANG THEO TUNG GIA TRI BOI (tat ca yeu quai)")
    from collections import defaultdict
    appear = defaultdict(int)
    win = defaultdict(int)
    for rd in rounds:
        for m in rd["monsters"]:
            o = int(round(m["odds"]))
            appear[o] += 1
            if rd["winner"] == m["slot"]:
                win[o] += 1
    print(f"{'odds':>5}{'xuat hien':>11}{'thang':>7}{'winrate':>9}{'implied':>9}{'EV':>7}{'ROI/cuoc':>10}")
    for o in sorted(appear):
        a, w = appear[o], win[o]
        wr = w / a
        implied = 1 / o
        ev = wr * o
        roi = ev - 1
        flag = "  <== +EV" if roi > 0.05 else ""
        print(f"{o:>5}{a:>11}{w:>7}{wr*100:>8.0f}%{implied*100:>8.0f}%{ev:>7.2f}{roi*100:>9.0f}%{flag}")


def hyp_B_bigodds(rounds):
    section("B2. CHIEN LUOC CUOC CON BOI TO NHAT")
    # Trong moi tran, chon con co odds cao nhat
    def strat(filter_fn, label):
        bets = wins = 0
        profit = 0.0
        for rd in rounds:
            cand = max(rd["monsters"], key=lambda m: m["odds"])
            if not filter_fn(cand["odds"]):
                continue
            bets += 1
            if rd["winner"] == cand["slot"]:
                wins += 1
                profit += cand["odds"] - 1
            else:
                profit -= 1
        if bets == 0:
            print(f"{label:35s}: khong co tran nao thoa")
            return
        print(f"{label:35s}: {bets:3d} cuoc, thang {wins:2d} ({wins/bets*100:.0f}%), "
              f"ROI {profit/bets*100:+.0f}%  (lai {profit:+.1f} don vi)")

    strat(lambda o: True, "Con boi to nhat (bat ky)")
    strat(lambda o: o >= 10, "Con boi to nhat, chi khi odds>=10")
    strat(lambda o: o in (10, 11, 12), "Con boi to nhat, odds in 10-12")
    strat(lambda o: o >= 8, "Con boi to nhat, chi khi odds>=8")


def hyp_B_baselines(rounds):
    section("B3. SO SANH BASELINE")
    def strat(pick_fn, label):
        bets = wins = 0
        profit = 0.0
        for rd in rounds:
            cand = pick_fn(rd)
            bets += 1
            if rd["winner"] == cand["slot"]:
                wins += 1
                profit += cand["odds"] - 1
            else:
                profit -= 1
        print(f"{label:35s}: {bets:3d} cuoc, thang {wins:2d} ({wins/bets*100:.0f}%), "
              f"ROI {profit/bets*100:+.0f}%  (lai {profit:+.1f} don vi)")

    strat(lambda rd: min(rd["monsters"], key=lambda m: m["odds"]), "Luon cuoc con BOI NHO nhat")
    strat(lambda rd: max(rd["monsters"], key=lambda m: m["odds"]), "Luon cuoc con BOI TO nhat")
    strat(lambda rd: rd["teacher"], "Luon cuoc THAY")


def hyp_C_teacher(rounds):
    section("C. THAY THEO GIA TRI BOI")
    from collections import defaultdict
    appear = defaultdict(int)
    esc = defaultdict(int)
    for rd in rounds:
        o = int(round(rd["teacher"]["odds"]))
        appear[o] += 1
        if rd["winner"] == "teacher":
            esc[o] += 1
    print(f"{'odds':>5}{'xuat hien':>11}{'thoat':>7}{'rate':>8}{'implied':>9}{'EV':>7}{'ROI/cuoc':>10}")
    for o in sorted(appear):
        a, w = appear[o], esc[o]
        wr = w / a
        ev = wr * o
        roi = ev - 1
        flag = "  <== +EV" if roi > 0.05 else ""
        print(f"{o:>5}{a:>11}{w:>7}{wr*100:>7.0f}%{1/o*100:>8.0f}%{ev:>7.2f}{roi*100:>9.0f}%{flag}")

    # Chien luoc: cuoc thay khi odds cao
    print()
    for thr in (18, 20, 26):
        bets = wins = 0
        profit = 0.0
        for rd in rounds:
            if rd["teacher"]["odds"] >= thr:
                bets += 1
                if rd["winner"] == "teacher":
                    wins += 1
                    profit += rd["teacher"]["odds"] - 1
                else:
                    profit -= 1
        if bets:
            print(f"Cuoc THAY khi odds>={thr:2d}: {bets:3d} cuoc, thoat {wins} ({wins/bets*100:.0f}%), "
                  f"ROI {profit/bets*100:+.0f}%  (lai {profit:+.1f})")
        else:
            print(f"Cuoc THAY khi odds>={thr:2d}: khong co tran nao")


if __name__ == "__main__":
    rounds = load_rounds()
    print(f"Tong {len(rounds)} tran co ket qua.")
    hyp_A_structure(rounds)
    winrate_by_odds(rounds)
    hyp_B_bigodds(rounds)
    hyp_B_baselines(rounds)
    hyp_C_teacher(rounds)
