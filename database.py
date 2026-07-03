import sqlite3
import json
from datetime import datetime
from config import DATABASE_PATH, TURSO_DATABASE_URL, TURSO_AUTH_TOKEN, canonical_name

# Path lúc import module — dùng để nhận biết khi backtest.py/tune_shrinkage.py/
# strategy_analysis.py tạm tráo `database.DATABASE_PATH` sang 1 file SQLite tạm
# (tempfile.mktemp) để replay hàng trăm lần. Khi đó PHẢI luôn dùng SQLite local
# (nhanh, không round-trip mạng), bất kể đã cấu hình Turso hay chưa.
_CONFIGURED_DB_PATH = DATABASE_PATH


class _RemoteRow(dict):
    """Row từ Turso: cho phép truy cập theo tên cột (row['x']) VÀ theo index
    (row[0]), giống sqlite3.Row mà 18 hàm bên dưới đang dùng."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


class _RemoteCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def _wrap(self, row):
        if row is None:
            return None
        cols = [d[0] for d in self._cursor.description]
        return _RemoteRow(zip(cols, row))

    def fetchall(self):
        return [self._wrap(r) for r in self._cursor.fetchall()]

    def fetchone(self):
        return self._wrap(self._cursor.fetchone())

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return self._cursor.lastrowid


class _RemoteConn:
    """Bọc kết nối libsql (Turso) để có API giống sqlite3.Connection: execute()
    trả cursor có fetchall/fetchone trả row truy cập theo tên cột, và dùng được
    như context manager (`with get_conn() as conn:`)."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return _RemoteCursor(self._conn.execute(sql, params))

    def commit(self):
        self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        # Không close: giống hành vi sqlite3 hiện tại, mỗi lời gọi tự mở kết
        # nối mới và để tự dọn (đơn giản, phù hợp tần suất ghi thấp của app).


def _is_remote() -> bool:
    """True khi đang dùng DATABASE_PATH gốc (không bị backtest/tune tráo tạm)
    VÀ đã cấu hình Turso."""
    return DATABASE_PATH == _CONFIGURED_DB_PATH and bool(TURSO_DATABASE_URL)


def get_conn():
    if _is_remote():
        import libsql
        conn = libsql.connect(database=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)
        return _RemoteConn(conn)
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


def get_monster_stats_batch(names: list[str]) -> dict[str, dict]:
    """Thống kê xuất hiện/thắng cho NHIỀU yêu quái trong 1 query (thay vì 1
    query/con) — tránh N+1 round-trip khi gọi Turso ở predictor.predict()."""
    if not names:
        return {}
    placeholders = ",".join("?" * len(names))
    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT name, SUM(appeared) as appeared, SUM(won) as won FROM (
                SELECT monster1_name as name, COUNT(*) as appeared,
                       SUM(CASE WHEN winner='monster1' THEN 1 ELSE 0 END) as won
                FROM rounds WHERE winner IS NOT NULL AND monster1_name IN ({placeholders})
                GROUP BY monster1_name
                UNION ALL
                SELECT monster2_name, COUNT(*),
                       SUM(CASE WHEN winner='monster2' THEN 1 ELSE 0 END)
                FROM rounds WHERE winner IS NOT NULL AND monster2_name IN ({placeholders})
                GROUP BY monster2_name
                UNION ALL
                SELECT monster3_name, COUNT(*),
                       SUM(CASE WHEN winner='monster3' THEN 1 ELSE 0 END)
                FROM rounds WHERE winner IS NOT NULL AND monster3_name IN ({placeholders})
                GROUP BY monster3_name
                UNION ALL
                SELECT monster4_name, COUNT(*),
                       SUM(CASE WHEN winner='monster4' THEN 1 ELSE 0 END)
                FROM rounds WHERE winner IS NOT NULL AND monster4_name IN ({placeholders})
                GROUP BY monster4_name
            ) GROUP BY name
        """, names * 4).fetchall()
    result = {r["name"]: {"appeared": r["appeared"], "won": r["won"] or 0} for r in rows}
    for name in names:
        result.setdefault(name, {"appeared": 0, "won": 0})
    return result


def get_teacher_stats(teacher_name: str) -> dict:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as appeared,
                   SUM(CASE WHEN winner='teacher' THEN 1 ELSE 0 END) as won
            FROM rounds WHERE winner IS NOT NULL AND teacher_name=?
        """, (teacher_name,)).fetchone()
    return {"name": teacher_name, "appeared": row["appeared"], "won": row["won"] or 0}


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


def get_monster_name_odds_stats_batch(names: list[str]) -> dict[tuple[str, int], dict]:
    """Số lần thắng/xuất hiện của NHIỀU yêu quái tại TỪNG giá trị bội, trong
    1 query (thay vì 1 query/con) — tránh N+1 round-trip khi gọi Turso.

    Trả về {(name, odds_int): {"appeared": n, "won": w}}. Lấy tất cả mức bội
    đã từng xuất hiện của các tên này rồi predictor tự tra theo bội cần dùng.
    """
    if not names:
        return {}
    placeholders = ",".join("?" * len(names))
    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT name, odds, COUNT(*) AS appeared, SUM(win) AS won FROM (
                SELECT monster1_name AS name,
                       CAST(ROUND(monster1_multiplier) AS INTEGER) AS odds,
                       CASE WHEN winner='monster1' THEN 1 ELSE 0 END AS win
                FROM rounds WHERE winner IS NOT NULL AND monster1_name IN ({placeholders})
                UNION ALL
                SELECT monster2_name, CAST(ROUND(monster2_multiplier) AS INTEGER),
                       CASE WHEN winner='monster2' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL AND monster2_name IN ({placeholders})
                UNION ALL
                SELECT monster3_name, CAST(ROUND(monster3_multiplier) AS INTEGER),
                       CASE WHEN winner='monster3' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL AND monster3_name IN ({placeholders})
                UNION ALL
                SELECT monster4_name, CAST(ROUND(monster4_multiplier) AS INTEGER),
                       CASE WHEN winner='monster4' THEN 1 ELSE 0 END
                FROM rounds WHERE winner IS NOT NULL AND monster4_name IN ({placeholders})
            ) GROUP BY name, odds
        """, names * 4).fetchall()
    return {(r["name"], r["odds"]): {"appeared": r["appeared"], "won": r["won"] or 0} for r in rows}


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


def clear_all_rounds() -> int:
    """Xóa TOÀN BỘ dữ liệu trận (dùng khi Import CSV muốn nạp lại từ đầu).
    Trả về số dòng đã xóa."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM rounds")
        conn.commit()
        return cur.rowcount


def clear_rounds_by_source(source: str) -> int:
    """Xóa các trận theo giá trị cột `source` (vd. dọn rác nguồn replay tạm
    của strategy_analysis.py). Trả về số dòng đã xóa."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM rounds WHERE source=?", (source,))
        conn.commit()
        return cur.rowcount


def source_round_id_exists(original_id: int, source: str = "csv_import") -> bool:
    """Kiểm tra đã import trận này từ nguồn này chưa (tránh duplicate)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM rounds WHERE notes=? AND source=?",
            (f"original_id:{original_id}", source)
        ).fetchone()
    return row is not None
