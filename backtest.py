"""
Backtest: danh gia predictor co thuc su tot hon baseline (chi dung odds) khong.

Phuong phap: chronological replay (walk-forward).
  - Duyet cac tran theo thu tu thoi gian.
  - Voi moi tran: du doan CHI bang du lieu cac tran TRUOC do, roi so ket qua that.
  - So sanh 3 chien luoc:
      MODEL    : predictor.predict() hien tai
      ODDS     : chi dung xac suat implied tu boi so (1/odds normalize)
      UNIFORM  : 1/5 cho moi nguoi (mu hoan toan)

Metric:
  - LogLoss (thap = tot): -mean(log(p gan cho nguoi THANG that))
  - Brier   (thap = tot)
  - Top-1 accuracy (cao = tot): argmax co trung nguoi thang khong
  - Mean p(winner): xac suat trung binh model gan cho nguoi thang that
"""

import math

import database as db
import predictor


def _reconstruct(row):
    monsters = [
        {"name": row[f"monster{i}_name"], "multiplier": row[f"monster{i}_multiplier"]}
        for i in range(1, 5)
    ]
    teacher = {"name": row["teacher_name"], "multiplier": row["teacher_multiplier"]}
    if row["winner"] == "teacher":
        winner_name = teacher["name"]
    else:
        winner_name = row[f"{row['winner']}_name"]
    return monsters, teacher, winner_name


def _odds_probs(monsters, teacher):
    chars = monsters + [teacher]
    inv = {c["name"]: 1.0 / max(c["multiplier"], 0.01) for c in chars}
    tot = sum(inv.values())
    return {k: v / tot for k, v in inv.items()}


def run_backtest(min_history: int = 10):
    # 1. Lay du lieu that (DB hien tai), theo thu tu thoi gian
    real_rounds = db.get_all_rounds_with_winner()
    real_rounds = sorted(real_rounds, key=lambda r: r["id"])  # id ~ thu tu nhap

    if len(real_rounds) <= min_history:
        print(f"Chi co {len(real_rounds)} tran, can > {min_history} de backtest.")
        return

    stats = {
        "MODEL":   {"logloss": 0.0, "brier": 0.0, "top1": 0, "pwin": 0.0},
        "ODDS":    {"logloss": 0.0, "brier": 0.0, "top1": 0, "pwin": 0.0},
        "UNIFORM": {"logloss": 0.0, "brier": 0.0, "top1": 0, "pwin": 0.0},
    }
    method_count = {}
    n_eval = 0

    # Walk-forward THUAN PYTHON: du doan tran thu i CHI bang cac tran TRUOC do
    # (real_rounds[:i]). Khong con DB tam / trao DATABASE_PATH.
    for i, row in enumerate(real_rounds):
        monsters, teacher, winner_name = _reconstruct(row)
        history = real_rounds[:i]

        if len(history) >= min_history:
            n_eval += 1

            model = predictor.predict(monsters, teacher, history)
            p_model = model["probabilities"]
            method_count[model["method"]] = method_count.get(model["method"], 0) + 1

            p_odds = _odds_probs(monsters, teacher)
            names = list(p_odds.keys())
            p_unif = {k: 1.0 / len(names) for k in names}

            for tag, P in (("MODEL", p_model), ("ODDS", p_odds), ("UNIFORM", p_unif)):
                pw = max(P.get(winner_name, 1e-9), 1e-9)
                stats[tag]["logloss"] += -math.log(pw)
                stats[tag]["pwin"] += pw
                # Brier multiclass
                stats[tag]["brier"] += sum(
                    (P.get(n, 0.0) - (1.0 if n == winner_name else 0.0)) ** 2
                    for n in names
                )
                pred_name = max(P, key=P.get)
                if pred_name == winner_name:
                    stats[tag]["top1"] += 1

    # 3. Bao cao
    print("=" * 60)
    print(f"BACKTEST — {n_eval} tran duoc danh gia (history >= {min_history})")
    print(f"Tong tran co ket qua: {len(real_rounds)}")
    print("=" * 60)
    print(f"Phuong phap model da dung: {method_count}")
    print()
    print(f"{'Metric':<16}{'MODEL':>12}{'ODDS':>12}{'UNIFORM':>12}")
    print("-" * 52)
    for metric, better in (("logloss", "low"), ("brier", "low"),
                           ("pwin", "high"), ("top1", "high")):
        row_vals = []
        for tag in ("MODEL", "ODDS", "UNIFORM"):
            v = stats[tag][metric]
            if metric == "top1":
                row_vals.append(f"{v}/{n_eval} ({v/n_eval*100:.0f}%)")
            else:
                row_vals.append(f"{v/n_eval:.4f}")
        arrow = "v thap tot" if better == "low" else "^ cao tot"
        print(f"{metric:<16}{row_vals[0]:>12}{row_vals[1]:>12}{row_vals[2]:>12}   {arrow}")

    print()
    # Ket luan nhanh
    ll_model = stats["MODEL"]["logloss"] / n_eval
    ll_odds = stats["ODDS"]["logloss"] / n_eval
    diff = ll_odds - ll_model
    print("-" * 60)
    if diff > 0.005:
        print(f"=> MODEL TOT HON odds-only (logloss thap hon {diff:.4f}).")
    elif diff < -0.005:
        print(f"=> MODEL TE HON odds-only (logloss cao hon {-diff:.4f}).")
    else:
        print(f"=> MODEL ~ ngang odds-only (chenh logloss {diff:+.4f}, khong dang ke).")
    print("-" * 60)


if __name__ == "__main__":
    run_backtest()
