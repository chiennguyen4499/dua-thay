import os
import re
import json
import unicodedata
from dotenv import load_dotenv

load_dotenv()


def _load_tuned_params() -> dict:
    """Doc tuned_params.json (do tune_shrinkage.py ghi) neu co.

    Day la co che "auto-ready": chay `python tune_shrinkage.py` sau khi them data,
    no chon lai (ind_k, odds_k, name_odds_k) tot nhat va ghi file nay; config se
    tu dung. Dat IGNORE_TUNED_PARAMS=1 (luc dang tune) de bo qua, tranh vong lap.
    """
    if os.getenv("IGNORE_TUNED_PARAMS") == "1":
        return {}
    path = os.path.join(os.path.dirname(__file__), "tuned_params.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_TUNED = _load_tuned_params()


def _tuned_int(key: str, env_default: str) -> int:
    """Uu tien: bien moi truong > tuned_params.json > mac dinh."""
    if os.getenv(key) is not None:
        return int(os.getenv(key))
    if key in _TUNED:
        return int(_TUNED[key])
    return int(env_default)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "")

# Đường dẫn DB neo theo VỊ TRÍ FILE NÀY (thư mục dự án), không theo thư mục đang
# chạy (CWD). Nhờ vậy `python main.py` chạy từ bất kỳ đâu — hoặc sau khi di chuyển
# cả thư mục dự án — vẫn luôn trỏ đúng data/rounds.db, không tạo DB rỗng mới ở CWD.
# Nếu DATABASE_PATH trong .env là đường dẫn TUYỆT ĐỐI thì tôn trọng nguyên trạng.
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_db_path = os.getenv("DATABASE_PATH", "data/rounds.db")
DATABASE_PATH = _db_path if os.path.isabs(_db_path) else os.path.join(_PROJECT_ROOT, _db_path)
MIN_SAMPLES_FOR_PATTERN = int(os.getenv("MIN_SAMPLES_FOR_PATTERN", "3"))

# Turso (database online dùng chung giữa bot trên PC và Web UI trên Streamlit
# Cloud). Để trống 2 biến này thì hệ thống tự dùng SQLite local (DATABASE_PATH)
# như trước — không bắt buộc phải có Turso mới chạy được.
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

# Yêu quái trong game (tên từ CSV — dùng dấu gạch thay dấu cách)
KNOWN_MONSTERS = [
    "Bach_cot_tinh",
    "Bach_nhan_quan",
    "Bach_tuong",
    "Cuu_dau_trung",
    "Dai_bang_kim_si",
    "Duong_dai_tien",
    "Hac_hung_tinh",
    "Hoang_mi_vuong",
    "Hong_hai_nhi",
    "Lao_ban",
    "Loc_dai_tien",
    "Mac_lan",
    "Thanh_long_nu",
    "Thanh_nguu",
    "Thanh_su",
    "Thien_thu_yeu_co",
    "Tieu_toan_phong",
    "Xich_vy_ma_hat",
]

KNOWN_TEACHERS = ["Duong_tang"]

TEACHER_DEFAULT = "Duong_tang"


