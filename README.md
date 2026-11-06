# بوت PDF لحل التاسك

## التشغيل
1. بتيليجرام افتحي **@BotFather** ← `/newbot` ← اختاري اسم ويوزر للبوت ← انسخي الـ token.
2. افتحي ملف `.env` وحطي: `BOT_TOKEN=التوكن_هون`
3. دبل كليك على `run.bat` (لازم الشباك يضل مفتوح والبوت شغال).

## شو بيعمل البوت
`/start` ← لوجو (اختياري) ← الاسم (إجباري) ← الـ ID (إجباري) ← اسم الدكتور (اختياري) ← صور الحل بالترتيب ← «✅ اعمل الـ PDF» ← بيبعت ملف `ID_الاسم.pdf`.
- أول صفحة غلاف (لوجو + الاسم + الرقم + الدكتور + التاريخ)، وبعدين كل صورة بصفحة A4 مع الاسم والرقم ورقم الصفحة تحت.
- بيتذكر بيانات كل مستخدم للمرة الجاية («نفس بياناتي»).
- «↩️ احذف آخر صورة» لو انبعتت صورة غلط. `/cancel` للإلغاء.

## شغال 24 ساعة على PythonAnywhere (مجاني)
- البوت: t.me/riman_task_pdf_bot — السيرفر: https://rimanlogic.pythonanywhere.com (حساب rimanlogic)
- الكود على السيرفر: `~/task-pdf-bot` (نسخة `pa_app.py`)، التوكن بـ `~/task-pdf-bot/.env`.
- **كل شهر**: pythonanywhere.com ← Web ← «Run until 1 month from today» (وإلا البوت بيوقف).
- تحديث الكود: Bash console ← `cd ~/task-pdf-bot && git pull` ← Web ← Reload.
- الأخطاء: `/var/log/rimanlogic.pythonanywhere.com.error.log`.
