# -*- coding: utf-8 -*-
"""
reseller_api.py
================
سرویس API مخصوص فروشنده‌ها — کاملاً جدا از bot-2-10-5.py اجرا می‌شه (پروسه‌ی
جدا، پورت جدا)، ولی دقیقاً از همون دیتابیس Turso که ربات و پنل استفاده
می‌کنن می‌خونه/می‌نویسه (با همون TURSO_DATABASE_URL / TURSO_AUTH_TOKEN).

چرا سرویس جدا؟
  - ربات با polling کار می‌کنه؛ این یه HTTP API معمولیه، پس بهتره worker
    جدا و مستقل داشته باشه (اگه فروشنده‌ای کلیدش لو بره یا اسپم کنه، فقط
    همین سرویس رو می‌بندید، کار ربات اصلی قطع نمی‌شه).

اجرا:
    pip install flask requests turso-serverless --break-system-packages
    export TURSO_DATABASE_URL=...        (دقیقاً همونی که تو ربات هست)
    export TURSO_AUTH_TOKEN=...
    export CONFIG_API=...                (دقیقاً همونی که تو ربات هست)
    export CONFIG_KEY=...
    export RESELLER_API_PORT=8088        (اختیاری، پیش‌فرض 8088)
    python3 reseller_api.py

بهتره با pm2/systemd به‌صورت دائمی بالا نگه داشته بشه، دقیقاً مثل خود ربات.

احراز هویت:
    هر درخواست باید دو هدر داشته باشه:
        X-Api-Key:    <کلید عمومی فروشنده>
        X-Api-Secret: <رمز محرمانه‌ی فروشنده>
    این‌ها همونایی هستن که ربات موقع «فعال‌سازی فروشندگی» یا «ساخت مجدد
    کلید» به فروشنده نشون می‌ده.

اندپوینت‌ها:
    GET  /api/v1/ping                 - تست اتصال، بدون نیاز به احراز هویت
    GET  /api/v1/balance              - موجودی استخر و قیمت هر گیگ
    POST /api/v1/configs              - ساخت کانفیگ جدید (از استخر کم می‌شه)
    GET  /api/v1/configs              - لیست کانفیگ‌های ساخته‌شده توسط این فروشنده
    GET  /api/v1/configs/<config_id>  - جزئیات یک کانفیگ
    DELETE /api/v1/configs/<config_id>- حذف یک کانفیگ (گیگ برنمی‌گرده)

نمونه‌ی ساخت کانفیگ:
    curl -X POST http://SERVER:8088/api/v1/configs \\
      -H "X-Api-Key: rk_xxx" -H "X-Api-Secret: xxxxx" \\
      -H "Content-Type: application/json" \\
      -d '{"gb": 5, "days": 30, "type": "both", "label": "customer-1"}'

    type یکی از این مقادیره: config, wireguard, both, openvpn, dns
    اگه label نده، خودکار یه اسم یکتا ساخته می‌شه.
"""

import os
import time
import hashlib
import secrets
import threading
from datetime import datetime

import requests
import turso_serverless
from flask import Flask, request, jsonify, g

# ---------------------------------------------------------------------------
# تنظیمات — دقیقاً باید با مقادیر ربات یکی باشن چون هر دو رو یه دیتابیس و یه
# پنل ساخت کانفیگ کار می‌کنن.
# ---------------------------------------------------------------------------
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")
CONFIG_API = os.getenv("CONFIG_API", "https://su.randomatic.ir/api/v1/configs")
CONFIG_KEY = os.getenv("CONFIG_KEY", "sk_live_azIaKWpOvQDoD2-7vX8-yyf3WNPg6U1p")
# روی Railway پورت رو خودِ پلتفرم با متغیر PORT بهت تزریق می‌کنه (یه عدد
# متغیر و تصادفی، نه ۸۰۸۸ ثابت)؛ باید همون رو بخونیم وگرنه دامنه‌ی عمومی
# نمی‌تونه به سرویس وصل بشه. رو یه سرور معمولی (VPS)، PORT ست نیست، پس
# می‌ره سراغ RESELLER_API_PORT یا پیش‌فرض ۸۰۸۸.
PORT = int(os.getenv("PORT") or os.getenv("RESELLER_API_PORT", "8088"))

