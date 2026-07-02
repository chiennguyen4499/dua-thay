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
import argparse
import database as db

db.init_db()


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
