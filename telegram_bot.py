"""
Telegram bot cho Sư Phụ Chạy Mau predictor.

Luồng chính:
1. /manual → nhập tay thông tin trận → dự đoán
2. /result <tên> → lưu kết quả trận vừa dự đoán
3. /stats → thống kê tổng quát
4. /cancel → hủy thao tác hiện tại
"""

import os
import logging
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

import database as db
import predictor as pred
from config import (
    TELEGRAM_TOKEN, ALLOWED_CHAT_ID, KNOWN_MONSTERS,
    TEACHER_DEFAULT, display_name,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ConversationHandler states
(
    SELECT_MONSTERS,
    ENTER_MULTS,
    AWAITING_RESULT,
) = range(3)

LAST_ROUND_ID_KEY = "last_round_id"


def _check_allowed(update: Update) -> bool:
    if not ALLOWED_CHAT_ID:
        return True
    return str(update.effective_chat.id) == str(ALLOWED_CHAT_ID)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_allowed(update):
        return
    await update.message.reply_text(
        "🎮 *Bot Dự Đoán Sư Phụ Chạy Mau*\n\n"
        "Các lệnh:\n"
        "• /manual — Nhập thông tin trận thủ công\n"
        "• /result — Nhập kết quả sau khi trận kết thúc\n"
        "• /stats — Xem thống kê\n"
        "• /cancel — Hủy thao tác hiện tại\n\n"
        "💡 Sau mỗi trận hãy dùng /result để lưu lại kết quả giúp bot học!",
        parse_mode=ParseMode.MARKDOWN
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_allowed(update):
        return ConversationHandler.END
    # Nếu vừa dự đoán mà chưa nhập kết quả thì trận đó là rác → xóa luôn.
    last_id = context.user_data.get(LAST_ROUND_ID_KEY)
    deleted = db.delete_round(last_id) if last_id else False
    context.user_data.clear()

    msg = "✅ Đã hủy."
    if deleted:
        msg += f" Đã xóa trận #{last_id} (chưa có kết quả)."
    msg += " Dùng /manual để bắt đầu lại."
    await update.message.reply_text(msg)
    return ConversationHandler.END


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_allowed(update):
        return
    overall = db.get_overall_stats()
    recent = db.get_recent_rounds(5)

    text = (
        "📊 *THỐNG KÊ TỔNG QUÁT*\n\n"
        f"📋 Tổng trận đã có kết quả: {overall['total']}\n"
        f"👹 Yêu quái thắng: {overall['monster_wins']} ({100-overall['teacher_win_rate']:.1f}%)\n"
        f"👨‍🏫 Thầy thoát: {overall['teacher_wins']} ({overall['teacher_win_rate']:.1f}%)\n"
        f"⏳ Chờ nhập kết quả: {overall['pending']}\n"
    )

    # ── Mô hình & EV (đọc từ file cache — RẺ, không replay 17s) ──
    text += _stats_ev_section()

    if recent:
        text += "\n📅 *5 trận gần nhất:*\n"
        for r in recent:
            if r["winner"]:
                w_label = "Thầy thoát" if r["winner"] == "teacher" else display_name(r[f"{r['winner']}_name"])
                text += f"• {r['created_at'][:16]} → {w_label}\n"
            else:
                text += f"• {r['created_at'][:16]} → chưa có kết quả\n"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


def _stats_ev_section() -> str:
    """Phần EV/ROI cho /stats — chỉ ĐỌC tuned_params.json + model_picks_cache.json
    và tổng hợp nhanh win-rate theo bội (KHÔNG predict/replay) nên rất nhẹ, không
    đụng race với DB như khi chạy leave-one-out."""
    import strategy_analysis as sa
    out = ""

    # 1) Tham số tune + ROI walk-forward (tiêu chí chọn tham số).
    try:
        path = os.path.join(os.path.dirname(__file__), "tuned_params.json")
        with open(path, encoding="utf-8") as f:
            tp = json.load(f)
        meta = tp.get("_meta", {})
        out += "\n💰 *MÔ HÌNH & EV*\n"
        out += (f"⚙️ Tham số: ind={tp.get('INDIVIDUAL_PRIOR_STRENGTH','?')} "
                f"odds={tp.get('ODDS_CALIB_STRENGTH','?')} "
                f"tên×bội={tp.get('NAME_ODDS_STRENGTH','?')}\n")
        roi_wf = meta.get("roi_ev")
        if isinstance(roi_wf, (int, float)):
            out += f"📈 ROI/trận (walk-forward, tiêu chí tune): {roi_wf*100:+.0f}%\n"
    except (OSError, ValueError):
        pass

    # 2) ROI leave-one-out từ cache (nếu còn khớp số trận hiện tại).
    try:
        rounds = sa.load_rounds()
        n = len(rounds)
        picks = sa.load_model_picks_cache(n)
        if picks:
            m = sa.aggregate_model_strategies(picks)[0]
            out += (f"🎯 ROI/trận (leave-one-out): {m['roi']*100:+.0f}% "
                    f"(trúng {m['hit_rate']*100:.0f}%)\n")
        else:
            out += "🎯 ROI leave-one-out: _data vừa đổi — mở web bấm tính lại_\n"
    except Exception:
        rounds = None

    # 3) Bội số +EV theo lịch sử (tổng hợp nhanh, không replay).
    try:
        if rounds and len(rounds) >= 15:
            pos = [d for d in sa.compute_odds_winrate(rounds)
                   if d["roi"] > 0.05 and d["appeared"] >= 5]
            pos.sort(key=lambda d: d["roi"], reverse=True)
            if pos:
                out += "\n🔥 *Bội số +EV (lịch sử yêu quái):*\n"
                for d in pos[:3]:
                    out += (f"• Bội {d['odds']}: về {d['win_rate']*100:.0f}% "
                            f"({d['won']}/{d['appeared']}), EV {d['ev']:.2f}\n")
            tpos = [d for d in sa.compute_teacher_by_odds(rounds)
                    if d["roi"] > 0.05 and d["appeared"] >= 5]
            tpos.sort(key=lambda d: d["roi"], reverse=True)
            if tpos:
                d = tpos[0]
                out += (f"👨‍🏫 Thầy bội {d['odds']}: thoát {d['escape_rate']*100:.0f}% "
                        f"({d['escaped']}/{d['appeared']}), EV {d['ev']:.2f}\n")
            if pos or tpos:
                out += "_⚠️ Ít mẫu → variance cao, chỉ tham khảo._\n"
    except Exception:
        pass

    return out


# ─── Nhập tay: chọn yêu quái bằng nút + nhập bội số 1 dòng ───

MANUAL_SELECTED_KEY = "manual_selected"


async def _parse_mult(text: str) -> float | None:
    try:
        return float(text.replace(",", ".").replace("x", "").replace("X", "").strip())
    except ValueError:
        return None


def _monster_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    """Bàn phím chọn yêu quái: đánh số thứ tự cho con đã chọn, 2 cột."""
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    rows, row = [], []
    for name in KNOWN_MONSTERS:
        if name in selected:
            label = f"{nums[selected.index(name)]} {display_name(name)}"
        else:
            label = display_name(name)
        row.append(InlineKeyboardButton(label, callback_data=f"pick|{name}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    ctrl = []
    if selected:
        ctrl.append(InlineKeyboardButton("↩️ Xóa hết", callback_data="pick_reset"))
    if len(selected) == 4:
        ctrl.append(InlineKeyboardButton("✅ Xong", callback_data="pick_done"))
    if ctrl:
        rows.append(ctrl)
    return InlineKeyboardMarkup(rows)


async def manual_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _check_allowed(update):
        return
    context.user_data[MANUAL_SELECTED_KEY] = []
    await update.message.reply_text(
        "✏️ *Chọn 4 yêu quái* (bấm vào tên — bấm lại để bỏ chọn).\n"
        "Chọn đủ 4 rồi bấm *✅ Xong*. /cancel để hủy.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_monster_keyboard([]),
    )
    return SELECT_MONSTERS


async def manual_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    selected = context.user_data.setdefault(MANUAL_SELECTED_KEY, [])
    data = query.data

    if data == "pick_reset":
        selected.clear()
        await query.answer("Đã xóa lựa chọn")
        await query.edit_message_reply_markup(_monster_keyboard(selected))
        return SELECT_MONSTERS

    if data == "pick_done":
        if len(selected) != 4:
            await query.answer("Cần chọn đúng 4 yêu quái", show_alert=True)
            return SELECT_MONSTERS
        await query.answer()
        lines = "\n".join(f"{i+1}. {display_name(n)}" for i, n in enumerate(selected))
        await query.edit_message_text(
            f"✅ Đã chọn:\n{lines}\n\n"
            "Giờ nhập *5 bội số* cách nhau dấu cách — 4 yêu quái (đúng thứ tự trên) + Thầy.\n"
            "Ví dụ: `3 4 10 12 20`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ENTER_MULTS

    # data == "pick|<name>": toggle
    name = data.split("|", 1)[1]
    if name in selected:
        selected.remove(name)
        await query.answer(f"Bỏ {name}")
    elif len(selected) < 4:
        selected.append(name)
        await query.answer(f"Chọn {name} ({len(selected)}/4)")
    else:
        await query.answer("Đã đủ 4 — bỏ bớt hoặc bấm Xong", show_alert=True)
        return SELECT_MONSTERS
    await query.edit_message_reply_markup(_monster_keyboard(selected))
    return SELECT_MONSTERS


async def manual_enter_mults(update: Update, context: ContextTypes.DEFAULT_TYPE):
    selected = context.user_data.get(MANUAL_SELECTED_KEY, [])
    if len(selected) != 4:
        await update.message.reply_text("Có lỗi, hãy /manual lại nhé.")
        return ConversationHandler.END

    tokens = update.message.text.replace(",", " ").split()
    vals = []
    for t in tokens:
        v = await _parse_mult(t)
        if v is not None:
            vals.append(v)
    if len(vals) != 5:
        await update.message.reply_text(
            "❌ Cần đúng *5 số* (4 yêu quái + Thầy), cách nhau dấu cách.\n"
            "Ví dụ: `3 4 10 12 20`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ENTER_MULTS

    monsters = [{"name": n, "multiplier": vals[i]} for i, n in enumerate(selected)]
    teacher = {"name": TEACHER_DEFAULT, "multiplier": vals[4]}
    await _do_prediction(update, context, monsters, teacher, source="telegram_manual")
    return ConversationHandler.END


# ─── Dự đoán & lưu kết quả ───────────────────────────────────

async def _do_prediction(update, context, monsters, teacher, source="telegram"):
    prediction = pred.predict(monsters, teacher)
    text = pred.format_prediction_text(prediction, monsters, teacher)

    # Lưu trận vào DB (chưa có winner)
    round_id = db.save_round(monsters, teacher, winner=None, source=source)
    context.user_data[LAST_ROUND_ID_KEY] = round_id

    # Tạo nút chọn kết quả nhanh
    keyboard = []
    row = []
    for i, m in enumerate(monsters, 1):
        row.append(InlineKeyboardButton(
            f"👹 {display_name(m['name'])}", callback_data=f"result|{round_id}|monster{i}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton(
        f"👨‍🏫 Thầy thoát ({display_name(teacher['name'])})", callback_data=f"result|{round_id}|teacher"
    )])

    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    await target.reply_text(
        f"📝 ID trận: `{round_id}`\n"
        "Sau khi trận kết thúc, bấm nút trên hoặc dùng `/result tên_người_thắng` để lưu!",
        parse_mode=ParseMode.MARKDOWN
    )


async def result_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, round_id_str, winner = query.data.split("|")
    round_id = int(round_id_str)

    db.update_winner(round_id, winner)
    r = db.get_round_by_id(round_id)

    if winner == "teacher":
        winner_label = f"👨‍🏫 Thầy thoát ({display_name(r['teacher_name'])})"
    else:
        winner_label = f"👹 {display_name(r[f'{winner}_name'])}"

    await query.edit_message_reply_markup(None)
    await query.message.reply_text(
        f"✅ Đã lưu kết quả trận #{round_id}: *{winner_label}*\n"
        "Cảm ơn! Bot sẽ học từ dữ liệu này 📈",
        parse_mode=ParseMode.MARKDOWN
    )


async def result_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /result <tên> — lưu kết quả cho trận cuối cùng.
    Ví dụ: /result Hồ Ly  hoặc  /result thầy
    """
    if not _check_allowed(update):
        return

    last_id = context.user_data.get(LAST_ROUND_ID_KEY)
    if not last_id:
        await update.message.reply_text("❌ Không tìm thấy trận vừa dự đoán. Dùng /manual trước.")
        return

    args = " ".join(context.args).strip().lower() if context.args else ""
    if not args:
        await update.message.reply_text(
            "Nhập tên người thắng. Ví dụ:\n"
            "`/result Hồ Ly` hoặc `/result thầy`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    r = db.get_round_by_id(last_id)
    if not r:
        await update.message.reply_text("❌ Không tìm thấy trận trong database.")
        return

    # Map tên → winner slot
    winner = None
    if args in ["thầy", "sư phụ", "teacher", r["teacher_name"].lower()]:
        winner = "teacher"
    else:
        for slot in ["monster1", "monster2", "monster3", "monster4"]:
            if args == r[f"{slot}_name"].lower():
                winner = slot
                break

    if winner is None:
        names = [r[f"monster{i}_name"] for i in range(1, 5)] + [r["teacher_name"]]
        await update.message.reply_text(
            f"❌ Không nhận ra '{args}'.\nCác tên hợp lệ: {', '.join(names)}"
        )
        return

    db.update_winner(last_id, winner)
    winner_label = f"Thầy thoát ({display_name(r['teacher_name'])})" if winner == "teacher" else display_name(r[f"{winner}_name"])
    await update.message.reply_text(
        f"✅ Đã lưu kết quả trận #{last_id}: *{winner_label}* 📈",
        parse_mode=ParseMode.MARKDOWN
    )


# ─── Main ────────────────────────────────────────────────────

def run_bot():
    db.init_db()

    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN chưa được đặt trong file .env!")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # ConversationHandler cho nhập tay (chọn nút + nhập bội số 1 dòng)
    manual_conv = ConversationHandler(
        entry_points=[CommandHandler("manual", manual_start)],
        states={
            SELECT_MONSTERS: [CallbackQueryHandler(manual_pick_callback, pattern=r"^pick")],
            ENTER_MULTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_enter_mults)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("result", result_command))
    app.add_handler(manual_conv)
    # /cancel toàn cục: hoạt động cả khi đã thoát conversation (sau khi dự đoán xong)
    # — lúc đó vẫn cho phép hủy & xóa trận rác chưa nhập kết quả.
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(result_callback, pattern=r"^result\|"))

    logger.info("Bot đang chạy...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run_bot()
