"""Telegram task-PDF bot as a small Flask webhook app (for PythonAnywhere's free plan).

Flow: up to 3 logos, name, ID, doctor, year, page frame (all optional)
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

from urllib.parse import quote

import requests
from flask import Flask, abort, request, send_file

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
LOGOS_DONE = "✅ خلصت اللوجوهات"
LOGO_MORE = "➕ في لوجو كمان"
RETRY = "🔁 جرّب مرة ثانية"
FRAME_YES = "🖼️ مع إطار"
FRAME_NO = "⬜ بدون إطار"
BUTTONS = (SKIP, DONE, UNDO, REUSE_YES, REUSE_NO, LOGOS_DONE, LOGO_MORE, RETRY, FRAME_YES, FRAME_NO)
MAX_IMAGES = 60
MAX_LOGOS = 3

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
_http = requests.Session()  # reuses the proxy tunnel between calls -> far fewer 503s


def tg(method, _tries=8, **params):
    """Call the Bot API. PythonAnywhere's free proxy sometimes answers 503, so retry."""
    files = params.pop("_files", None)
    for attempt in range(_tries):
        try:
            if files:
                for f in files.values():
                    f[1].seek(0)
            r = _http.post(f"{API}/{method}", data=params if files else None,
                              json=None if files else params, files=files, timeout=60)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.ConnectionError(f"HTTP {r.status_code}")
            if not r.ok:
                log.warning("%s failed: %s", method, r.text[:300])
            return r.json() if r.content else {}
        except requests.RequestException as e:
            log.warning("%s attempt %d failed: %s", method, attempt + 1, type(e).__name__)
            time.sleep(min(0.5 * (attempt + 1), 3))
    raise RuntimeError(f"Telegram {method} failed after retries")


def kb(*rows):
    return {"keyboard": [[{"text": b} for b in r] for r in rows], "resize_keyboard": True}


NO_KB = {"remove_keyboard": True}


_ctx = threading.local()


def say(chat, text, markup=None, md=False, now=False):
    """Queue a message. Inside a webhook the last queued message is returned in the HTTP
    response itself (instant, bypasses PythonAnywhere's flaky outbound proxy)."""
    p = {"chat_id": chat, "text": text}
    if markup is not None:
        p["reply_markup"] = markup
    if md:
        p["parse_mode"] = "Markdown"
    q = getattr(_ctx, "queue", None)
    if q is None or now:
        flush()
        try:
            return tg("sendMessage", **p)
        except Exception:
            log.exception("sendMessage lost")
            return {}
    q.append(p)
    return {}


def flush(keep_last=False):
    """Send queued messages over the API (all, or all but the last)."""
    q = getattr(_ctx, "queue", None)
    if not q:
        return
    send = q[:-1] if keep_last else q[:]
    del q[:len(send)]
    for p in send:
        try:
            tg("sendMessage", **p)
        except Exception:
            log.exception("sendMessage lost")


CACHE = os.path.join(HERE, "cache")
PDF_DIR = os.path.join(HERE, "pdfs")


def _cache_path(file_id):
    return os.path.join(CACHE, hashlib.sha1(file_id.encode()).hexdigest()[:24] + ".img")


def download(file_id, tries=10) -> bytes:
    """Bytes of a Telegram file. Cached on disk: images are fetched as they arrive, so the
    PDF step usually needs no network at all (the free proxy fails a lot)."""
    cp = _cache_path(file_id)
    if os.path.exists(cp):
        with open(cp, "rb") as f:
            return f.read()
    info = tg("getFile", _tries=tries, file_id=file_id)
    path = info["result"]["file_path"]
    for attempt in range(tries):
        try:
            r = _http.get(f"{FILE_API}/{path}", timeout=40)
            r.raise_for_status()
            os.makedirs(CACHE, exist_ok=True)
            with open(cp + ".tmp", "wb") as f:
                f.write(r.content)
            os.replace(cp + ".tmp", cp)
            return r.content
        except requests.RequestException as e:  # never log the URL: it contains the token
            log.warning("download attempt %d failed: %s", attempt + 1, type(e).__name__)
            time.sleep(min(0.5 * (attempt + 1), 3))
    raise RuntimeError("download failed after retries")


