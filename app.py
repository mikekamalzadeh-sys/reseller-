# -*- coding: utf-8 -*-
"""
run_reseller.py
===============
یه سرویس Railway واحد که هم API فروشنده‌ها (reseller_api.py) و هم پنل
فروشنده‌ها (reseller_panel.py) رو روی *یک پورت* سرو می‌کنه:

    /api/v1/...   ->  reseller_api.app
    هر مسیر دیگه  ->  reseller_panel.app   (/login, /, /configs/...)

چرا به‌جای دو subprocess؟ چون Railway فقط یک PORT بهت می‌ده؛ دو پروسه رو
یه پورت با هم تصادم می‌کنن. اینجا فقط یه اپ (WSGI) بالا میاد و درخواست‌ها
بر اساس مسیر تقسیم می‌شن.

Start Command / Procfile:
    web: gunicorn run_reseller:app --bind 0.0.0.0:$PORT --workers 2 --threads 4

متغیرهای محیطی لازم (روی همین سرویس):
    TURSO_DATABASE_URL, TURSO_AUTH_TOKEN, CONFIG_API, CONFIG_KEY   (برای API)
    SECRET_KEY            (برای کوکی سشن پنل؛ یه رشته‌ی تصادفی طولانی)
    DEFAULT_API_BASE      (اختیاری: آدرس همین سرویس، مثلاً
                           https://xxxx.up.railway.app — فیلد آدرس API تو
                           فرم لاگین خودکار پر می‌شه)
"""

from reseller_api import app as api_app
from reseller_panel import app as panel_app

API_PREFIX = '/api/v1'


class Dispatcher:
    """مسیرهای /api/v1 رو به API می‌فرسته، بقیه رو به پنل. مسیر رو دست‌نخورده
    رد می‌کنه (برخلاف DispatcherMiddleware که prefix رو می‌بُره و روت‌های
    /api/v1/... رو خراب می‌کنه)."""

    def __init__(self, api, panel):
        self.api = api
        self.panel = panel

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '') or ''
        if path == API_PREFIX or path.startswith(API_PREFIX + '/'):
            return self.api(environ, start_response)
        return self.panel(environ, start_response)


app = Dispatcher(api_app, panel_app)


if __name__ == '__main__':
    # اجرای لوکال/تست دستی (روی Railway از gunicorn استفاده کن)
    import os
    from werkzeug.serving import run_simple
    port = int(os.getenv('PORT') or os.getenv('RESELLER_API_PORT', '8088'))
    print(f'🧑\u200d💼 API + پنل فروشنده‌ها رو پورت {port} بالا اومد.')
    run_simple('0.0.0.0', port, app, threaded=True)