def _strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt: 'Hồ Ly' -> 'Ho Ly'."""
    text = text.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm_key(name: str) -> str:
    """Khóa so khớp: bỏ dấu, lowercase, gộp khoảng trắng/gạch dưới."""
    s = _strip_diacritics(name).lower().strip()
    s = re.sub(r"[\s_]+", "_", s)
    return s


# Bảng tra ngược: khóa chuẩn hóa -> tên canonical trong KNOWN_*
_CANONICAL_LOOKUP = {_norm_key(n): n for n in KNOWN_MONSTERS + KNOWN_TEACHERS}
# Một vài bí danh tiếng Việt có dấu hay gặp khi nhập tay
for _alias, _canon in {
    "duong_tang": "Duong_tang",
    "su_phu": "Duong_tang",
    "thay": "Duong_tang",
}.items():
    _CANONICAL_LOOKUP.setdefault(_alias, _canon)


def display_name(name: str) -> str:
    """Tên hiển thị thân thiện và AN TOÀN cho Telegram Markdown.

    Tên canonical dùng gạch dưới ('Bach_nhan_quan'). Trong Markdown (legacy),
    dấu '_' là ký tự bắt đầu in nghiêng và KHÔNG escape được — số '_' lẻ trong
    một tin nhắn sẽ làm Telegram báo 'can't find end of the entity' và bỏ gửi.
    Đổi '_' thành khoảng trắng vừa hết lỗi vừa dễ đọc hơn. Chỉ dùng để HIỂN THỊ,
    tên lưu trong DB vẫn giữ nguyên dạng canonical.
    """
    return name.replace("_", " ")


def canonical_name(raw: str) -> str:
    """
    Đưa tên nhập tự do về dạng canonical đã biết để dữ liệu không bị phân mảnh.
    'Hồ Ly', 'ho ly', 'Ho_Ly' -> cùng một tên nếu khớp KNOWN_*.
    Không khớp thì trả về tên đã chuẩn hóa khoảng trắng (giữ nguyên ý người dùng).
    """
    if not raw:
        return raw
    key = _norm_key(raw)
    if key in _CANONICAL_LOOKUP:
        return _CANONICAL_LOOKUP[key]
    # Không có trong danh sách biết trước: chuẩn hóa nhẹ để ổn định
    return re.sub(r"\s+", "_", raw.strip())


# Độ mạnh prior (số "trận ảo") khi blend lịch sử cá nhân với odds.
# a (số lần xuất hiện) >> K  -> tin lịch sử;  a << K -> tin odds.
# Đã tune theo backtest walk-forward trên 118 trận (2026-06): vùng tối ưu rộng
# ind_k≈25–40. Tỷ lệ-thắng-theo-TÊN còn nhiễu (mỗi tên ~vài chục mẫu) nên shrink
# mạnh về tầng "giá trị bội"; logloss 1.443 -> 1.421. Khi data tăng nhiều (vài
# trăm trận/tên), tên đáng tin hơn -> chạy lại tune_shrinkage.py, ind_k có thể giảm.
INDIVIDUAL_PRIOR_STRENGTH = _tuned_int("INDIVIDUAL_PRIOR_STRENGTH", "25")

# ── Mô hình phân tầng theo BỘI SỐ (odds-calibration) ──────────────────────
# Mỗi nhân vật được ước lượng qua 3 tầng shrinkage, từ thô đến tinh:
#   1/odds  ->  win-rate GỘP theo giá trị bội  ->  win-rate theo TÊN
#                                              ->  win-rate theo (TÊN × bội)
# Mỗi tầng "kéo" về tầng thô hơn theo số mẫu của chính nó; K = số trận ảo.
#
# ODDS_CALIB_STRENGTH: độ tin của "win-rate theo giá trị bội" so với 1/odds.
#   Nhỏ -> tin dữ liệu thực tế của bội đó sớm hơn (bắt nhanh tín hiệu như
#   "bội 5 hay về", "thầy bội cao hay thoát") nhưng dễ nhiễu khi ít mẫu.
#   Tune 2026-06: odds_k=3 cho logloss thấp nhất — bảng theo-bội đã đủ mẫu để tin sớm.
ODDS_CALIB_STRENGTH = _tuned_int("ODDS_CALIB_STRENGTH", "3")
# NAME_ODDS_STRENGTH: độ tin của "tên CỤ THỂ tại bội CỤ THỂ".
# CHẾ ĐỘ AUTO-READY: tầng này giờ nằm trong lưới quét của tune_shrinkage.py.
# Mặc định vẫn 0 (TẮT) vì ở 127 trận ô (tên × bội) còn quá mỏng mẫu (~2 mẫu/ô)
# → backtest cho thấy bật vào làm logloss TỆ hơn. NHƯNG không cần chỉnh tay nữa:
# mỗi lần thêm data, chạy `python tune_shrinkage.py` — nếu name_odds_k>0 cho
# logloss thấp hơn, nó tự ghi vào tuned_params.json và config tự bật tầng này.
NAME_ODDS_STRENGTH = _tuned_int("NAME_ODDS_STRENGTH", "0")
