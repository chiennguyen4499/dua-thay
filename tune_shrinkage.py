"""Quet tham so shrinkage de tim K toi uu theo ROI walk-forward (EV toi da).

Chay:  python tune_shrinkage.py
  - quet luoi (ind_k, odds_k, name_odds_k)
  - in bang ket qua, danh dau combo tot nhat
  - GHI combo tot nhat ra tuned_params.json (config.py se tu doc file nay)

Tieu chi chon: ROI cao nhat khi moi tran cuoc bang nhau vao nhan vat co
EV du doan cao nhat (argmax p*odds). Day la metric truc tiep cho muc tieu
"best EV gia su dat so tien bang nhau moi tran".

"Auto-ready": tang ten x boi (name_odds_k) nam trong luoi quet. Hien tai du lieu
con mong nen tuner thuong chon name_odds_k=0 (tat). Khi data tang den muc o
(ten x boi) du mau, chay lai script nay -> neu name_odds_k>0 cho ROI cao hon
thi no se TU DONG duoc bat va ghi vao tuned_params.json. Khong can sua tay.
"""
import os, math, json, tempfile, importlib

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
    import database as db; importlib.reload(db)
    import predictor; importlib.reload(predictor)

    tmp = tempfile.mktemp(suffix=".db")
    orig = db.DATABASE_PATH
    db.DATABASE_PATH = tmp
    db.init_db()
    ll = 0.0; n = 0; top1 = 0; brier = 0.0; roi_profit = 0.0
    try:
        for row in real:
            monsters = [{"name": row[f"monster{i}_name"], "multiplier": row[f"monster{i}_multiplier"]} for i in range(1, 5)]
            teacher = {"name": row["teacher_name"], "multiplier": row["teacher_multiplier"]}
            wname = teacher["name"] if row["winner"] == "teacher" else row[f"{row['winner']}_name"]
            if db.get_total_rounds_with_winner() >= min_history:
                P = predictor.predict(monsters, teacher)["probabilities"]
                pw = max(P.get(wname, 1e-9), 1e-9)
                ll += -math.log(pw); n += 1
                if max(P, key=P.get) == wname:
                    top1 += 1
                for nm, pr in P.items():
                    brier += (pr - (1.0 if nm == wname else 0.0)) ** 2
                # ROI: cuoc 1 don vi vao nhan vat co EV du doan cao nhat.
                odds_map_round = {c["name"]: c["multiplier"] for c in monsters + [teacher]}
                pick = max(P, key=lambda nm: P[nm] * odds_map_round.get(nm, 1.0))
                if pick == wname:
                    roi_profit += odds_map_round[pick] - 1
                else:
                    roi_profit -= 1
            db.save_round(monsters, teacher, winner=row["winner"], source="bt")
    finally:
        db.DATABASE_PATH = orig
        try: os.unlink(tmp)
        except OSError: pass
    return ll / n, top1, n, brier / n, roi_profit / n


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
                ll, t, n, br, roi = eval_combo(ik, ok, nok, real_rounds)
                n_total = n
                results.append((ll, br, t, ik, ok, nok, roi))
                print(f"{ik:>5}{ok:>7}{nok:>10}{ll:>10.4f}{br:>9.4f}{t:>5}/{n}{roi:>+9.4f}", flush=True)

    # Tieu chi chon: ROI cao nhat (EV toi da khi cuoc bang nhau moi tran).
    results.sort(key=lambda x: x[6], reverse=True)
    best_ll, best_br, best_t, ik, ok, nok, best_roi = results[0]
    print("\n>>> Combo tot nhat (ROI cao nhat = EV toi da):")
    print(f"    INDIVIDUAL_PRIOR_STRENGTH={ik}  ODDS_CALIB_STRENGTH={ok}  NAME_ODDS_STRENGTH={nok}")
    print(f"    roi_ev={best_roi:+.4f}  logloss={best_ll:.4f}  brier={best_br:.4f}  top1={best_t}/{n_total}")
    if nok > 0:
        print("    -> Tang 'ten x boi' DA co loi! Da bat tu dong.")
    else:
        print("    -> Tang 'ten x boi' van chua co loi (name_odds_k=0). Them data roi chay lai.")

    # Thong tin bo: top 5 theo ROI de so sanh.
    print("\n--- Top 5 combo theo ROI (EV) ---")
    print(f"{'indK':>5}{'oddsK':>7}{'nameOddsK':>10}{'roi_ev':>9}{'logloss':>10}{'top1':>8}")
    for r in results[:5]:
        print(f"{r[3]:>5}{r[4]:>7}{r[5]:>10}{r[6]:>+9.4f}{r[0]:>10.4f}{r[2]:>5}/{n_total}")

    payload = {
        "INDIVIDUAL_PRIOR_STRENGTH": ik,
        "ODDS_CALIB_STRENGTH": ok,
        "NAME_ODDS_STRENGTH": nok,
        "_meta": {
            "rounds_evaluated": n_total,
            "roi_ev": round(best_roi, 4),
            "logloss": round(best_ll, 4),
            "brier": round(best_br, 4),
            "top1": best_t,
            "optimized_by": "roi_ev",
        },
    }
    with open(PARAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nDa ghi {PARAMS_FILE}. config.py se tu dong dung cac tham so nay.")
