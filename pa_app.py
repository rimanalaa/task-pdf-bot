"""Telegram task-PDF bot as a small Flask webhook app (for PythonAnywhere's free plan).

Same flow as bot.py: logo (optional) -> name (required) -> ID (required) -> doctor (optional)
-> solution images in order -> PDF. State is kept in a JSON file, so it survives restarts.

Setup on the server:
  - put BOT_TOKEN=... in .env next to this file
  - WSGI file: `from pa_app import app as application`
  - open https://<user>.pythonanywhere.com/setup/<first 12 chars of sha256(token)> once
"""
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time

import requests
from flask import Flask, abort, request

from pdf_builder import build_pdf

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
log = logging.getLogger("taskpdf")


def load_token():
    tok = os.environ.get("BOT_TOKEN")
    env = os.path.join(HERE, ".env")
    if not tok and os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.strip().startswith("BOT_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"')
    return tok


TOKEN = load_token() or ""
API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"
SECRET = hashlib.sha256(TOKEN.encode()).hexdigest()
HOOK_PATH = "/hook/" + SECRET[:24]

SKIP = "⏭️ تخطي"
DONE = "✅ اعمل الـ PDF"
UNDO = "↩️ احذف آخر صورة"
REUSE_YES = "👍 نفس بياناتي"
REUSE_NO = "✏️ بيانات جديدة"
MAX_IMAGES = 60

app = Flask(__name__)
_lock = threading.Lock()


# ---------------- storage ----------------
def _load():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"users": {}, "seen": []}


def _save(db):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


# ---------------- telegram helpers ----------------
def tg(method, **params):
    """Call the Bot API. PythonAnywhere's free proxy sometimes answers 503, so retry."""
    files = params.pop("_files", None)
    for attempt in range(5):
        try:
            if files:
                for f in files.values():
                    f[1].seek(0)
            r = requests.post(f"{API}/{method}", data=params if files else None,
                              json=None if files else params, files=files, timeout=60)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.ConnectionError(f"HTTP {r.status_code}")
            if not r.ok:
                log.warning("%s failed: %s", method, r.text[:300])
            return r.json() if r.content else {}
        except requests.RequestException as e:
            log.warning("%s attempt %d failed: %s", method, attempt + 1, type(e).__name__)
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"Telegram {method} failed after retries")


def kb(*rows):
    return {"keyboard": [[{"text": b} for b in r] for r in rows], "resize_keyboard": True}


NO_KB = {"remove_keyboard": True}


def say(chat, text, markup=None, md=False):
    p = {"chat_id": chat, "text": text}
    if markup is not None:
        p["reply_markup"] = markup
    if md:
        p["parse_mode"] = "Markdown"
    return tg("sendMessage", **p)


def download(file_id) -> bytes:
    info = tg("getFile", file_id=file_id)
    path = info["result"]["file_path"]
    for attempt in range(5):
        try:
            r = requests.get(f"{FILE_API}/{path}", timeout=60)
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(0.6 * (attempt + 1))


def image_file_id(msg):
    if msg.get("photo"):
        return msg["photo"][-1]["file_id"]
    doc = msg.get("document")
    if doc and (doc.get("mime_type") or "").startswith("image/"):
        return doc["file_id"]
    return None


# ---------------- flow ----------------
def ask_logo(chat, u):
    u.update(state="LOGO", draft={}, images=[])
    say(chat, "أهلاً 👋 رح نعمل ملف PDF لحل التاسك.\n\n1️⃣ ابعت اللوجو (اختياري) أو اضغط تخطي.", kb([SKIP]))


def ask_images(chat, u):
    u.update(state="IMAGES", images=[])
    say(chat, "5️⃣ هلّق ابعت صور حل التاسك *بالترتيب* 📸\n"
              "(بتقدر تبعتهم كألبوم أو وحدة وحدة، وكـ File لجودة أعلى)\n\n"
              f"لما تخلص اضغط «{DONE}».", kb([DONE], [UNDO]), md=True)


def start(chat, u):
    p = u.get("profile")
    if p:
        doc = f"\nالدكتور: {p['doctor']}" if p.get("doctor") else ""
        logo = "\nاللوجو: موجود ✅" if p.get("logo") else ""
        u["state"] = "REUSE"
        say(chat, f"أهلاً من جديد 👋\nآخر بيانات استخدمتها:\nالاسم: {p['name']}\nالرقم: {p['id']}{doc}{logo}\n\nبدك نفسها؟",
            kb([REUSE_YES, REUSE_NO]))
    else:
        ask_logo(chat, u)


def make_pdf(chat, u):
    imgs = sorted(u.get("images", []))
    if not imgs:
        say(chat, "ابعت صورة وحدة على الأقل 📸")
        return
    d = u["draft"]
    say(chat, f"⏳ عم جهّز الـ PDF ({len(imgs)} صورة)...", NO_KB)
    try:
        datas = [download(fid) for _, fid in imgs]
        logo = download(d["logo"]) if d.get("logo") else None
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", f"{d['id']}_{d['name']}").strip("_")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, f"{safe}.pdf")
            build_pdf(path, d["name"], d["id"], datas, logo=logo, doctor=d.get("doctor"))
            with open(path, "rb") as fh:
                tg("sendDocument", chat_id=chat, caption="جاهز ✅ بالتوفيق 🌟\nلتاسك جديد: /start",
                   _files={"document": (f"{safe}.pdf", fh, "application/pdf")})
    except Exception:
        log.exception("PDF failed")
        say(chat, "صار خطأ وأنا بعمل الملف 😕 جرّب مرة ثانية: /start")
    u.update(state=None, images=[])


