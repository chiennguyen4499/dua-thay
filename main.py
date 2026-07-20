"""
Entry point: chạy Telegram bot và Web UI đồng thời (2 thread riêng).

Cách dùng:
  python main.py          — chạy cả hai
  python main.py --bot    — chỉ chạy Telegram bot
  python main.py --web    — chỉ chạy Web UI
"""

import sys
import threading
import subprocess
import os
import json
import argparse
import database as db

db.init_db()


def _sync_tuned_params_from_meta():
    """Kéo `tuned_params` từ Turso (bảng meta dùng chung) về file local + reload
    config, để bot (PC) dùng bộ tham số mới nhất — kể cả khi bộ đó do auto-retune
    trên Web (Cloud) sinh ra. Chạy TRƯỚC khi import telegram_bot (→ predictor →
    config) nên config sẽ đọc file đã cập nhật. Lỗi mạng → bỏ qua, dùng file sẵn có."""
    try:
        meta_tp = db.get_meta("tuned_params")
        if not meta_tp:
            return
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tuned_params.json")
        try:
            with open(path, encoding="utf-8") as f:
                cur = json.load(f)
        except (OSError, ValueError):
            cur = None
        if cur != meta_tp:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(meta_tp, f, ensure_ascii=False, indent=2)
            import importlib, config
            importlib.reload(config)
            print("[OK] Da dong bo tuned_params tu Turso (meta) ve local.")
    except Exception as e:
        print(f"[WARN] Khong dong bo duoc tuned_params tu meta: {e}")


_sync_tuned_params_from_meta()


def run_bot():
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    from telegram_bot import run_bot as _run
    print("Telegram bot dang khoi dong...")
    _run()


def run_web():
    print("Web UI: http://localhost:8501")
    subprocess.run([
        sys.executable, "-m", "streamlit", "run", "web_app.py",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ])


def main():
    parser = argparse.ArgumentParser(description="Sư Phụ Chạy Mau Predictor")
    parser.add_argument("--bot", action="store_true", help="Chỉ chạy Telegram bot")
    parser.add_argument("--web", action="store_true", help="Chỉ chạy Web UI")
    args = parser.parse_args()

    if args.bot:
        run_bot()
    elif args.web:
        run_web()
    else:
        # Chạy cả hai
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        run_web()  # Web chạy ở main thread


if __name__ == "__main__":
    main()