def prefetch(file_id):
    """Best-effort quick download when an image arrives (keeps replies fast)."""
    try:
        download(file_id, tries=2)
    except Exception:
        pass


def cleanup(file_ids):
    for fid in file_ids:
        try:
            os.remove(_cache_path(fid))
        except OSError:
            pass
    now = time.time()
    for d in (CACHE, PDF_DIR):
        for fn in os.listdir(d) if os.path.isdir(d) else ():
            p = os.path.join(d, fn)
            try:
                if now - os.path.getmtime(p) > 6 * 3600:
                    os.remove(p)
            except OSError:
                pass


def image_file_id(msg):
    if msg.get("photo"):
        return msg["photo"][-1]["file_id"]
    doc = msg.get("document")
    if doc and (doc.get("mime_type") or "").startswith("image/"):
        return doc["file_id"]
    return None


# ---------------- flow ----------------
# LOGO -> NAME -> ID -> DOCTOR -> YEAR -> FRAME -> IMAGES ; everything except the images is optional
def year_buttons():
    now = time.gmtime()
    acad = f"{now.tm_year}/{now.tm_year + 1}" if now.tm_mon >= 8 else f"{now.tm_year - 1}/{now.tm_year}"
    return kb([acad, str(now.tm_year)], [SKIP])


def ask_logo(chat, u):
    u.update(state="LOGO", draft={"logos": []}, images=[])
    say(chat, "أهلاً 👋 رح نعمل ملف PDF لحل التاسك.\n\n"
              f"1️⃣ ابعت اللوجو (لحد {MAX_LOGOS} لوجوهات، اختياري) أو اضغط تخطي.", kb([SKIP]))


def ask_name(chat, u, prefix=""):
    u["state"] = "NAME"
    say(chat, prefix + "2️⃣ اكتب اسمك (اختياري):", kb([SKIP]))


def ask_frame(chat, u):
    u["state"] = "FRAME"
    say(chat, "6️⃣ بدك إطار حول صفحات الحل؟", kb([FRAME_YES, FRAME_NO]))


def ask_images(chat, u):
    u.update(state="IMAGES", images=[])
    say(chat, "7️⃣ هلّق ابعت صور حل التاسك *بالترتيب* 📸\n"
              "(بتقدر تبعتهم كألبوم أو وحدة وحدة، وكـ File لجودة أعلى)\n\n"
              f"لما تخلص اضغط «{DONE}».", kb([DONE], [UNDO]), md=True)


def summary(p):
    rows = [("الاسم", p.get("name")), ("الرقم", p.get("id")), ("الدكتور", p.get("doctor")),
            ("السنة", p.get("year"))]
    out = [f"{k}: {v}" for k, v in rows if v]
    if p.get("logos"):
        out.append(f"اللوجوهات: {len(p['logos'])} ✅")
    out.append("الإطار: " + ("مع إطار" if p.get("frame") else "بدون"))
    return "\n".join(out)


def start(chat, u):
    p = u.get("profile")
    if p:
        if p.get("logo") and not p.get("logos"):  # profile saved by the older version
            p["logos"] = [[0, p["logo"]]]
        u["state"] = "REUSE"
        say(chat, f"أهلاً من جديد 👋\nآخر بيانات استخدمتها:\n{summary(p)}\n\nبدك نفسها؟",
            kb([REUSE_YES, REUSE_NO]))
    else:
        ask_logo(chat, u)


def default_name(d):
    return "_".join(x for x in (d.get("id"), d.get("name")) if x) or "حل_التاسك"


def safe_name(text):
    text = re.sub(r"\.pdf$", "", (text or "").strip(), flags=re.I)
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_.")[:60] or "حل_التاسك"


def ask_filename(chat, u):
    if not u.get("images"):
        return say(chat, "ابعت صورة وحدة على الأقل 📸")
    u["state"] = "FILENAME"
    say(chat, "📝 شو بدك يكون اسم ملف الـ PDF؟\nاكتبه، أو اختار الاسم المقترح 👇",
        kb([safe_name(default_name(u["draft"]))]))


