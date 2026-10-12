# MEGA BOT — Bale + PostgreSQL + Railway

ربات اجتماعی بله با پنل‌های دسته‌بندی‌شده‌ی `/admin` و `/owner`، تنظیم دسترسی ادمین‌ها، آمار، جست‌وجوی کاربر/گروه، بن داخلی همراه کد پیگیری، مدیریت متن «درباره ما» و اعضای تیم توسعه، و چند بازی گروهی.

## راه‌اندازی

1. پوشه را استخراج کنید.
2. Python 3.11 یا جدیدتر نصب باشد.
3. وابستگی‌ها را نصب کنید:
   ```bash
   pip install -r requirements.txt
   ```
4. `config.py` را باز کنید و `BOT_TOKEN` و `DATABASE_URL` را مقداردهی کنید؛ یا این دو را به‌صورت متغیر محیطی تنظیم کنید. شناسه مالک به‌صورت پیش‌فرض `1967315238` است.
5. دیتابیس PostgreSQL را بسازید و برنامه را اجرا کنید:
   ```bash
   python main.py
   ```

## Railway

یک سرویس از Dockerfile بسازید یا Worker ایجاد کنید، فایل‌های پروژه را بارگذاری کنید، متغیرهای `BOT_TOKEN` و `DATABASE_URL` را در Variables قرار دهید. Dockerfile، ffmpeg و فونت پایه را نصب می‌کند. از PostgreSQL plugin خود Railway می‌توانید مقدار اتصال را بگیرید.

## امنیت

`config.py` برای راحتی مطابق درخواست پروژه در نظر گرفته شده است، اما برای استقرار امن‌تر، متغیرهای محیطی Railway توصیه می‌شوند. اگر توکن در جایی منتشر شد، آن را از پنل سازنده‌ی ربات باطل و جایگزین کنید.

## رفتار پنل‌ها

- `/admin`: دسترسی به بخش‌هایی که مجوزشان برای همان ادمین فعال شده است.
- `/owner`: فقط برای شناسه‌ی مالک.
- ادمین جدید به‌صورت پیش‌فرض مجوز بن کاربر و گروه ندارد؛ آنبن داخلی پیش‌فرض فعال است.
- تمام تغییرات مدیریتی در لاگ ثبت می‌شوند.
- بن‌های این پروژه، محدودیت داخلی استفاده از قابلیت‌های MEGA BOT هستند؛ این با بن‌کردن واقعی عضو در خود پیام‌رسان یکی نیست.
- کد پرونده با `#` و چهار رقم ساخته می‌شود. چون فقط ۹۰۰۰ کد چهارتایی در بازه‌ی 1000 تا 9999 وجود دارد، کدهای فعال/ثبت‌شده دوباره استفاده نمی‌شوند و پس از اتمام ظرفیت، ایجاد پرونده‌ی جدید خطا می‌دهد.

## درباره سازگاری API

این پروژه با درخواست‌های HTTP مستقیم به نقطه‌ی پایانی Bot API بله کار می‌کند و از قالب‌های متداول `getUpdates`, `sendMessage`, `answerCallbackQuery`, `editMessageText` و روش‌های مدیریت پیام استفاده می‌کند. امکانات وابسته به پشتیبانی خاص سرور بله (مانند پین‌کردن یا حذف پیام) در زمان اجرا خطای API را مدیریت می‌کنند. قبل از استفاده در گروه واقعی، توکن آزمایشی و مجوزهای ربات را بررسی کنید.

## قابلیت‌های فعلی

