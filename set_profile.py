"""One-off: sets the bot's description, short description, commands (and photo if the API allows).
Run on the server: python3.12 set_profile.py"""
import json
import os

from pa_app import HERE, TOKEN, tg

SHORT = "حوّل صور حل التاسك لملف PDF مرتّب مع غلاف فيه لوجوهاتك واسمك ورقمك، خلال ثواني 📄"

DESCRIPTION = """📄 حوّل صور حل التاسك لملف PDF جاهز للتسليم.

ابعت بالترتيب (كله اختياري ما عدا الصور):
1️⃣ لحد 3 لوجوهات
2️⃣ اسمك ورقمك الجامعي
3️⃣ اسم الدكتور والسنة
4️⃣ إطار للصفحات أو بدون
5️⃣ صور الحل بالترتيب

وبيرجعلك PDF فيه غلاف مرتّب، وكل صورة بصفحة A4 مع رقم الصفحة.

اضغط Start وابدأ 👇"""

COMMANDS = [
    {"command": "start", "description": "ابدأ تاسك جديد"},
    {"command": "cancel", "description": "إلغاء التاسك الحالي"},
]


def call(method, **kw):
    try:
        out = tg(method, **kw)  # retries PythonAnywhere proxy errors
    except Exception as e:
        out = {"ok": False, "description": str(e)}
    print(method, "->", out.get("ok"), out.get("description", ""))
    return {method: [out.get("ok"), out.get("description", "")]}


def apply():
    assert TOKEN, "BOT_TOKEN missing"
    res = [call("setMyShortDescription", short_description=SHORT),
           call("setMyDescription", description=DESCRIPTION),
           call("setMyCommands", commands=COMMANDS)]
    with open(os.path.join(HERE, "assets", "avatar.png"), "rb") as f:
        res.append(call("setMyProfilePhoto",
                        photo=json.dumps({"type": "static", "photo": "attach://pic"}),
                        _files={"pic": ("avatar.png", f, "image/png")}))
    return res


if __name__ == "__main__":
    apply()
