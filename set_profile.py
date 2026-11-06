"""One-off: sets the bot's description, short description, commands (and photo if the API allows).
Run on the server: python3.12 set_profile.py"""
import json
import os

import requests

from pa_app import API, HERE, TOKEN

SHORT = "حوّل صور حل التاسك لملف PDF مرتّب، مع غلاف باسمك ورقمك الجامعي، خلال ثواني 📄"

DESCRIPTION = """📄 حوّل صور حل التاسك لملف PDF جاهز للتسليم.

ابعت بالترتيب:
1️⃣ اللوجو (اختياري)
2️⃣ اسمك ورقمك الجامعي
3️⃣ اسم الدكتور (اختياري)
4️⃣ صور الحل بالترتيب

وبيرجعلك PDF فيه غلاف مرتّب، وكل صورة بصفحة A4 مع اسمك ورقم الصفحة.

اضغط Start وابدأ 👇"""

COMMANDS = [
    {"command": "start", "description": "ابدأ تاسك جديد"},
    {"command": "cancel", "description": "إلغاء التاسك الحالي"},
]


def call(method, **kw):
    files = kw.pop("_files", None)
    r = requests.post(f"{API}/{method}", data=kw if files else None, json=None if files else kw,
                      files=files, timeout=60)
    out = r.json()
    print(method, "->", out.get("ok"), out.get("description", ""))
    return out


assert TOKEN, "BOT_TOKEN missing"
call("setMyShortDescription", short_description=SHORT)
call("setMyDescription", description=DESCRIPTION)
call("setMyCommands", commands=COMMANDS)

with open(os.path.join(HERE, "assets", "avatar.png"), "rb") as f:
    call("setMyProfilePhoto",
         photo=json.dumps({"type": "static", "photo": "attach://pic"}),
         _files={"pic": ("avatar.png", f, "image/png")})