- PostgreSQL برای کاربران، گروه‌ها، تنظیمات، اعضای تیم، ادمین‌ها، پرونده‌های محدودیت، لاگ و وضعیت بازی‌ها.
- منوی کاربر با پنج دکمه‌ی Reply Keyboard: پشتیبانی، حمایت مالی، تیم توسعه، آمار و اطلاعات، و منوی بیشتر. بخش‌های درباره ما، راهنمای ربات و تیم من در «منوی بیشتر» قرار دارند.
- منوهای صفحه‌بندی‌شده با سقف ۶ دکمه در هر صفحه، شامل کنترل‌های برگشت/بستن.
- ویرایش «درباره ما» و مدیریت تیم توسعه از پنل مالک.
- جست‌وجوی کاربران و گروه‌هایی که ربات در رویدادهای مشاهده‌شده ثبت کرده است.
- بن/آنبن داخلی کاربر و گروه با دلیل، مدت، کد پیگیری و اعلان تلاش‌شده.
- نمایش پرونده در بخش «تیم من» برای کاربر.
- آمار شمارش‌های ثبت‌شده در دیتابیس.
- پاسخ‌های اجتماعی متنوع، دوز، شیر یا خط و بازی ساده‌ی «هرکی تک بیاره».
- قابلیت `پین` برای ریپلای به پیام، در صورت داشتن دسترسی API.
- تبدیل عکس و ویدئو به GIF با ریپلای و دستور `گیف`؛ برای افزودن نوشته از `گیف متن دلخواه` استفاده کن. ورودی حداکثر ۲۰ مگابایت، ویدئو تا ۲۰ ثانیه و خروجی حداکثر ۱۸ مگابایت محدود شده است.
- دستورهای `بن` و `کیک` با ریپلای به پیام کاربر در گروه؛ این دو از API مدیریت گروه استفاده می‌کنند و نیازمند دسترسی ادمین ربات هستند. کیک با بن کوتاه و رفع آن اجرا می‌شود.

## محدودیت‌هایی که باید پیش از استفاده‌ی عمومی تست شوند

بله ممکن است بر اساس نسخه‌ی Bot API، نوع گفتگو یا سطح دسترسی ربات، بعضی عملیات‌ها را نپذیرد. آمار تاریخی قبل از اضافه‌شدن ربات قابل بازیابی نیست. ارسال پیام خصوصی فقط زمانی ممکن است که بله اجازه‌ی ارسال به آن کاربر را بدهد. ردیابی کامل و تاریخی ورود/خروج اعضا و شمارش انواع رسانه هنوز کامل نیست. تبدیل GIF و بن/کیک گروهی اضافه شده‌اند، اما چون پشتیبانی دقیق متدهای فایل و مدیریت عضو ممکن است در نسخه Bot API بله تفاوت داشته باشد، حتماً روی گروه آزمایشی و با دسترسی‌های واقعی تست کنید. قابلیت GIF به `ffmpeg` نیاز دارد؛ از Dockerfile ارائه‌شده در Railway استفاده کنید.

## رفع خطای polling در Railway

در نسخه‌ی فعلی، دریافت `getUpdates` با پارامترهای URL انجام می‌شود و زمان انتظار HTTP از زمان long polling بیشتر است. اگر اتصال همچنان شکست بخورد، لاگ جدید نام دقیق خطای شبکه یا پاسخ HTTP را نمایش می‌دهد. مطمئن شوید فقط یک نمونه از ربات با همین توکن در حال polling است و برای این توکن webhook فعال نباشد.

## Donation workflow

- The owner can edit the donation intro, payment instructions, card number, cardholder name, and public owner username from `/owner` → **تنظیمات حمایت مالی**.
- Donation amounts are accepted in Persian or English digits, including forms such as `2000` and `2.000`.
- A user-submitted payment claim is not counted immediately. It creates a pending record and asks the owner to approve or reject it in private.
- Only an owner-approved claim increments the donation total and starts a direct-message announcement to recorded user IDs; the broadcast target is always a user ID, never a group chat ID. Some DMs may fail if a user has not opened the bot, blocked it, or Bale rejects the message; the owner receives sent/failed counts.
- If the donor chose public attribution, their name and numeric user ID are announced after approval and they receive a confirmation DM. If anonymous, the announcement does not expose their name. Rejected claims do not change totals and do not notify the donor.
- This is an owner-reviewed honor system, not automatic bank verification. The owner must verify the payment before approving.

## Audit notes (2026-10)

- Scheduled group lock: `setChatPermissions` is best-effort on Bale; **primary enforcement** is immediate `deleteMessage` for non-admins during active windows, plus start/end announcements.
- Bot must be group admin with **delete messages** permission for lock enforcement to work.
- Social replies no longer invent a zero group count when stats fail to load.
- Owner panel includes live monitoring counters (`bot_metrics` / daily metrics); numbers start from deployment of that feature.
- Automated tests: `python -m pytest tests/ -v` (pure logic + schedule lock + regressions).
