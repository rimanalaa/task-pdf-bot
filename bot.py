"""Telegram bot: collects logo (optional), name + ID (required), doctor (optional),
then the task-solution images in order, and sends back a ready PDF."""
import hashlib
import logging
import os
import re
import tempfile

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, PicklePersistence, filters,
)

from pdf_builder import build_pdf

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
HERE = os.path.dirname(os.path.abspath(__file__))

LOGO, NAME, STUDENT_ID, DOCTOR, IMAGES, REUSE = range(6)

SKIP = "⏭️ تخطي"
DONE = "✅ اعمل الـ PDF"
UNDO = "↩️ احذف آخر صورة"
REUSE_YES = "👍 نفس بياناتي"
REUSE_NO = "✏️ بيانات جديدة"
MAX_IMAGES = 60


def kb(*rows):
    return ReplyKeyboardMarkup([[KeyboardButton(b) for b in r] for r in rows], resize_keyboard=True)


IMAGE_FILTER = filters.PHOTO | filters.Document.IMAGE


async def _file_bytes(msg) -> bytes:
    f = await (msg.photo[-1].get_file() if msg.photo else msg.document.get_file())
    return bytes(await f.download_as_bytearray())


def _file_id(msg) -> str:
    return msg.photo[-1].file_id if msg.photo else msg.document.file_id


# ---------------- flow ----------------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["images"] = []
    p = ctx.user_data.get("profile")
    if p:
        doc = f"\nالدكتور: {p['doctor']}" if p.get("doctor") else ""
        logo = "\nاللوجو: موجود ✅" if p.get("logo") else ""
        await update.message.reply_text(
            f"أهلاً من جديد 👋\nآخر بيانات استخدمتها:\nالاسم: {p['name']}\nالرقم: {p['id']}{doc}{logo}\n\nبدك نفسها؟",
            reply_markup=kb([REUSE_YES, REUSE_NO]))
        return REUSE
    return await ask_logo(update, ctx)


async def reuse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text == REUSE_YES:
        ctx.user_data["draft"] = dict(ctx.user_data["profile"])
        return await ask_images(update, ctx)
    return await ask_logo(update, ctx)


async def ask_logo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["draft"] = {}
    await update.message.reply_text(
        "أهلاً 👋 رح نعمل ملف PDF لحل التاسك.\n\n1️⃣ ابعت اللوجو (اختياري) أو اضغط تخطي.",
        reply_markup=kb([SKIP]))
    return LOGO


async def got_logo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["draft"]["logo"] = await _file_bytes(update.message)
    return await ask_name(update, ctx, "تمام، وصل اللوجو ✅\n\n")


async def skip_logo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["draft"]["logo"] = None
    return await ask_name(update, ctx, "")


async def ask_name(update, ctx, prefix):
    await update.message.reply_text(prefix + "2️⃣ اكتب اسمك (إجباري):", reply_markup=ReplyKeyboardRemove())
    return NAME


async def got_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 2 or name in (SKIP, DONE):
        await update.message.reply_text("الاسم إجباري، اكتبه لو سمحت:")
        return NAME
    ctx.user_data["draft"]["name"] = name[:80]
    await update.message.reply_text("3️⃣ اكتب الرقم الجامعي / الـ ID (إجباري):")
    return STUDENT_ID


async def got_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sid = update.message.text.strip()
    if not sid or sid in (SKIP, DONE):
        await update.message.reply_text("الـ ID إجباري، اكتبه لو سمحت:")
        return STUDENT_ID
    ctx.user_data["draft"]["id"] = sid[:40]
    await update.message.reply_text("4️⃣ اسم الدكتور (اختياري) — اكتبه أو اضغط تخطي:", reply_markup=kb([SKIP]))
    return DOCTOR


async def got_doctor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ctx.user_data["draft"]["doctor"] = None if text == SKIP else text[:80]
    ctx.user_data["profile"] = dict(ctx.user_data["draft"])  # remember for next time
    return await ask_images(update, ctx)


async def ask_images(update, ctx):
    ctx.user_data["images"] = []
    await update.message.reply_text(
        "5️⃣ هلّق ابعت صور حل التاسك **بالترتيب** 📸\n"
        "(بتقدر تبعتهم كألبوم أو وحدة وحدة، وكـ File لجودة أعلى)\n\n"
        f"لما تخلص اضغط «{DONE}».",
        parse_mode="Markdown", reply_markup=kb([DONE], [UNDO]))
    return IMAGES