# سقف درخواست: هر کلید API حداکثر این تعداد درخواست در دقیقه (جلوی
# اسپم/سوءاستفاده رو می‌گیره؛ فقط تو حافظه‌ست، با ری‌استارت سرویس صفر می‌شه).
RATE_LIMIT_PER_MIN = int(os.getenv("RESELLER_RATE_LIMIT", "30"))

TYPE_TO_PROTO = {
    'config': 'xray',
    'wireguard': 'wireguard',
    'both': 'both',
    'openvpn': 'openvpn',
    'dns': 'dns',
}

SESSION = requests.Session()
app = Flask(__name__)

# ---------------------------------------------------------------------------
# دیتابیس — هر ترد اتصال مخصوص خودش (روی Turso، اشتراک‌گذاری یه اتصال بین
# تردها می‌تونه نتیجه‌ها رو قاطی کنه؛ همون الگویی که تو ربات و پنل هست).
# ---------------------------------------------------------------------------
_db_local = threading.local()


def get_conn():
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = turso_serverless.connect(TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)
        conn.close = lambda: None
        _db_local.conn = conn
    return conn


def db_retry(func):
    """اگه اتصال قطع/خراب بود، یه بار اتصال رو تازه می‌کنه و دوباره تلاش
    می‌کنه؛ همون الگوی with_db_retry تو ربات."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            _db_local.conn = None
            return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


# ---------------------------------------------------------------------------
# ساخت کانفیگ روی پنل هاست — دقیقاً همون منطق make_config تو ربات، چون باید
# روی همون سرویس ساخت کانفیگ کار کنه.
# ---------------------------------------------------------------------------
def make_config(gb, label, days, proto='both'):
    try:
        res = SESSION.post(
            CONFIG_API,
            headers={'Authorization': 'Bearer ' + CONFIG_KEY},
            json={'gb': gb, 'label': label, 'expiryDays': days, 'proto': proto},
            timeout=20
        )
        if res.status_code in (200, 201):
            data = res.json()
            return {
                'success': True,
                'sub_url': data.get('subUrl'),
                'config_id': data.get('id') or data.get('uuid') or data.get('username'),
            }
        err_code = None
        try:
            body = res.json()
            err_code = body.get('error') or body.get('message') or body.get('code')
        except Exception:
            pass
        return {'success': False, 'error': str(res.status_code), 'error_code': err_code}
    except Exception as e:
        return {'success': False, 'error': str(e), 'error_code': None}


def delete_config_from_panel(config_id):
    if not config_id:
        return True
    try:
        res = SESSION.delete(
            CONFIG_API + '/' + str(config_id),
            headers={'Authorization': 'Bearer ' + CONFIG_KEY},
            timeout=15
        )
        return res.status_code in (200, 201, 204)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# احراز هویت فروشنده
# ---------------------------------------------------------------------------
@db_retry
def _find_reseller_by_key(api_key):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT user_id, api_key, secret_hash, gb_balance, price_per_gb, status "
        "FROM resellers WHERE api_key=?",
        (api_key,)
    )
    return c.fetchone()


_rate_state = {}
_rate_lock = threading.Lock()


def _rate_limited(api_key):
    now = time.time()
    with _rate_lock:
        window = _rate_state.setdefault(api_key, [])
        window[:] = [t for t in window if now - t < 60]
        if len(window) >= RATE_LIMIT_PER_MIN:
            return True
        window.append(now)
        return False


def require_auth():
    """هدرها رو چک می‌کنه؛ اگه معتبر بود reseller رو تو g می‌ذاره، وگرنه
    یه (response, status) برمی‌گردونه که باید همون لحظه return بشه."""
    api_key = request.headers.get('X-Api-Key', '').strip()
    api_secret = request.headers.get('X-Api-Secret', '').strip()
    if not api_key or not api_secret:
        return jsonify({'success': False, 'error': 'missing_credentials',
                         'message': 'هدرهای X-Api-Key و X-Api-Secret الزامی هستن.'}), 401
    if _rate_limited(api_key):
        return jsonify({'success': False, 'error': 'rate_limited',
                         'message': f'بیش از {RATE_LIMIT_PER_MIN} درخواست در دقیقه.'}), 429
    row = _find_reseller_by_key(api_key)
    if not row:
        return jsonify({'success': False, 'error': 'invalid_key'}), 401
    user_id, _key, secret_hash, gb_balance, price_per_gb, status = row
    if hashlib.sha256(api_secret.encode('utf-8')).hexdigest() != secret_hash:
        return jsonify({'success': False, 'error': 'invalid_secret'}), 401
    if status != 'active':
        return jsonify({'success': False, 'error': 'account_blocked',
                         'message': 'حساب فروشندگی شما مسدود شده.'}), 403
    g.reseller_id = user_id
    g.gb_balance = gb_balance
    g.price_per_gb = price_per_gb
    return None


# ---------------------------------------------------------------------------
# عملیات دیتابیس مخصوص فروشنده
# ---------------------------------------------------------------------------
@db_retry
def deduct_gb_atomic(user_id, gb):
    """فقط اگه واقعاً موجودی کافی باشه کم می‌کنه (شرط تو خودِ WHERE، تا اگه
    چند درخواست هم‌زمان بیان، هیچ‌وقت استخر منفی نشه). True/False برمی‌گردونه."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE resellers SET gb_balance = gb_balance - ? WHERE user_id=? AND gb_balance >= ?",
        (gb, user_id, gb)
    )
    conn.commit()
    ok = c.rowcount > 0
    conn.close()
    return ok