def handle(msg, u):
    chat = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    bare = text.replace("️", "")
    for label in (SKIP, DONE, UNDO, REUSE_YES, REUSE_NO):  # some clients drop the emoji variation selector
        if bare == label.replace("️", ""):
            text = label
    fid = image_file_id(msg)
    st = u.get("state")

    if text in ("/start", "/new"):
        return start(chat, u)
    if text == "/cancel":
        u.update(state=None, images=[])
        return say(chat, "انلغى. لتبدأ من جديد: /start", NO_KB)

    if st == "REUSE":
        if text == REUSE_YES:
            u["draft"] = dict(u["profile"])
            return ask_images(chat, u)
        if text == REUSE_NO:
            return ask_logo(chat, u)
        return say(chat, "اختار من الأزرار تحت 👇")

    if st == "LOGO":
        if fid:
            u["draft"]["logo"] = fid
            prefix = "تمام، وصل اللوجو ✅\n\n"
        elif text == SKIP:
            u["draft"]["logo"] = None
            prefix = ""
        else:
            return say(chat, "ابعت اللوجو كصورة، أو اضغط تخطي.")
        u["state"] = "NAME"
        return say(chat, prefix + "2️⃣ اكتب اسمك (إجباري):", NO_KB)

    if st == "NAME":
        if len(text) < 2 or text.startswith("/") or text in (SKIP, DONE):
            return say(chat, "الاسم إجباري، اكتبه لو سمحت:")
        u["draft"]["name"] = text[:80]
        u["state"] = "ID"
        return say(chat, "3️⃣ اكتب الرقم الجامعي / الـ ID (إجباري):")

    if st == "ID":
        if not text or text.startswith("/") or text in (SKIP, DONE):
            return say(chat, "الـ ID إجباري، اكتبه لو سمحت:")
        u["draft"]["id"] = text[:40]
        u["state"] = "DOCTOR"
        return say(chat, "4️⃣ اسم الدكتور (اختياري) — اكتبه أو اضغط تخطي:", kb([SKIP]))

    if st == "DOCTOR":
        if not text:
            return say(chat, "اكتب اسم الدكتور أو اضغط تخطي.")
        u["draft"]["doctor"] = None if text == SKIP else text[:80]
        u["profile"] = dict(u["draft"])  # remember for next time
        return ask_images(chat, u)

    if st == "IMAGES":
        imgs = u.setdefault("images", [])
        if fid:
            if len(imgs) >= MAX_IMAGES:
                return say(chat, f"الحد الأقصى {MAX_IMAGES} صورة. اضغط «{DONE}».")
            imgs.append([msg["message_id"], fid])  # message_id keeps the sending order (albums too)
            if not msg.get("media_group_id") or len(imgs) % 5 == 0:
                say(chat, f"✅ وصلت صورة {len(imgs)}")
            return
        if text == DONE:
            return make_pdf(chat, u)
        if text == UNDO:
            imgs.sort()
            if imgs:
                imgs.pop()
                return say(chat, f"انحذفت آخر صورة. صار عندك {len(imgs)} صورة.")
            return say(chat, "ما في صور لسا.")
        return say(chat, f"ابعت صورة، أو اضغط «{DONE}».")

    return say(chat, "ابدأ بـ /start 🙂")


# ---------------- routes ----------------
@app.route("/")
def index():
    return "Task PDF bot is running ✅"


@app.route(HOOK_PATH, methods=["POST"])
def hook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRET[:32]:
        abort(403)
    upd = request.get_json(silent=True) or {}
    msg = upd.get("message")
    if not msg or msg.get("chat", {}).get("type") != "private":
        return "ok"
    with _lock:
        db = _load()
        uid = upd.get("update_id")
        if uid in db["seen"]:  # Telegram retry of an update we already handled
            return "ok"
        db["seen"] = (db["seen"] + [uid])[-300:]
        _save(db)
        key = str(msg["chat"]["id"])
        u = db["users"].setdefault(key, {})
        try:
            handle(msg, u)
        except Exception:
            log.exception("handler failed")
        db = _load()
        db["users"][key] = u
        _save(db)
    return "ok"


@app.route("/setup/<code>")
def setup(code):
    if not TOKEN or code != SECRET[:12]:
        abort(404)
    url = request.host_url.rstrip("/").replace("http://", "https://") + HOOK_PATH
    r = tg("setWebhook", url=url, secret_token=SECRET[:32], allowed_updates=["message"],
           drop_pending_updates=True)
    me = tg("getMe")
    return {"setWebhook": r, "bot": me.get("result", {}).get("username")}


@app.route("/setup/<code>/profile")
def setup_profile(code):
    """Applies set_profile.py from inside the web app (its proxy is more reliable than consoles)."""
    if not TOKEN or code != SECRET[:12]:
        abort(404)
    import set_profile
    return {"results": set_profile.apply()}