def make_pdf(chat, u, fname):
    imgs = sorted(u.get("images", []))
    d = u["draft"]
    logo_ids = [fid for _, fid in sorted(d.get("logos") or [])]
    say(chat, f"⏳ عم جهّز الـ PDF ({len(imgs)} صورة)...", NO_KB, now=True)
    try:
        datas = [download(fid) for _, fid in imgs]
        logos = [download(fid) for fid in logo_ids]
    except Exception as e:
        log.error("PDF download failed: %s", type(e).__name__)
        u["fname"] = fname
        return say(chat, "😕 السيرفر ما قدر يحمّل الصور من تيليجرام هلّق (ضغط على الشبكة).\n"
                         "صورك محفوظة، اضغط «🔁 جرّب مرة ثانية» بعد شوي.", kb([RETRY], [UNDO]))
    os.makedirs(PDF_DIR, exist_ok=True)
    key = hashlib.sha1(f"{chat}{time.time()}".encode()).hexdigest()[:16]
    path = os.path.join(PDF_DIR, f"{key}.pdf")
    try:
        build_pdf(path, d.get("name"), d.get("id"), datas, logos=logos, doctor=d.get("doctor"),
                  year=d.get("year"), frame=bool(d.get("frame")))
    except Exception:
        log.exception("PDF build failed")
        u.update(state=None, images=[])
        return say(chat, "صار خطأ وأنا بعمل الملف 😕 جرّب مرة ثانية: /start", NO_KB)
    caption = "جاهز ✅ بالتوفيق 🌟\nلتاسك جديد: /start"
    try:
        with open(path, "rb") as fh:
            r = tg("sendDocument", _tries=4, chat_id=chat, caption=caption,
                   _files={"document": (f"{fname}.pdf", fh, "application/pdf")})
        if not r.get("ok"):
            raise RuntimeError(r.get("description"))
        os.remove(path)
    except Exception as e:
        # proxy down: let Telegram fetch the PDF from us, inside the webhook reply
        log.warning("upload failed (%s), sending by URL", type(e).__name__)
        url = request.host_url.rstrip("/").replace("http://", "https://")
        _ctx.reply = {"method": "sendDocument", "chat_id": chat, "caption": caption,
                      "document": f"{url}/pdf/{key}/{quote(fname)}.pdf"}
    cleanup(fid for _, fid in imgs)
    u.update(state=None, images=[])


def optional_text(text):
    """(ok, value): SKIP -> (True, None); empty/command/other button -> (False, None)."""
    if text == SKIP:
        return True, None
    if not text or text.startswith("/") or text in BUTTONS:
        return False, None
    return True, text