@db_retry
def refund_gb(user_id, gb):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE resellers SET gb_balance = gb_balance + ? WHERE user_id=?", (gb, user_id))
    conn.commit()
    conn.close()


@db_retry
def get_gb_balance(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT gb_balance FROM resellers WHERE user_id=?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else 0


@db_retry
def save_api_config(reseller_id, config_id, sub_url, gb, days, label, ctype):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO reseller_api_configs "
        "(reseller_id, config_id, sub_url, gb, days, label, type, active, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (reseller_id, config_id, sub_url, gb, days, label, ctype, datetime.now().isoformat())
    )
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return new_id


@db_retry
def list_api_configs(reseller_id, limit=50):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, config_id, sub_url, gb, days, label, type, active, created_at "
        "FROM reseller_api_configs WHERE reseller_id=? ORDER BY id DESC LIMIT ?",
        (reseller_id, limit)
    )
    res = c.fetchall()
    conn.close()
    return res


@db_retry
def get_api_config(reseller_id, config_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, config_id, sub_url, gb, days, label, type, active, created_at "
        "FROM reseller_api_configs WHERE reseller_id=? AND config_id=?",
        (reseller_id, config_id)
    )
    res = c.fetchone()
    conn.close()
    return res


@db_retry
def mark_api_config_deleted(reseller_id, config_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE reseller_api_configs SET active=0 WHERE reseller_id=? AND config_id=?",
        (reseller_id, config_id)
    )
    conn.commit()
    ok = c.rowcount > 0
    conn.close()
    return ok


_label_lock = threading.Lock()
_label_counter = {'n': 0}


def gen_label(reseller_id):
    with _label_lock:
        _label_counter['n'] += 1
        n = _label_counter['n']
    return f'API-{reseller_id}-{int(time.time())}-{n}'


def row_to_dict(row):
    (rid, config_id, sub_url, gb, days, label, ctype, active, created_at) = row
    return {
        'id': rid, 'config_id': config_id, 'sub_url': sub_url, 'gb': gb, 'days': days,
        'label': label, 'type': ctype, 'active': bool(active), 'created_at': created_at,
    }


# ---------------------------------------------------------------------------
# اندپوینت‌ها
# ---------------------------------------------------------------------------
@app.route('/api/v1/ping', methods=['GET'])
def ping():
    return jsonify({'ok': True, 'time': datetime.now().isoformat()})


@app.route('/api/v1/balance', methods=['GET'])
def balance():
    err = require_auth()
    if err:
        return err
    return jsonify({
        'success': True,
        'gb_balance': g.gb_balance,
        'price_per_gb': g.price_per_gb,
    })


