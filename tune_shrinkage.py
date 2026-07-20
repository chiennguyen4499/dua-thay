"""Quet tham so shrinkage de tim K toi uu theo LOGLOSS walk-forward.

Chay:  python tune_shrinkage.py
  - quet luoi (ind_k, odds_k, name_odds_k)
  - in bang ket qua, danh dau combo tot nhat
  - GHI combo tot nhat ra tuned_params.json (config.py se tu doc file nay)

Tieu chi chon: LOGLOSS walk-forward THAP NHAT (calibration tot nhat).
ROI van duoc tinh va ghi kem BOOTSTRAP CI 95% nhung CHI de tham khao.

Vi sao khong chon theo ROI (doi 2026-07-20): ROI moi cuoc co do lech chuan
~3 don vi -> sai so chuan cua ROI tren ~270 tran la ~0.2, LON HON chenh lech
ROI giua cac combo trong luoi (sd ~0.11). Chon max ROI cua 80 combo la chon
NHIEU (winner's curse): bo (40/2/10) duoc chon theo ROI cho logloss 1.4707,
TE hon ca odds-only (1.4552); moi combo name_odds=0 deu tot hon (~1.442).
Logloss on dinh hon nhieu va la thuoc do calibration truc tiep.

"Auto-ready": tang ten x boi (name_odds_k) van nam trong luoi quet. Khi data
du day den muc o (ten x boi) co tin hieu that, logloss cua name_odds_k>0 se
tu thap hon va tang nay TU DONG duoc bat. Khong can sua tay.
"""
import os, math, json, random, importlib

PARAMS_FILE = os.path.join(os.path.dirname(__file__), "tuned_params.json")

# Lưới quét (cấp module để web_app tính được tổng số tổ hợp cho thanh tiến độ).
# Bao gồm name_odds_k=0 (tắt) làm baseline cho tầng tên×bội.
IND_KS = (15, 20, 25, 30, 40)
ODDS_KS = (2, 3, 4, 6)
NAME_ODDS_KS = (0, 10, 20, 40)
TOTAL_COMBOS = len(IND_KS) * len(ODDS_KS) * len(NAME_ODDS_KS)


def eval_combo(ind_k, odds_k, name_odds_k, real, min_history=10):
    os.environ["ODDS_CALIB_STRENGTH"] = str(odds_k)
    os.environ["NAME_ODDS_STRENGTH"] = str(name_odds_k)
    os.environ["INDIVIDUAL_PRIOR_STRENGTH"] = str(ind_k)
    # Tranh tuned_params.json ghi de luc dang quet (xem config.py).
    os.environ["IGNORE_TUNED_PARAMS"] = "1"
    import config; importlib.reload(config)
    import predictor; importlib.reload(predictor)

    # Walk-forward THUAN PYTHON: tran thu i du doan bang real[:i] (chi qua khu).
    # Khong con DB tam / trao DATABASE_PATH -> nhanh (vai giay ca luoi) va het
    # han hazard ro du lieu replay vao DB that.
    ll = 0.0; n = 0; top1 = 0; brier = 0.0
    gains = []  # lai/lo tung cuoc (de tinh ROI + bootstrap CI)
    for i, row in enumerate(real):
        if i < min_history:
            continue
        monsters = [{"name": row[f"monster{i2}_name"], "multiplier": row[f"monster{i2}_multiplier"]} for i2 in range(1, 5)]
        teacher = {"name": row["teacher_name"], "multiplier": row["teacher_multiplier"]}
        wname = teacher["name"] if row["winner"] == "teacher" else row[f"{row['winner']}_name"]
        P = predictor.predict(monsters, teacher, real[:i])["probabilities"]
        pw = max(P.get(wname, 1e-9), 1e-9)
        ll += -math.log(pw); n += 1
        if max(P, key=P.get) == wname:
            top1 += 1
        for nm, pr in P.items():
            brier += (pr - (1.0 if nm == wname else 0.0)) ** 2
        # ROI: cuoc 1 don vi vao nhan vat co EV du doan cao nhat.
        odds_map_round = {c["name"]: c["multiplier"] for c in monsters + [teacher]}
        pick = max(P, key=lambda nm: P[nm] * odds_map_round.get(nm, 1.0))
        gains.append(odds_map_round[pick] - 1 if pick == wname else -1.0)
    return ll / n, top1, n, brier / n, sum(gains) / n, gains


def bootstrap_roi_ci(gains, b=4000, seed=7):
    """CI 95% cua ROI trung binh bang bootstrap — ROI moi cuoc lech chuan ~3
    don vi nen diem uoc luong don le rat nhieu; CI cho biet do rong that."""
    if not gains:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(gains)
    means = sorted(
        sum(gains[rng.randrange(n)] for _ in range(n)) / n for _ in range(b)
    )
    return (means[int(0.025 * b)], means[int(0.975 * b)])


