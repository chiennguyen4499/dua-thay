"""
Import dữ liệu lịch sử từ file CSV vào database.

Format CSV:
  Round_id,Competitor,Odds,Is_winner

Cách dùng:
  python import_csv.py path/to/Duathay.csv
  python import_csv.py path/to/Duathay.csv --clear   # xóa DB cũ trước khi import
"""

import sys
import csv
import argparse
from pathlib import Path
import database as db

TEACHER_NAME = "Duong_tang"


def parse_rounds(filepath: str) -> dict[int, list[dict]]:
    """Đọc CSV, nhóm theo Round_id."""
    rounds: dict[int, list[dict]] = {}
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("Round_id"):
                continue
            rid = int(row["Round_id"])
            if rid not in rounds:
                rounds[rid] = []
            rounds[rid].append({
                "name": row["Competitor"].strip(),
                "odds": float(row["Odds"]),
                "is_winner": int(row["Is_winner"]),
            })
    return rounds


def import_rounds(rounds: dict, skip_existing: bool = True, verbose: bool = True) -> tuple[int, int]:
    """Import vào DB. Trả về (imported, skipped)."""
    imported = 0
    skipped = 0

    for rid, competitors in sorted(rounds.items()):
        # Bỏ qua nếu đã import rồi
        if skip_existing and db.source_round_id_exists(rid, "csv_import"):
            skipped += 1
            continue

        # Tách monsters và teacher
        monsters = []
        teacher_data = None
        winner_name = None

        for c in competitors:
            if c["is_winner"] == 1:
                winner_name = c["name"]
            if c["name"] == TEACHER_NAME:
                teacher_data = {"name": c["name"], "multiplier": c["odds"]}
            else:
                monsters.append({"name": c["name"], "multiplier": c["odds"]})

        if len(monsters) != 4:
            if verbose:
                print(f"  SKIP  Round {rid}: {len(monsters)} yeu quai (can dung 4)")
            skipped += 1
            continue
        if teacher_data is None:
            if verbose:
                print(f"  SKIP  Round {rid}: khong tim thay {TEACHER_NAME}")
            skipped += 1
            continue

        # Xác định winner slot
        winner_slot = None
        if winner_name == TEACHER_NAME:
            winner_slot = "teacher"
        elif winner_name:
            for i, m in enumerate(monsters, 1):
                if m["name"] == winner_name:
                    winner_slot = f"monster{i}"
                    break

        db.save_round(
            monsters, teacher_data,
            winner=winner_slot,
            source="csv_import",
            notes=f"original_id:{rid}",
        )
        imported += 1
        if verbose:
            winner_label = winner_name or "?"
            print(f"  OK    Round {rid:3d} -> winner: {winner_label}")

    return imported, skipped


def main():
    parser = argparse.ArgumentParser(description="Import CSV lịch sử Đua Thầy")
    parser.add_argument("filepath", help="Đường dẫn file CSV")
    parser.add_argument("--clear", action="store_true",
                        help="Xóa toàn bộ database trước khi import")
    parser.add_argument("--quiet", action="store_true", help="Không in chi tiết từng trận")
    args = parser.parse_args()

    if not Path(args.filepath).exists():
        print(f"Lỗi: không tìm thấy file '{args.filepath}'")
        sys.exit(1)

    db.init_db()

    if args.clear:
        print("[!!] Dang xoa database cu...")
        db.clear_all_rounds()
        print("   Đã xóa.")

    print(f"[DOC] File: {args.filepath}")
    rounds = parse_rounds(args.filepath)
    print(f"   Tim thay {len(rounds)} tran\n")

    imported, skipped = import_rounds(rounds, skip_existing=not args.clear, verbose=not args.quiet)

    print(f"\n{'='*40}")
    print(f"[OK] Da import: {imported} tran")
    print(f"[--] Bo qua:    {skipped} tran (da ton tai)")
    print(f"{'='*40}")

    stats = db.get_overall_stats()
    print(f"\nDatabase hien co:")
    print(f"   Tong tran co ket qua: {stats['total']}")
    print(f"   Yeu quai thang: {stats['monster_wins']} ({100 - stats['teacher_win_rate']:.1f}%)")
    print(f"   Thay thoat:     {stats['teacher_wins']} ({stats['teacher_win_rate']:.1f}%)")

    all_stats = db.get_all_competitor_stats()
    non_teacher = [s for s in all_stats if s["name"] != TEACHER_NAME and s["appeared"] > 0]
    non_teacher_sorted = sorted(non_teacher, key=lambda x: x["won"] / x["appeared"], reverse=True)
    print(f"\nTop yeu quai (ty le thang khi xuat hien):")
    for s in non_teacher_sorted[:8]:
        rate = s["won"] / s["appeared"] * 100
        print(f"   {s['name']:22s}: {s['won']:2d}/{s['appeared']:2d} = {rate:.1f}%")


if __name__ == "__main__":
    main()
