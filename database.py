import sqlite3
import json
from datetime import datetime
from config import DATABASE_PATH, canonical_name


def get_conn():
    conn = sqlite3.connect(DATABASE_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    # WAL: cho phép bot (thread) và web (process riêng) đọc/ghi đồng thời
    # mà không bị "database is locked".
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT (datetime('now','localtime')),

                monster1_name TEXT NOT NULL,
                monster1_multiplier REAL NOT NULL,
                monster2_name TEXT NOT NULL,
                monster2_multiplier REAL NOT NULL,
                monster3_name TEXT NOT NULL,
                monster3_multiplier REAL NOT NULL,
                monster4_name TEXT NOT NULL,
                monster4_multiplier REAL NOT NULL,

                teacher_name TEXT NOT NULL,
                teacher_multiplier REAL NOT NULL,

                -- 'monster1'..'monster4' hoặc 'teacher'
                winner TEXT,

                pattern_key TEXT,
                source TEXT DEFAULT 'manual',
                notes TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pattern ON rounds(pattern_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_winner ON rounds(winner)")
        conn.commit()


def make_pattern_key(monsters: list[dict], teacher: dict) -> str:
    """
    Tạo key pattern chỉ dựa trên tên (không gồm odds).
    Cùng combo yêu quái dù odds khác nhau vẫn là 1 pattern.
    """
    sorted_names = sorted(m["name"] for m in monsters)
    return "|".join(sorted_names) + f"|T:{teacher['name']}"


def save_round(monsters: list[dict], teacher: dict, winner: str | None = None,
               source: str = "manual", notes: str = "") -> int:
    """Lưu 1 trận vào database, trả về id.

    Tên được chuẩn hóa về canonical tại đây — điểm vào duy nhất — nên mọi
    nguồn (web, telegram tay, OCR, CSV) đều thống nhất, tránh phân mảnh dữ liệu.
    """
    monsters = [{**m, "name": canonical_name(m["name"])} for m in monsters]
    teacher = {**teacher, "name": canonical_name(teacher["name"])}
    key = make_pattern_key(monsters, teacher)
    with get_conn() as conn:
        cursor = conn.execute("""
            INSERT INTO rounds
              (monster1_name, monster1_multiplier,
               monster2_name, monster2_multiplier,
               monster3_name, monster3_multiplier,
               monster4_name, monster4_multiplier,
               teacher_name, teacher_multiplier,
               winner, pattern_key, source, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            monsters[0]["name"], monsters[0]["multiplier"],
            monsters[1]["name"], monsters[1]["multiplier"],
            monsters[2]["name"], monsters[2]["multiplier"],
            monsters[3]["name"], monsters[3]["multiplier"],
            teacher["name"], teacher["multiplier"],
            winner, key, source, notes
        ))
        conn.commit()
        return cursor.lastrowid


def update_winner(round_id: int, winner: str):
    with get_conn() as conn:
        conn.execute("UPDATE rounds SET winner=? WHERE id=?", (winner, round_id))
        conn.commit()


def delete_pending_rounds() -> int:
    """Xóa các trận chưa nhập kết quả (winner IS NULL). Trả về số trận đã xóa."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM rounds WHERE winner IS NULL")
        conn.commit()
        return cur.rowcount


def delete_round(round_id: int, only_pending: bool = True) -> bool:
    """Xóa 1 trận theo id. Trả về True nếu có xóa.

    only_pending=True (mặc định) chỉ xóa khi trận chưa có kết quả — an toàn,
    không bao giờ xóa nhầm trận đã ghi kết quả.
    """
    with get_conn() as conn:
        if only_pending:
            cur = conn.execute("DELETE FROM rounds WHERE id=? AND winner IS NULL", (round_id,))
        else:
            cur = conn.execute("DELETE FROM rounds WHERE id=?", (round_id,))
        conn.commit()
        return cur.rowcount > 0


def get_rounds_by_pattern(pattern_key: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM rounds WHERE pattern_key=? AND winner IS NOT NULL ORDER BY created_at DESC",
            (pattern_key,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_rounds_with_winner() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM rounds WHERE winner IS NOT NULL ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_monster_stats(monster_name: str) -> dict:
    """Thống kê tổng hợp cho 1 yêu quái: số lần xuất hiện, số lần thắng."""
    with get_conn() as conn:
        # Đếm số trận xuất hiện (trong bất kỳ slot nào)
        appeared = conn.execute("""
            SELECT COUNT(*) as cnt FROM rounds
            WHERE winner IS NOT NULL
              AND (monster1_name=? OR monster2_name=? OR monster3_name=? OR monster4_name=?)
        """, (monster_name,)*4).fetchone()["cnt"]

        # Đếm số lần thắng (winner = monster_name trong bất kỳ slot tương ứng)
        won = conn.execute("""
            SELECT COUNT(*) as cnt FROM rounds
            WHERE winner IS NOT NULL AND (
                (winner='monster1' AND monster1_name=?) OR
                (winner='monster2' AND monster2_name=?) OR
                (winner='monster3' AND monster3_name=?) OR
                (winner='monster4' AND monster4_name=?)
            )
        """, (monster_name,)*4).fetchone()["cnt"]

    return {"name": monster_name, "appeared": appeared, "won": won}


def get_teacher_stats(teacher_name: str) -> dict:
    with get_conn() as conn:
        appeared = conn.execute(
            "SELECT COUNT(*) as cnt FROM rounds WHERE winner IS NOT NULL AND teacher_name=?",
            (teacher_name,)
        ).fetchone()["cnt"]
        won = conn.execute(
            "SELECT COUNT(*) as cnt FROM rounds WHERE winner='teacher' AND teacher_name=?",
            (teacher_name,)
        ).fetchone()["cnt"]
    return {"name": teacher_name, "appeared": appeared, "won": won}


def get_overall_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM rounds WHERE winner IS NOT NULL"
        ).fetchone()["cnt"]
        teacher_wins = conn.execute(
            "SELECT COUNT(*) as cnt FROM rounds WHERE winner='teacher'"
        ).fetchone()["cnt"]
        pending = conn.execute(
            "SELECT COUNT(*) as cnt FROM rounds WHERE winner IS NULL"
        ).fetchone()["cnt"]
    monster_wins = total - teacher_wins
    return {
        "total": total,
        "teacher_wins": teacher_wins,
        "monster_wins": monster_wins,
        "pending": pending,
        "teacher_win_rate": (teacher_wins / total * 100) if total > 0 else 0,
    }


def get_recent_rounds(limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM rounds ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_round_by_id(round_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM rounds WHERE id=?", (round_id,)).fetchone()
    return dict(row) if row else None


def get_total_rounds_with_winner() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) as cnt FROM rounds WHERE winner IS NOT NULL"
        ).fetchone()["cnt"]


def get_all_competitor_stats() -> list[dict]:
    """Trả về thống kê tổng hợp cho tất cả yêu quái trong DB."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT name, SUM(appeared) as appeared, SUM(won) as won FROM (
                SELECT monster1_name as name, COUNT(*) as appeared,
                       SUM(CASE WHEN winner='monster1' THEN 1 ELSE 0 END) as won
                FROM rounds WHERE winner IS NOT NULL GROUP BY monster1_name
                UNION ALL
                SELECT monster2_name, COUNT(*),
                       SUM(CASE WHEN winner='monster2' THEN 1 ELSE 0 END)
                FROM rounds WHERE winner IS NOT NULL GROUP BY monster2_name
                UNION ALL
                SELECT monster3_name, COUNT(*),
                       SUM(CASE WHEN winner='monster3' THEN 1 ELSE 0 END)
                FROM rounds WHERE winner IS NOT NULL GROUP BY monster3_name
                UNION ALL
                SELECT monster4_name, COUNT(*),
                       SUM(CASE WHEN winner='monster4' THEN 1 ELSE 0 END)
                FROM rounds WHERE winner IS NOT NULL GROUP BY monster4_name
                UNION ALL
                SELECT teacher_name, COUNT(*),
                       SUM(CASE WHEN winner='teacher' THEN 1 ELSE 0 END)
                FROM rounds WHERE winner IS NOT NULL GROUP BY teacher_name
            ) GROUP BY name ORDER BY appeared DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_odds_calibration_data() -> list[dict]:
    """
    Lấy dữ liệu để phân tích: odds vs xác suất thắng thực tế.
    Mỗi record là 1 competitor trong 1 trận, kèm odds và is_winner.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT name, odds, is_winner FROM (
                SELECT monster1_name as name, monster1_multiplier as odds,
                       CASE WHEN winner='monster1' THEN 1 ELSE 0 END as is_winner
                FROM rounds WHERE winner IS NOT NULL
                UNION ALL
                SELECT monster2_name, monster2_multiplier,
                       CASE WHEN winner='monster2' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL
                UNION ALL
                SELECT monster3_name, monster3_multiplier,
                       CASE WHEN winner='monster3' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL
                UNION ALL
                SELECT monster4_name, monster4_multiplier,
                       CASE WHEN winner='monster4' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL
                UNION ALL
                SELECT teacher_name, teacher_multiplier,
                       CASE WHEN winner='teacher' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL
            )
        """).fetchall()
    return [dict(r) for r in rows]


def get_monster_odds_winrate() -> dict[int, dict]:
    """Tỷ lệ thắng GỘP theo từng giá trị bội của yêu quái (mọi tên, mọi slot).

    Trả về {odds_int: {"appeared": n, "won": w}}. Đây là "đường calibration"
    thực tế: bội X thắng bao nhiêu %, gộp toàn bộ yêu quái để có đủ mẫu.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT CAST(ROUND(odds) AS INTEGER) AS odds,
                   COUNT(*) AS appeared, SUM(is_winner) AS won
            FROM (
                SELECT monster1_multiplier AS odds,
                       CASE WHEN winner='monster1' THEN 1 ELSE 0 END AS is_winner
                FROM rounds WHERE winner IS NOT NULL
                UNION ALL
                SELECT monster2_multiplier,
                       CASE WHEN winner='monster2' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL
                UNION ALL
                SELECT monster3_multiplier,
                       CASE WHEN winner='monster3' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL
                UNION ALL
                SELECT monster4_multiplier,
                       CASE WHEN winner='monster4' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL
            ) GROUP BY odds
        """).fetchall()
    return {r["odds"]: {"appeared": r["appeared"], "won": r["won"] or 0} for r in rows}


def get_teacher_odds_winrate() -> dict[int, dict]:
    """Tỷ lệ thoát của Thầy theo từng giá trị bội. {odds_int: {appeared, won}}.

    Thầy xuất hiện mọi trận nên bảng này nhiều mẫu — bắt tín hiệu "thầy bội
    cao hay thoát" mà 1/odds không thấy được.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT CAST(ROUND(teacher_multiplier) AS INTEGER) AS odds,
                   COUNT(*) AS appeared,
                   SUM(CASE WHEN winner='teacher' THEN 1 ELSE 0 END) AS won
            FROM rounds WHERE winner IS NOT NULL
            GROUP BY odds
        """).fetchall()
    return {r["odds"]: {"appeared": r["appeared"], "won": r["won"] or 0} for r in rows}


def get_monster_name_odds_stats(name: str, odds: int) -> dict:
    """Số lần 1 yêu quái CỤ THỂ thắng/xuất hiện tại MỘT giá trị bội cụ thể."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS appeared, SUM(win) AS won FROM (
                SELECT CASE WHEN winner='monster1' THEN 1 ELSE 0 END AS win
                FROM rounds WHERE winner IS NOT NULL
                  AND monster1_name=? AND CAST(ROUND(monster1_multiplier) AS INTEGER)=?
                UNION ALL
                SELECT CASE WHEN winner='monster2' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL
                  AND monster2_name=? AND CAST(ROUND(monster2_multiplier) AS INTEGER)=?
                UNION ALL
                SELECT CASE WHEN winner='monster3' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL
                  AND monster3_name=? AND CAST(ROUND(monster3_multiplier) AS INTEGER)=?
                UNION ALL
                SELECT CASE WHEN winner='monster4' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL
                  AND monster4_name=? AND CAST(ROUND(monster4_multiplier) AS INTEGER)=?
            )
        """, (name, odds) * 4).fetchone()
    return {"appeared": row["appeared"], "won": row["won"] or 0}


def get_teacher_name_odds_stats(name: str, odds: int) -> dict:
    """Số lần Thầy thoát/xuất hiện tại một giá trị bội cụ thể."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS appeared,
                   SUM(CASE WHEN winner='teacher' THEN 1 ELSE 0 END) AS won
            FROM rounds WHERE winner IS NOT NULL
              AND teacher_name=? AND CAST(ROUND(teacher_multiplier) AS INTEGER)=?
        """, (name, odds)).fetchone()
    return {"appeared": row["appeared"], "won": row["won"] or 0}


def source_round_id_exists(original_id: int, source: str = "csv_import") -> bool:
    """Kiểm tra đã import trận này từ nguồn này chưa (tránh duplicate)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM rounds WHERE notes=? AND source=?",
            (f"original_id:{original_id}", source)
        ).fetchone()
    return row is not None