async def got_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    imgs = ctx.user_data.setdefault("images", [])
    if len(imgs) >= MAX_IMAGES:
        await update.message.reply_text(f"الحد الأقصى {MAX_IMAGES} صورة. اضغط «{DONE}».")
        return IMAGES
    # keep (message_id, file_id): message_id preserves the order she sent them, even for albums
    imgs.append((update.message.message_id, _file_id(update.message)))
    if not update.message.media_group_id or len(imgs) % 5 == 0:
        await update.message.reply_text(f"✅ وصلت صورة {len(imgs)}")
    return IMAGES


async def undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    imgs = sorted(ctx.user_data.get("images", []))
    if imgs:
        imgs.pop()
        ctx.user_data["images"] = imgs
        await update.message.reply_text(f"انحذفت آخر صورة. صار عندك {len(imgs)} صورة.")
    else:
        await update.message.reply_text("ما في صور لسا.")
    return IMAGES


async def done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    imgs = sorted(ctx.user_data.get("images", []))
    if not imgs:
        await update.message.reply_text("ابعت صورة وحدة على الأقل 📸")
        return IMAGES
    d = ctx.user_data["draft"]
    wait = await update.message.reply_text(f"⏳ عم جهّز الـ PDF ({len(imgs)} صورة)...", reply_markup=ReplyKeyboardRemove())
    try:
        datas = []
        for _, fid in imgs:
            f = await ctx.bot.get_file(fid)
            datas.append(bytes(await f.download_as_bytearray()))
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", f"{d['id']}_{d['name']}").strip("_")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, f"{safe}.pdf")
            build_pdf(path, d["name"], d["id"], datas, logo=d.get("logo"), doctor=d.get("doctor"))
            with open(path, "rb") as fh:
                await update.message.reply_document(fh, filename=f"{safe}.pdf", caption="جاهز ✅ بالتوفيق 🌟\nلتاسك جديد: /start")
    except Exception:
        logging.exception("PDF failed")
        await update.message.reply_text("صار خطأ وأنا بعمل الملف 😕 جرّب مرة ثانية: /start")
    finally:
        try:
            await wait.delete()
        except Exception:
            pass
    ctx.user_data["images"] = []
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["images"] = []
    await update.message.reply_text("انلغى. لتبدأ من جديد: /start", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def need_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"ابعت صورة، أو اضغط «{DONE}».")
    return IMAGES


async def need_logo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ابعت اللوجو كصورة، أو اضغط تخطي.")
    return LOGO


async def lost(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("ابدأ من جديد بـ /start 🙂")


def load_token():
    tok = os.environ.get("BOT_TOKEN")
    env = os.path.join(HERE, ".env")
    if not tok and os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.strip().startswith("BOT_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"')
    if not tok:
        raise SystemExit("حطي BOT_TOKEN=... بملف .env جنب bot.py")
    return tok


def main():
    persistence = PicklePersistence(os.path.join(HERE, "bot_data.pickle"))
    app = Application.builder().token(load_token()).persistence(persistence).build()
    text = filters.TEXT & ~filters.COMMAND
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            REUSE: [MessageHandler(filters.Text([REUSE_YES, REUSE_NO]), reuse)],
            LOGO: [MessageHandler(IMAGE_FILTER, got_logo), MessageHandler(filters.Text([SKIP]), skip_logo),
                   MessageHandler(~filters.COMMAND, need_logo)],
            NAME: [MessageHandler(text, got_name)],
            STUDENT_ID: [MessageHandler(text, got_id)],
            DOCTOR: [MessageHandler(text, got_doctor)],
            IMAGES: [MessageHandler(IMAGE_FILTER, got_image), MessageHandler(filters.Text([DONE]), done),
                     MessageHandler(filters.Text([UNDO]), undo), MessageHandler(~filters.COMMAND, need_image)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        name="task_pdf", persistent=True, allow_reentry=True,
    )
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, lost))  # e.g. after a server restart

    base = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if base:  # server: Telegram pushes updates to us (also wakes the free instance up)
        secret = hashlib.sha256(load_token().encode()).hexdigest()[:32]
        logging.info("Webhook mode on %s", base)
        app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", "10000")),
                        url_path="tg", webhook_url=base.rstrip("/") + "/tg", secret_token=secret,
                        drop_pending_updates=False)
    else:  # laptop
        logging.info("Polling mode")
        app.run_polling()


if __name__ == "__main__":
    main()