@app.route('/api/v1/configs', methods=['POST'])
def create_config():
    err = require_auth()
    if err:
        return err

    body = request.get_json(silent=True) or {}
    try:
        gb = float(body.get('gb'))
        days = int(body.get('days'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'invalid_input',
                         'message': 'gb (عدد) و days (عدد صحیح) الزامی هستن.'}), 400
    if gb <= 0 or days <= 0:
        return jsonify({'success': False, 'error': 'invalid_input',
                         'message': 'gb و days باید بزرگ‌تر از صفر باشن.'}), 400

    ctype = str(body.get('type', 'both')).strip().lower()
    if ctype not in TYPE_TO_PROTO:
        return jsonify({'success': False, 'error': 'invalid_type',
                         'message': f'type باید یکی از این‌ها باشه: {", ".join(TYPE_TO_PROTO)}'}), 400

    label = str(body.get('label') or gen_label(g.reseller_id))[:64]

    # اول از استخر کم می‌کنیم (اتمیک، شرط تو WHERE)، بعد کانفیگ می‌سازیم؛
    # اگه ساخت کانفیگ شکست خورد، گیگ رو برمی‌گردونیم. این ترتیب جلوی
    # اوورسل (فروش بیشتر از استخر موجود) رو زیر بار هم‌زمان می‌گیره.
    if not deduct_gb_atomic(g.reseller_id, gb):
        return jsonify({'success': False, 'error': 'insufficient_balance',
                         'message': 'موجودی استخر گیگ کافی نیست.',
                         'gb_balance': get_gb_balance(g.reseller_id)}), 402

    res = make_config(gb, label, days, proto=TYPE_TO_PROTO[ctype])
    if not res.get('success') or not res.get('sub_url'):
        refund_gb(g.reseller_id, gb)
        return jsonify({'success': False, 'error': 'panel_error',
                         'message': 'ساخت کانفیگ رو سرور ناموفق بود؛ گیگ به استخر برگشت.',
                         'detail': res.get('error_code') or res.get('error')}), 502

    save_api_config(g.reseller_id, res['config_id'], res['sub_url'], gb, days, label, ctype)
    return jsonify({
        'success': True,
        'config_id': res['config_id'],
        'sub_url': res['sub_url'],
        'label': label,
        'gb': gb,
        'days': days,
        'type': ctype,
        'gb_balance_remaining': get_gb_balance(g.reseller_id),
    }), 201


@app.route('/api/v1/configs', methods=['GET'])
def list_configs():
    err = require_auth()
    if err:
        return err
    rows = list_api_configs(g.reseller_id)
    return jsonify({'success': True, 'configs': [row_to_dict(r) for r in rows]})


@app.route('/api/v1/configs/<config_id>', methods=['GET'])
def get_config(config_id):
    err = require_auth()
    if err:
        return err
    row = get_api_config(g.reseller_id, config_id)
    if not row:
        return jsonify({'success': False, 'error': 'not_found'}), 404
    return jsonify({'success': True, 'config': row_to_dict(row)})


@app.route('/api/v1/configs/<config_id>', methods=['DELETE'])
def delete_config(config_id):
    err = require_auth()
    if err:
        return err
    row = get_api_config(g.reseller_id, config_id)
    if not row:
        return jsonify({'success': False, 'error': 'not_found'}), 404
    if not delete_config_from_panel(config_id):
        return jsonify({'success': False, 'error': 'panel_error',
                         'message': 'حذف روی سرور ناموفق بود.'}), 502
    mark_api_config_deleted(g.reseller_id, config_id)
    return jsonify({'success': True})


@app.errorhandler(404)
def not_found(_e):
    return jsonify({'success': False, 'error': 'not_found'}), 404


@app.errorhandler(500)
def server_error(_e):
    return jsonify({'success': False, 'error': 'server_error'}), 500


if __name__ == '__main__':
    if not TURSO_DATABASE_URL:
        raise SystemExit('❌ متغیر محیطی TURSO_DATABASE_URL تنظیم نشده — دقیقاً همونی که تو ربات استفاده می‌شه رو ست کنید.')
    print(f'🧑\u200d💼 سرویس API فروشندگی رو پورت {PORT} بالا اومد.')
    app.run(host='0.0.0.0', port=PORT, threaded=True)
