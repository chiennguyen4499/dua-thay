"""
OCR module: trích xuất thông tin từ ảnh chụp màn hình game Sư Phụ Chạy Mau.
Dùng EasyOCR để nhận dạng text tiếng Việt.

Kết quả trả về:
{
    "monsters": [
        {"name": "Hồ Ly", "multiplier": 2.5},
        ...
    ],
    "teacher": {"name": "Đường Tăng", "multiplier": 1.8},
    "raw_text": [...],
    "confidence": float (0-1),
}
"""

import re
from pathlib import Path

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["vi", "en"], gpu=False)
    return _reader


def _extract_multiplier(text: str) -> float | None:
    """Tìm bội số dạng '2.5x', '2,5x', '2x', '1.5X'."""
    pattern = r"(\d+[.,]?\d*)\s*[xX×]"
    match = re.search(pattern, text)
    if match:
        val = match.group(1).replace(",", ".")
        try:
            return float(val)
        except ValueError:
            return None
    return None


def _clean_name(text: str) -> str:
    """Làm sạch tên nhân vật từ OCR output."""
    # Bỏ các ký tự đặc biệt, số, dấu x/X
    cleaned = re.sub(r"[\d.,xX×%\[\]{}()|\\/_=+*&^%$#@!`~]", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _find_best_name_match(candidates: list[str], known_names: list[str]) -> str | None:
    """Fuzzy match tên từ OCR với danh sách tên đã biết."""
    if not known_names:
        return None

    best = None
    best_score = 0

    for candidate in candidates:
        cand_lower = candidate.lower()
        for known in known_names:
            known_lower = known.lower()
            # Simple substring match
            if known_lower in cand_lower or cand_lower in known_lower:
                score = len(set(known_lower.split()) & set(cand_lower.split()))
                if score > best_score:
                    best_score = score
                    best = known
    return best


def parse_game_screenshot(image_path: str, known_names: list[str] | None = None) -> dict:
    """
    Phân tích ảnh chụp màn hình game.
    Trả về dict với monsters và teacher đã trích xuất.
    """
    from PIL import Image

    reader = _get_reader()

    # OCR
    img = Image.open(image_path)
    results = reader.readtext(str(image_path), detail=1, paragraph=False)

    raw_text = [(text, conf) for _, text, conf in results]

    # Ghép text gần nhau theo vị trí để tìm cặp (tên + bội số)
    blocks = []
    for bbox, text, conf in results:
        if conf < 0.2:
            continue
        # Tọa độ trung tâm
        cx = sum(p[0] for p in bbox) / 4
        cy = sum(p[1] for p in bbox) / 4
        mult = _extract_multiplier(text)
        name = _clean_name(text)
        blocks.append({
            "text": text,
            "name": name,
            "multiplier": mult,
            "cx": cx,
            "cy": cy,
            "conf": conf,
        })

    # Nhóm các block có multiplier và tên gần nhau theo cột (x gần nhau)
    mult_blocks = [b for b in blocks if b["multiplier"] is not None]
    name_blocks = [b for b in blocks if b["multiplier"] is None and len(b["name"]) > 1]

    # Ghép tên với bội số theo proximity
    pairs = []
    used_names = set()
    for mb in mult_blocks:
        best_name_block = None
        best_dist = float("inf")
        for nb in name_blocks:
            if id(nb) in used_names:
                continue
            dist = ((mb["cx"] - nb["cx"])**2 + (mb["cy"] - nb["cy"])**2)**0.5
            if dist < best_dist and dist < 300:  # pixels threshold
                best_dist = dist
                best_name_block = nb
        if best_name_block:
            used_names.add(id(best_name_block))
            # Nếu có known_names, thử match
            final_name = best_name_block["name"]
            if known_names:
                matched = _find_best_name_match([final_name], known_names)
                if matched:
                    final_name = matched
            pairs.append({
                "name": final_name,
                "multiplier": mb["multiplier"],
                "cy": mb["cy"],
            })

    # Sắp xếp theo vị trí Y (từ trên xuống)
    pairs.sort(key=lambda x: x["cy"])

    # Confidence tổng thể
    avg_conf = sum(b["conf"] for b in blocks) / len(blocks) if blocks else 0

    return {
        "pairs": pairs,
        "raw_text": raw_text,
        "confidence": avg_conf,
        "blocks": blocks,
    }


def extract_game_info(image_path: str, known_names: list[str] | None = None) -> dict:
    """
    Interface chính: trích xuất thông tin game từ ảnh.
    Trả về dict với monsters (4 con) và teacher.

    Nếu OCR không tự động xác định được, trả về partial data để user xác nhận.
    """
    result = parse_game_screenshot(image_path, known_names)
    pairs = result["pairs"]

    monsters = []
    teacher = None

    # Heuristic: teacher thường có bội số thấp hơn, hoặc ở vị trí riêng
    # Với 5 pairs: 4 monsters + 1 teacher
    if len(pairs) >= 5:
        # Teacher thường là người có bội số thấp nhất hoặc xuất hiện riêng lẻ
        sorted_by_mult = sorted(pairs, key=lambda x: x["multiplier"])
        # Teacher thường ở vị trí đặc biệt, tạm thời lấy index cuối
        for i, p in enumerate(pairs[:4]):
            monsters.append({"name": p["name"], "multiplier": p["multiplier"]})
        teacher = {"name": pairs[4]["name"], "multiplier": pairs[4]["multiplier"]}
    elif len(pairs) == 4:
        # Chỉ có 4 entries, không đủ (cần 5)
        for p in pairs[:4]:
            monsters.append({"name": p["name"], "multiplier": p["multiplier"]})
    elif len(pairs) > 0:
        for p in pairs:
            monsters.append({"name": p["name"], "multiplier": p["multiplier"]})

    # Đảm bảo đủ 4 monsters
    while len(monsters) < 4:
        monsters.append({"name": f"Yêu quái {len(monsters)+1}", "multiplier": 1.0})

    if teacher is None:
        teacher = {"name": "Sư Phụ", "multiplier": 1.0}

    return {
        "monsters": monsters[:4],
        "teacher": teacher,
        "raw_text": result["raw_text"],
        "confidence": result["confidence"],
        "is_partial": len(pairs) < 5 or result["confidence"] < 0.5,
    }