if __name__ == "__main__":
    import database as _db_once
    # Lay lich su THẬT 1 lan duy nhat (khong doi theo ind_k/odds_k/name_odds_k)
    # thay vi trong tung eval_combo() — tranh goi lai 80 lan (1 lan/to hop luoi
    # quet), vi khi da cau hinh Turso moi lan la 1 round-trip mang.
    real_rounds = sorted(_db_once.get_all_rounds_with_winner(), key=lambda r: r["id"])

    print(f"{'indK':>5}{'oddsK':>7}{'nameOddsK':>10}{'logloss':>10}{'brier':>9}{'top1':>8}{'roi_ev':>9}", flush=True)
    results = []
    n_total = 0
    for ik in IND_KS:
        for ok in ODDS_KS:
            for nok in NAME_ODDS_KS:
                ll, t, n, br, roi, gains = eval_combo(ik, ok, nok, real_rounds)
                n_total = n
                results.append((ll, br, t, ik, ok, nok, roi, gains))
                print(f"{ik:>5}{ok:>7}{nok:>10}{ll:>10.4f}{br:>9.4f}{t:>5}/{n}{roi:>+9.4f}", flush=True)

    # Tieu chi chon: LOGLOSS thap nhat (calibration). ROI chi de tham khao —
    # xem docstring dau file vi sao khong chon theo ROI (winner's curse).
    results.sort(key=lambda x: x[0])
    best_ll, best_br, best_t, ik, ok, nok, best_roi, best_gains = results[0]
    roi_lo, roi_hi = bootstrap_roi_ci(best_gains)
    print("\n>>> Combo tot nhat (LOGLOSS thap nhat):")
    print(f"    INDIVIDUAL_PRIOR_STRENGTH={ik}  ODDS_CALIB_STRENGTH={ok}  NAME_ODDS_STRENGTH={nok}")
    print(f"    logloss={best_ll:.4f}  brier={best_br:.4f}  top1={best_t}/{n_total}")
    print(f"    roi_ev={best_roi:+.4f}  CI95 [{roi_lo:+.4f}, {roi_hi:+.4f}]  (tham khao, KHONG phai tieu chi chon)")
    if nok > 0:
        print("    -> Tang 'ten x boi' DA du mau de co loi cho calibration. Da bat tu dong.")
    else:
        print("    -> Tang 'ten x boi' chua co loi (name_odds_k=0). Them data roi chay lai.")

    # Thong tin bo: top 5 theo logloss + top 5 theo ROI de doi chieu.
    print("\n--- Top 5 combo theo LOGLOSS (tieu chi chon) ---")
    print(f"{'indK':>5}{'oddsK':>7}{'nameOddsK':>10}{'logloss':>10}{'roi_ev':>9}{'top1':>8}")
    for r in results[:5]:
        print(f"{r[3]:>5}{r[4]:>7}{r[5]:>10}{r[0]:>10.4f}{r[6]:>+9.4f}{r[2]:>5}/{n_total}")
    by_roi = sorted(results, key=lambda x: x[6], reverse=True)
    print("--- Top 5 combo theo ROI (chi tham khao, nhieu) ---")
    for r in by_roi[:5]:
        print(f"{r[3]:>5}{r[4]:>7}{r[5]:>10}{r[0]:>10.4f}{r[6]:>+9.4f}{r[2]:>5}/{n_total}")

    payload = {
        "INDIVIDUAL_PRIOR_STRENGTH": ik,
        "ODDS_CALIB_STRENGTH": ok,
        "NAME_ODDS_STRENGTH": nok,
        "_meta": {
            "rounds_evaluated": n_total,
            "roi_ev": round(best_roi, 4),
            "roi_ci95": [round(roi_lo, 4), round(roi_hi, 4)],
            "logloss": round(best_ll, 4),
            "brier": round(best_br, 4),
            "top1": best_t,
            "optimized_by": "logloss",
        },
    }
    with open(PARAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nDa ghi {PARAMS_FILE}. config.py se tu dong dung cac tham so nay.")

    # Ghi thêm vào bảng meta trên Turso (nguồn dùng chung PC<->Cloud) để cả bot
    # lẫn Web đều thấy bộ tham số mới, và để auto-retune biết mốc trận đã tune.
    try:
        import database as _db
        _db.set_meta("tuned_params", payload)
        # Mốc auto-retune tính theo TỔNG số trận có kết quả (không phải số trận
        # được đánh giá n_total = tổng - min_history).
        _db.set_meta("tuned_at_rounds", len(real_rounds))
        print("Da dong bo tuned_params vao Turso (bang meta).")
    except Exception as e:
        print(f"[WARN] Khong ghi duoc meta len Turso: {e}")