def handle(msg, u):
    chat = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    bare = text.replace("️", "")
    for label in BUTTONS:  # some clients drop the emoji variation selector
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
        logos = u["draft"].setdefault("logos", [])
        if fid:
            logos.append([msg["message_id"], fid])
            prefetch(fid)
            n = len(logos)
            if n >= MAX_LOGOS:
                return ask_name(chat, u, f"تمام، وصلوا {MAX_LOGOS} لوجوهات ✅\n\n")
            if msg.get("media_group_id") and n > 1:
                return  # rest of an album: one question is enough
            return say(chat, f"✅ وصل اللوجو {n}.\nفي لوجو كمان؟", kb([LOGO_MORE, LOGOS_DONE]))
        if text == LOGO_MORE:
            return say(chat, f"تمام، ابعت اللوجو {len(logos) + 1} 👇", kb([LOGOS_DONE]))
        if text in (SKIP, LOGOS_DONE):
            return ask_name(chat, u)
        if logos:
            return say(chat, f"ابعت اللوجو كصورة، أو اضغط «{LOGOS_DONE}».", kb([LOGO_MORE, LOGOS_DONE]))
        return say(chat, "ابعت اللوجو كصورة، أو اضغط تخطي.", kb([SKIP]))

    if st == "NAME":
        if fid:
            return  # extra logos from an album after the 3rd: ignore quietly
        ok, val = optional_text(text)
        if not ok:
            return say(chat, "اكتب اسمك أو اضغط تخطي.")
        u["draft"]["name"] = val[:80] if val else None
        u["state"] = "ID"
        return say(chat, "3️⃣ اكتب الرقم الجامعي / الـ ID (اختياري):", kb([SKIP]))

    if st == "ID":
        ok, val = optional_text(text)
        if not ok:
            return say(chat, "اكتب الـ ID أو اضغط تخطي.")
        u["draft"]["id"] = val[:40] if val else None
        u["state"] = "DOCTOR"
        return say(chat, "4️⃣ اسم الدكتور (اختياري):", kb([SKIP]))

    if st == "DOCTOR":
        ok, val = optional_text(text)
        if not ok:
            return say(chat, "اكتب اسم الدكتور أو اضغط تخطي.")
        u["draft"]["doctor"] = val[:80] if val else None
        u["state"] = "YEAR"
        return say(chat, "5️⃣ السنة اللي بتنكتب آخر الغلاف (اختياري) — اختار أو اكتبها:", year_buttons())

    if st == "YEAR":
        ok, val = optional_text(text)
        if not ok:
            return say(chat, "اكتب السنة أو اضغط تخطي.")
        u["draft"]["year"] = val[:30] if val else None
        return ask_frame(chat, u)

    if st == "FRAME":
        if text not in (FRAME_YES, FRAME_NO):
            return say(chat, "اختار من الأزرار تحت 👇")
        u["draft"]["frame"] = text == FRAME_YES
        u["profile"] = dict(u["draft"])  # remember for next time
        return ask_images(chat, u)

    if st == "IMAGES":
        imgs = u.setdefault("images", [])
        if fid:
            if len(imgs) >= MAX_IMAGES:
                return say(chat, f"الحد الأقصى {MAX_IMAGES} صورة. اضغط «{DONE}».")
            imgs.append([msg["message_id"], fid])  # message_id keeps the sending order (albums too)
            prefetch(fid)
            if not msg.get("media_group_id") or len(imgs) % 5 == 0:
                say(chat, f"✅ وصلت صورة {len(imgs)}")
            return
        if text == DONE:
            return ask_filename(chat, u)
        if text == RETRY and imgs:
            return make_pdf(chat, u, u.get("fname") or safe_name(default_name(u["draft"])))
        if text == UNDO:
            imgs.sort()
            if imgs:
                imgs.pop()
                return say(chat, f"انحذفت آخر صورة. صار عندك {len(imgs)} صورة.", kb([DONE], [UNDO]))
            return say(chat, "ما في صور لسا.")
        return say(chat, f"ابعت صورة، أو اضغط «{DONE}».", kb([DONE], [UNDO]))

    if st == "FILENAME":
        if fid:  # she kept sending pages: take them, ask again later
            u["images"].append([msg["message_id"], fid])
            prefetch(fid)
            u["state"] = "IMAGES"
            return say(chat, f"✅ وصلت صورة {len(u['images'])}", kb([DONE], [UNDO]))
        if not text or text.startswith("/") or (text in BUTTONS and text != RETRY):
            return say(chat, "اكتب اسم الملف أو اختار الاسم المقترح 👇",
                       kb([safe_name(default_name(u["draft"]))]))
        if text == RETRY:
            return make_pdf(chat, u, u.get("fname") or safe_name(default_name(u["draft"])))
        u["state"] = "IMAGES"  # if the download fails, she stays here with RETRY
        return make_pdf(chat, u, safe_name(text))

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
        _ctx.queue, _ctx.reply = [], None
        try:
            handle(msg, u)
        except Exception:
            log.exception("handler failed")
        db = _load()
        db["users"][key] = u
        _save(db)
        reply, _ctx.reply = _ctx.reply, None
        flush(keep_last=reply is None)  # normally nothing: handlers send one message
        q, _ctx.queue = _ctx.queue, None
    if reply:
        return reply
    if q:  # reply inside the webhook response: no outbound request, arrives instantly
        return {"method": "sendMessage", **q[-1]}
    return "ok"


@app.route("/pdf/<key>/<name>")
def pdf_file(key, name):
    """Lets Telegram fetch a finished PDF when the upload through the proxy failed."""
    path = os.path.join(PDF_DIR, f"{key}.pdf")
    if not re.fullmatch(r"[0-9a-f]{16}", key) or not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="application/pdf", download_name=name)


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
