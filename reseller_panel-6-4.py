# -*- coding: utf-8 -*-
"""
reseller_panel.py  (نسخه‌ی سریع + ظاهر شبیه seller.vaslpro.com)
================================================================
فقط با HTTP به reseller_api.py حرف می‌زنه:
  GET /api/v1/balance ، GET/POST /api/v1/configs ، DELETE /api/v1/configs/<id>

تغییرات این نسخه:
  • ظاهر کارت‌های کانفیگ، دکمه «مشتری جدید» و شیت ساخت کانفیگ شبیه عکس‌ها
  • دکمه‌های پایین وقتی لمس می‌شن یه مربع کم‌رنگ بنفش می‌گیرن + نوار پیشرفت بالای صفحه
  • سرعت:
      - balance و configs هم‌زمان (موازی) گرفته می‌شن، نه پشت‌سرهم
      - اتصال به API دوباره استفاده می‌شه (keep-alive)، هر بار TLS جدید نمی‌سازه
      - کش کوتاه (۱۵ ثانیه) برای جابه‌جایی بین صفحه‌ها؛ بعد از ساخت/حذف خودکار پاک می‌شه
      - حذف کانفیگ از ۳ درخواست به ۲ درخواست رسید
      - فونت گوگل (که تو ایران کنده و رندر رو بلاک می‌کرد) حذف شد
      - ساخت چندتایی (تا ۲۰ تا) با هم و موازی

اجرا:
    pip install flask requests          # اختیاری: flask-compress
    export SECRET_KEY="یه رشته‌ی تصادفی طولانی"
    export DEFAULT_API_BASE="https://your-reseller-api.up.railway.app"
    python3 reseller_panel.py
"""

import os
import io
import re
import json
import math
import time
import base64
import hashlib
import hmac
from urllib.parse import unquote, quote, urlsplit
import html as html_lib
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from flask import Flask, request, redirect, url_for, session, Response

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key-in-production")
try:  # اگه نصب باشه، خروجی HTML فشرده می‌شه (اختیاری)
    from flask_compress import Compress
    Compress(app)
except Exception:
    pass

API_BASE = os.getenv("DEFAULT_API_BASE", "https://web-production-2f5065.up.railway.app").strip().rstrip("/")  # فقط برای صفحه‌ی ورود
# سرویس‌دهنده‌ی اصلی: ساخت، تمدید، حذف، موجودی، لیست — همه‌ی کارها به این می‌رن
PROVIDER_BASE = os.getenv("PROVIDER_API_BASE", "https://su.randomatic.ir").strip().rstrip("/")
if PROVIDER_BASE.endswith("/api/v1"):
    PROVIDER_BASE = PROVIDER_BASE[:-len("/api/v1")]
PROVIDER_KEY = os.getenv("PROVIDER_API_KEY", "").strip()   # sk_live_...  (تو کد نذار؛ فقط متغیر محیطی)
BRAND = os.getenv("PANEL_BRAND", "SKY TUNNEL")
FONT_CSS_URL = os.getenv("PANEL_FONT_CSS", "").strip()      # اختیاری: آدرس CSS فونت (مثلاً فونت خودت)
TURSO_URL = (os.getenv("TURSO_URL") or os.getenv("TURSO_DATABASE_URL") or "").strip().rstrip("/")      # مثلا libsql://dbname-user.turso.io
if TURSO_URL.startswith("libsql://"):
    TURSO_URL = "https://" + TURSO_URL[len("libsql://"):]
TURSO_TOKEN = (os.getenv("TURSO_TOKEN") or os.getenv("TURSO_AUTH_TOKEN") or "").strip()
PUBLIC_URL = os.getenv("PANEL_PUBLIC_URL", "").strip().rstrip("/")   # آدرس عمومی همین پنل (برای لینک اشتراک با اسم‌های دلخواه)
PROXY_SUB = os.getenv("PANEL_PROXY_SUB", "0") == "1"      # لینک اشتراک از خود پنل (دیگه لازم نیست؛ API اسم سرور رو خودش عوض می‌کنه)
# نوع داخل پنل ← مقدار proto تو API (اگه API اسم‌های دیگه‌ای داره اینجا عوض کن)
PROTO_MAP = {'config': 'xray', 'wireguard': 'wireguard', 'both': 'both', 'openvpn': 'openvpn', 'dns': 'dns'}
CACHE_TTL = float(os.getenv("PANEL_CACHE_TTL", "15"))        # ثانیه
CREATE_WORKERS = int(os.getenv("PANEL_CREATE_WORKERS", "3"))  # تعداد ساخت هم‌زمان

TYPE_LABELS = {
    'config': '🛡 کانفیگ',
    'wireguard': '🔒 وایرگارد',
    'both': '🧩 کانفیگ + وایرگارد',
    'openvpn': '📱 اوپن‌وی‌پی‌ان',
    'dns': '🎮 DNS بازی',
}
# (اسم کوتاه، آیکون، رنگ)
TYPE_META = {
    'config': ('کانفیگ', 'shield', 'v'),
    'wireguard': ('وایرگارد', 'lock', 'b'),
    'both': ('کانفیگ + وایرگارد', 'layers', 'v'),
    'openvpn': ('اوپن‌وی‌پی‌ان', 'phone', 't'),
    'dns': ('DNS بازی', 'game', 'o'),
}

ERROR_MESSAGES = {
    'missing_credentials': 'اطلاعات ورود ناقصه.',
    'invalid_key': 'کلید API اشتباهه.',
    'invalid_secret': 'رمز API اشتباهه.',
    'account_blocked': 'حساب فروشندگی شما مسدود شده. با ادمین تماس بگیرید.',
    'rate_limited': 'درخواست‌های شما زیاده؛ کمی صبر کنید.',
    'insufficient_balance': 'اعتبار گیگ کافی نیست.',
    'invalid_type': 'نوع کانفیگ نامعتبره.',
    'invalid_input': 'اطلاعات واردشده نامعتبره.',
    'panel_error': 'ساخت/حذف کانفیگ روی سرور ناموفق بود. بعداً دوباره تلاش کنید.',
    'not_found': 'کانفیگ مورد نظر پیدا نشد.',
    'insufficient_pool': 'استخر گیگ شما کافی نیست.',
    'proto_unavailable': 'فروش این نوع کانفیگ موقتاً بسته‌ست.',
    'dns_unavailable': 'سرویس DNS فعلاً در دسترس نیست.',
    'dns_off_for_seller': 'سرویس DNS برای شما فعال نیست.',
    'provider_key_missing': 'متغیر محیطی PROVIDER_API_KEY تنظیم نشده.',
}


def esc(v):
    return html_lib.escape('' if v is None else str(v), quote=True)


def err_text(code, fallback='خطای نامشخص.'):
    return ERROR_MESSAGES.get(code, fallback)


def fmt_gb(v):
    try:
        return f"{float(v):.3f}".rstrip('0').rstrip('.')
    except Exception:
        return '0'


# ---------------------------------------------------------------------------
# تاریخ شمسی
# ---------------------------------------------------------------------------
def g2j(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy, gy = 979, gy - 1600
    else:
        jy, gy = 0, gy - 621
    gy2 = gy + 1 if gm > 2 else gy
    days = 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def jdate(dt):
    if not dt:
        return '-'
    y, m, d = g2j(dt.year, dt.month, dt.day)
    return f'{y}/{m}/{d}'


# ---------------------------------------------------------------------------
# ارتباط با reseller_api.py  (اتصال پایدار + کش + درخواست موازی)
# ---------------------------------------------------------------------------
HTTP = requests.Session()
HTTP.mount('https://', HTTPAdapter(pool_connections=4, pool_maxsize=32))
HTTP.mount('http://', HTTPAdapter(pool_connections=4, pool_maxsize=32))
POOL = ThreadPoolExecutor(max_workers=8)
CACHE = {}  # api_key -> (time, bal, configs)


def creds():
    """اطلاعات ورود رو تو ترد اصلی می‌گیره تا تردها بدون session کار کنن."""
    return {'api_base': PROVIDER_BASE, 'api_key': PROVIDER_KEY, 'api_secret': ''}


def api_call(method, path, api_base=None, api_key=None, api_secret=None, json_body=None, timeout=12):
    if api_base is None:
        api_base = PROVIDER_BASE
    if api_key is None:
        api_key = PROVIDER_KEY
    if api_secret is None:
        api_secret = ''
    if not api_key:
        return False, 0, {'success': False, 'error': 'provider_key_missing'}
    headers = {'X-Api-Key': api_key, 'Authorization': 'Bearer ' + api_key}
    if api_secret:
        headers['X-Api-Secret'] = api_secret
    try:
        res = HTTP.request(method, api_base.rstrip('/') + path, headers=headers,
                           json=json_body, timeout=(5, timeout))
        try:
            data = res.json()
        except Exception:
            data = {'success': False, 'error': 'bad_response'}
        return res.status_code < 400, res.status_code, data
    except requests.exceptions.RequestException:
        return False, 0, {'success': False, 'error': 'connection_failed',
                           'message': 'اتصال به آدرس API برقرار نشد.'}


def invalidate():
    CACHE.pop(session.get('api_key', ''), None)


def login_required(view):
    def wrapper(*args, **kwargs):
        if not session.get('api_key') or not session.get('rid'):
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


def flash(msg, kind='ok'):
    session.setdefault('_flash', []).append([kind, msg])
    session.modified = True


def expired_session(status):
    if status in (401, 403):
        flash('کلید API سرویس‌دهنده (PROVIDER_API_KEY) رد شد.', 'err')
    return False


# ---------------------------------------------------------------------------
# داده‌ها
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# نرمال‌سازی جواب API سرویس‌دهنده (su.randomatic.ir)
# ---------------------------------------------------------------------------
CH_TO_PART = {'xray': 'xray', 'wireguard': 'wg', 'openvpn': 'ovpn', 'dns': 'dns'}
PART_TO_CH = {v: k for k, v in CH_TO_PART.items()}
PROTO_TO_TYPE = {'xray': 'config', 'vless': 'config', 'config': 'config', 'wireguard': 'wireguard', 'wg': 'wireguard',
                 'both': 'both', 'openvpn': 'openvpn', 'ovpn': 'openvpn', 'dns': 'dns'}


def pick(d, *names, default=None):
    if not isinstance(d, dict):
        return default
    for n in names:
        if d.get(n) is not None:
            return d[n]
    return default


def unwrap(data, *keys):
    if isinstance(data, dict):
        for k in keys:
            if data.get(k) is not None:
                return data[k]
    return data


def api_msg(data, fallback='عملیات ناموفق بود.'):
    m = pick(data, 'message', 'error', 'detail')
    if isinstance(m, dict):
        m = pick(m, 'message', 'code')
    return err_text(str(m), str(m)) if m else fallback


def _flat(d, prefix=''):
    out = []
    if isinstance(d, dict):
        for k, v in d.items():
            out += _flat(v, prefix + str(k).lower() + '.')
    elif isinstance(d, (int, float, str)) and not isinstance(d, bool):
        try:
            out.append((prefix[:-1], float(d)))
        except Exception:
            pass
    return out


def norm_wallet(data):
    """جواب /wallet: creditGb (گیگ اعتبار) و tomanPerGb (قیمت هر گیگ)."""
    d = unwrap(data, 'wallet', 'data')
    gb = pick(d, 'creditGb', 'credit_gb', 'sellerCreditGb', 'gb_balance', 'gbBalance', 'balanceGb')
    if gb is None:
        cb = pick(d, 'creditBytes', 'sellerCreditBytes')
        if cb is None:
            cb = pick(pick(d, 'balances', default={}), 'sellerCreditBytes')
        gb = float(cb) / 1024 ** 3 if cb is not None else None
    if gb is None:   # آخرین راه: اولین فیلد شبیه موجودی
        flat = [(k, v) for k, v in _flat(d) if 'credit' in k and 'gb' in k]
        gb = flat[0][1] if flat else 0
    price = pick(d, 'tomanPerGb', 'price_per_gb', 'pricePerGb', default=0)
    try:
        gb = float(gb)
    except Exception:
        gb = 0.0
    return {'gb_balance': gb, 'price_per_gb': price}


def norm_cfg(d):
    cid = pick(d, 'id', '_id', 'configId', 'config_id', 'uuid')
    status = str(pick(d, 'status', 'state', default='active')).lower()
    proto = str(pick(d, 'proto', 'protocol', 'type', default='both')).lower()
    return {
        'config_id': str(cid), 'label': pick(d, 'label', 'name', 'title', default=str(cid)),
        'type': PROTO_TO_TYPE.get(proto, 'both'),
        'gb': (pick(d, 'limitGb', 'gb', 'totalGb', 'total_gb', 'volumeGb', 'quotaGb')
               or (float(pick(d, 'trafficLimitBytes', default=0) or 0) / 1024 ** 3)),
        'used_gb': pick(d, 'usedGb', 'used_gb', 'usedGB'),
        'remaining_gb': pick(d, 'remainingGb', 'remaining_gb', 'leftGb', 'left_gb'),
        'days': pick(d, 'expiryDays', 'expiry_days', 'days', default=0),
        'created_at': pick(d, 'createdAt', 'created_at', 'created'),
        'expires_at': pick(d, 'expiresAt', 'expires_at', 'expireAt', 'expireDate'),
        'sub_url': pick(d, 'subUrl', 'sub_url', 'subscriptionUrl', 'subscription_url', 'subLink', 'link', 'url', default=''),
        'status': status, 'paused': status == 'paused',
        'finished': status in ('disabled', 'finished', 'expired'),
        'active': status not in ('deleted', 'removed'),
        'note': pick(d, 'note', default=''),
        'components': [CH_TO_PART[x] for x in (pick(d, 'channels', default=[]) or []) if x in CH_TO_PART] or None,
        'links': pick(d, 'links', 'config_links'),
    }


def norm_servers(data):
    items = unwrap(data, 'servers', 'items', 'data', 'results')
    out = []
    if isinstance(items, list):
        for x in items:
            key = pick(x, 'key', 'id', 'serverKey')
            if key is None:
                continue
            label = str(pick(x, 'name', 'label', 'title', default=key))
            out.append({'key': str(key), 'label': label, 'custom': str(pick(x, 'customName', default='') or ''),
                        'orig': str(pick(x, 'defaultName', 'originalLabel', 'default_label', 'original', default=label)),
                        'hidden': bool(pick(x, 'hidden', default=False)), 'cc': country_code(label, 'xray')})
    return out


# ---------------------------------------------------------------------------
# استخر گیگ فروشنده (همون دیتابیس Turso ربات)
# ---------------------------------------------------------------------------
def _v(row):
    return [c.get('value') if isinstance(c, dict) else c for c in row]


def _f(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


def db_seller_by_key(key):
    """(ردیف یا None، خطای اتصال؟)"""
    res = turso_exec([('SELECT user_id, gb_balance, price_per_gb, status FROM resellers WHERE api_key=?', [key])])
    if res is None:
        return None, True
    if not res[0]:
        return None, False
    v = _v(res[0][0])
    return {'rid': int(_f(v[0])), 'gb_balance': _f(v[1]), 'price_per_gb': int(_f(v[2])), 'status': v[3]}, False


def db_wallet(rid):
    res = turso_exec([('SELECT gb_balance, price_per_gb, status FROM resellers WHERE user_id=?', [rid])])
    if res is None:
        return False, 0, {'error': 'db_unreachable', 'message': 'اتصال به دیتابیس برقرار نشد.'}
    if not res[0]:
        return False, 404, {'error': 'seller_not_found', 'message': 'فروشنده پیدا نشد.'}
    v = _v(res[0][0])
    return True, 200, {'gb_balance': _f(v[0]), 'price_per_gb': int(_f(v[1])), 'status': v[2]}


def db_configs(rid):
    res = turso_exec([('SELECT config_id, sub_url, gb, days, label, type, created_at FROM reseller_api_configs '
                       'WHERE reseller_id=? AND active=1 ORDER BY id DESC', [rid])])
    if res is None:
        return False, 0, {'error': 'db_unreachable', 'message': 'اتصال به دیتابیس برقرار نشد.'}
    out = []
    for r in res[0]:
        v = _v(r)
        out.append(norm_cfg({'id': v[0], 'subUrl': v[1] or '', 'gb': _f(v[2]), 'expiryDays': int(_f(v[3])),
                             'label': v[4] or v[0], 'proto': v[5] or 'both', 'createdAt': v[6]}))
    return True, 200, out


def db_owns(rid, config_id):
    res = turso_exec([('SELECT 1 FROM reseller_api_configs WHERE reseller_id=? AND config_id=? AND active=1', [rid, str(config_id)])])
    return bool(res and res[0])


def db_cfg_gb(rid, config_id):
    res = turso_exec([('SELECT gb FROM reseller_api_configs WHERE reseller_id=? AND config_id=? AND active=1', [rid, str(config_id)])])
    return _f(_v(res[0][0])[0]) if res and res[0] else 0.0


def db_deduct(rid, gb):
    """فقط اگه استخر کافی باشه کم می‌کنه (شرط تو خودِ WHERE) تا منفی نشه."""
    res = turso_run([('UPDATE resellers SET gb_balance = gb_balance - ? WHERE user_id=? AND gb_balance >= ?', [gb, rid, gb])])
    return bool(res and res[0].get('affected_row_count', 0) > 0)


def db_refund(rid, gb):
    return turso_exec([('UPDATE resellers SET gb_balance = gb_balance + ? WHERE user_id=?', [gb, rid])]) is not None


def db_save_cfg(rid, config_id, sub_url, gb, days, label, ctype):
    return turso_exec([('INSERT INTO reseller_api_configs (reseller_id, config_id, sub_url, gb, days, label, type, active, created_at, source) '
                        'VALUES (?,?,?,?,?,?,?,1,?,?)',
                        [rid, str(config_id), sub_url or '', gb, days, label, ctype, datetime.now().isoformat(), 'panel'])]) is not None


def db_extend_cfg(rid, config_id, gb, days):
    turso_exec([('UPDATE reseller_api_configs SET gb = gb + ?, days = days + ? WHERE reseller_id=? AND config_id=?',
                 [gb, days, rid, str(config_id)])])


def db_set_label(rid, config_id, label):
    turso_exec([('UPDATE reseller_api_configs SET label=? WHERE reseller_id=? AND config_id=?', [label, rid, str(config_id)])])


def db_set_sub(rid, config_id, sub_url):
    turso_exec([('UPDATE reseller_api_configs SET sub_url=? WHERE reseller_id=? AND config_id=?', [sub_url, rid, str(config_id)])])


def db_deactivate(rid, config_id):
    turso_exec([('UPDATE reseller_api_configs SET active=0 WHERE reseller_id=? AND config_id=?', [rid, str(config_id)])])


def owned_or_redirect(config_id):
    if db_owns(session.get('rid'), config_id):
        return None
    flash('این کانفیگ مال شما نیست یا پیدا نشد.', 'warn')
    return redirect(url_for('configs_page'))



# (موجودی و لیست کانفیگ‌ها از دیتابیس خودِ فروشنده می‌آد: db_wallet / db_configs)


def parse_dt(s):
    try:
        dt = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
        if dt.tzinfo:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None


def cfg_info(c, now=None):
    now = now or datetime.now()
    created = parse_dt(c.get('created_at'))
    days = int(c.get('days') or 0)
    exp = parse_dt(c.get('expires_at'))
    if exp and created and not days:
        days = max(1, round((exp - created).total_seconds() / 86400))
    info = {'created': created, 'days': days, 'expiry': None, 'left': None, 'pct': 0, 'state': 'ok'}
    if created and days:
        expiry = exp or (created + timedelta(days=days))
        left = math.ceil((expiry - now).total_seconds() / 86400)
        info['expiry'] = expiry
        info['left'] = left
        elapsed = (now - created).total_seconds() / (days * 86400)
        info['pct'] = max(0, min(100, int(elapsed * 100)))
        info['state'] = 'expired' if left <= 0 else ('soon' if left <= 3 else 'ok')
    if c.get('finished'):
        info['state'] = 'expired'
    return info


def fetch_live(cr):
    """{id: کانفیگ نرمال‌شده} از سرویس‌دهنده (فعال‌ها + غیرفعال‌ها) برای مصرف/وضعیت/انقضای زنده."""
    live = {}
    for st in ('active', 'disabled'):
        page = 1
        while page <= 10:
            ok, _s, data = api_call('GET', f'/api/v1/configs?status={st}&limit=500&page={page}', **cr)
            if not ok:
                break
            items = unwrap(data, 'configs', 'items', 'data')
            items = items if isinstance(items, list) else []
            for x in items:
                if isinstance(x, dict):
                    n = norm_cfg(x)
                    live[n['config_id']] = n
            if not (isinstance(data, dict) and data.get('hasMore')):
                break
            page += 1
    return live


def merge_live(cfgs, live):
    for c in cfgs:
        lc = live.get(c['config_id'])
        if not lc:
            continue
        for k in ('used_gb', 'remaining_gb', 'expires_at', 'sub_url', 'note', 'components'):
            if lc.get(k):
                c[k] = lc[k]
        if lc.get('gb'):
            c['gb'] = lc['gb']
        c['paused'], c['finished'], c['status'] = lc['paused'], lc['finished'], lc['status']
    return cfgs


def load_data(force=False):
    """(bal, configs, redirect_response). balance و configs موازی گرفته می‌شن و ۱۵ ثانیه کش می‌شن."""
    key = session.get('api_key', '')
    hit = CACHE.get(key)
    if hit and not force and time.time() - hit[0] < CACHE_TTL:
        return hit[1], hit[2], None
    rid = session.get('rid')
    f_bal = POOL.submit(db_wallet, rid)
    f_cfg = POOL.submit(db_configs, rid)
    f_live = POOL.submit(fetch_live, creds())
    ok, status, bal = f_bal.result()
    ok2, _st, cfgs = f_cfg.result()
    try:
        if ok2:
            cfgs = merge_live(cfgs, f_live.result())
    except Exception:
        pass
    if not ok:
        expired_session(status)
        return None, None, render_page('', f'<div class="card"><p>{esc(api_msg(bal, "خطا در دریافت اطلاعات."))}</p></div>', None)
    configs = [c for c in (cfgs if ok2 else []) if c.get('active')]
    configs.sort(key=lambda c: str(c.get('created_at', '')), reverse=True)
    if ok2:
        if len(CACHE) > 200:
            CACHE.clear()
        CACHE[key] = (time.time(), bal, configs)
    return bal, configs, None


# ---------------------------------------------------------------------------
# آیکون‌ها
# ---------------------------------------------------------------------------
ICONS = {
    'grid': '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    'users': '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
    'wallet': '<path d="M20 12V8a2 2 0 0 0-2-2H5a2 2 0 0 1 0-4h13"/><path d="M3 5v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-4"/><path d="M17 14h4v-4h-4a2 2 0 0 0 0 4z"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'minus': '<path d="M5 12h14"/>',
    'search': '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    'menu': '<path d="M4 7h16M4 12h16M4 17h16"/>',
    'x': '<path d="M18 6 6 18M6 6l12 12"/>',
    'copy': '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    'trash': '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/>',
    'shield': '<path d="M12 2 4 6v6c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V6l-8-4z"/><path d="m9 12 2 2 4-4"/>',
    'out': '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'bag': '<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><path d="M3 6h18M16 10a4 4 0 0 1-8 0"/>',
    'drop': '<path d="M12 2.7s6 6.3 6 10.6a6 6 0 0 1-12 0C6 9 12 2.7 12 2.7z"/>',
    'eye': '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    'lock': '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    'phone': '<rect x="7" y="2" width="10" height="20" rx="2.5"/><path d="M11 18h2"/>',
    'layers': '<path d="m12 3 9 5-9 5-9-5 9-5z"/><path d="m3 13 9 5 9-5"/>',
    'game': '<rect x="2" y="7" width="20" height="11" rx="5.5"/><path d="M7 10.5v4M5 12.5h4"/><circle cx="15.5" cy="11.5" r=".7"/><circle cx="18" cy="13.7" r=".7"/>',
    'filter': '<path d="M4 7h9M19 7h1M4 17h1M11 17h9"/><circle cx="16" cy="7" r="2.3"/><circle cx="8" cy="17" r="2.3"/>',
    'check': '<path d="m5 12.5 4.5 4.5L19 7"/>',
    'bolt': '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z"/>',
    'shield0': '<path d="M12 2.5 4.5 6v5.8c0 4.6 3.1 8.2 7.5 9.7 4.4-1.5 7.5-5.1 7.5-9.7V6L12 2.5z"/>',
    'vpnkey': '<path d="M14 2.5H7.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V7l-4.5-4.5z"/><path d="M13.5 2.5V7h5"/><circle cx="10.6" cy="15.4" r="1.6"/><path d="m11.8 14.2 3-3"/>',
    'play': '<path d="M7 4.5v15l12-7.5-12-7.5z"/>',
    'pause': '<circle cx="12" cy="12" r="9.5"/><path d="M10 9v6M14 9v6"/>',
    'refresh': '<path d="M20 11a8 8 0 0 0-14.5-4M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.5 4M20 20v-4h-4"/>',
    'userplus': '<circle cx="9" cy="8" r="4"/><path d="M2.5 21v-1.5a5 5 0 0 1 5-5h3a5 5 0 0 1 5 5V21M19 8v6M16 11h6"/>',
    'user': '<circle cx="12" cy="8" r="4"/><path d="M4.5 21v-1a6 6 0 0 1 6-6h3a6 6 0 0 1 6 6v1"/>',
    'note': '<path d="M14 2.5H7a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5.5z"/><path d="M14 2.5V8h5"/>',
    'link': '<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/>',
    'qr': '<rect x="3" y="3" width="7" height="7" rx="1.2"/><rect x="14" y="3" width="7" height="7" rx="1.2"/><rect x="3" y="14" width="7" height="7" rx="1.2"/><path d="M14 14h3v3h-3zM20 14v.01M14 20h3M20 17v4"/>',
    'gauge': '<path d="M4.5 18.5a9.5 9.5 0 1 1 15 0"/><path d="m12 13 4-4.5"/>',
    'pencil': '<path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
    'send': '<path d="m21.5 2.5-9.5 19-2.6-8.4L2 10.5l19.5-8z"/><path d="m21.5 2.5-12 10.6"/>',
    'calclock': '<path d="M20 11V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h5M16 2.5v3M8 2.5v3M4 9.5h16"/><circle cx="17" cy="17" r="4.5"/><path d="M17 14.8V17l1.4 1"/>',
    'inf': '<path d="M12 12c-2-2.6-3.6-4-5.4-4a4 4 0 0 0 0 8c1.8 0 3.4-1.4 5.4-4zm0 0c2 2.6 3.6 4 5.4 4a4 4 0 0 0 0-8c-1.8 0-3.4 1.4-5.4 4z"/>',
}


def ico(name, size=22):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


# ---------------------------------------------------------------------------
# قالب صفحه
# ---------------------------------------------------------------------------
BASE_HTML = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#f7f6fd">
<title>__TITLE__</title>
__FONT__
<style>
:root{--p:#6a3df0;--p2:#4a26b5;--p-soft:#ece8fb;--p-ink:#3b1fa3;--bg:#f7f6fd;--card:#fff;--ink:#14112b;--mut:#7d7a94;
--line:#ebe9f5;--ok:#0f7a4d;--ok-soft:#d9f2e4;--bad:#c4302b;--bad-soft:#fde8e7;--warn:#b45f06;--warn-soft:#fff1dc;--box:#f4f2fb;}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0}
body{font-family:'Vazirmatn','Samim','Segoe UI',Tahoma,system-ui,sans-serif;background:var(--bg);color:var(--ink);font-size:15px;line-height:1.7;
padding-bottom:calc(96px + env(safe-area-inset-bottom,0px))}
a{color:inherit;text-decoration:none}
button{font-family:inherit}
#pbar{position:fixed;top:0;left:0;right:0;height:3px;background:var(--p);transform:scaleX(0);transform-origin:right;z-index:99;pointer-events:none}
#pbar.run{transform:scaleX(.85);transition:transform 8s cubic-bezier(.1,.6,.2,1)}
.wrap{max-width:560px;margin:0 auto;padding:0 16px}
/* header */
.hdr{display:flex;align-items:center;gap:10px;padding:calc(12px + env(safe-area-inset-top,0px)) 0 8px}
.avatar{width:48px;height:48px;border-radius:50%;background:#fff;border:1px solid var(--line);display:grid;place-items:center;color:var(--p);flex:none}
.chip{background:#fff;border-radius:999px;padding:0 16px;height:48px;display:flex;align-items:center;gap:8px;font-weight:700;border:1px solid var(--line);flex:none;direction:ltr}
.chip svg{color:var(--p)}
.search,.search2{flex:1;min-width:0;background:#fff;border-radius:999px;height:48px;display:flex;align-items:center;gap:8px;padding:0 16px;border:1px solid var(--line);margin:0}
.search input,.search2 input{border:0;outline:0;background:transparent;width:100%;font:inherit;font-size:14px;color:var(--ink)}
.search svg,.search2 svg{color:var(--mut);flex:none}
.search2{height:54px}
.round{width:48px;height:48px;border-radius:50%;background:#fff;border:1px solid var(--line);display:grid;place-items:center;flex:none;cursor:pointer;color:var(--ink);padding:0}
.round:active{background:var(--p-soft)}
.sbar{display:flex;gap:10px;margin:0 0 16px}
.sbar .round{width:54px;height:54px}
/* titles */
.ptitle{display:flex;align-items:flex-end;justify-content:space-between;gap:10px;margin:14px 0 16px}
.ptitle h1{margin:0;font-size:32px;line-height:1.2;font-weight:800}
.ptitle small{display:block;color:var(--mut);font-size:15px;font-weight:500;margin-top:2px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;height:54px;padding:0 22px;border-radius:999px;font:inherit;font-weight:700;font-size:16px;border:0;cursor:pointer;background:var(--p);color:#fff;box-shadow:0 10px 22px -8px rgba(106,61,240,.65);transition:transform .08s,filter .08s}
.btn:active{transform:scale(.96);filter:brightness(.92)}
.btn[disabled]{opacity:.5;pointer-events:none}
.btn.top{height:48px;padding:0 20px;font-size:15px;flex:none}
.btn.soft{background:#fff;color:var(--p-ink);box-shadow:none;border:1px solid var(--line)}
.btn.full{width:100%}
.btn.danger{background:var(--bad);box-shadow:none}
/* cards */
.card{background:var(--card);border-radius:28px;padding:18px 20px;margin-bottom:14px;border:1px solid var(--line)}
.card h3{margin:0;font-size:17px;font-weight:700}
.stats{background:#fff;border-radius:999px;padding:8px;display:flex;gap:8px;margin-bottom:14px;border:1px solid var(--line)}
.pill{flex:1;border-radius:999px;padding:10px 18px;display:flex;align-items:center;gap:12px}
.pill b{font-size:30px;font-weight:800;line-height:1.1}
.pill span{font-size:12.5px;line-height:1.4;display:block}
.pill.g{background:var(--ok-soft);color:var(--ok)}
.pill.v{background:var(--p-soft);color:var(--p-ink)}
.dot{width:10px;height:10px;border-radius:50%;background:currentColor;flex:none}
.credit{background:linear-gradient(160deg,#6c3cf2 0%,#4c27b8 100%);color:#fff;border-radius:36px;padding:20px 22px 18px;margin-bottom:14px;box-shadow:0 18px 34px -16px rgba(76,39,184,.8)}
.credit .row{display:flex;align-items:center;justify-content:space-between}
.credit .lbl{display:flex;align-items:center;gap:10px;font-weight:600;font-size:16px}
.credit .ib{width:44px;height:44px;border-radius:50%;background:rgba(255,255,255,.16);display:grid;place-items:center}
.credit .big{font-size:58px;font-weight:800;line-height:1.15;margin:12px 0 10px;direction:ltr;text-align:right}
.credit .big em{font-style:normal;font-size:32px;opacity:.75}
.credit .big u{text-decoration:none;font-size:20px;font-weight:600;opacity:.8;margin-left:6px}
.credit .foot{display:flex;justify-content:space-between;font-size:13px;opacity:.8;margin-top:12px}
.chart-head{display:flex;justify-content:space-between;align-items:flex-start}
.chart-head .t{display:flex;align-items:center;gap:10px;font-weight:700}
.chart-head .ib{width:44px;height:44px;border-radius:50%;background:#dcecfb;color:#2b6cb0;display:grid;place-items:center}
.sales{margin:10px 0 2px;font-size:14px;color:var(--mut)}
.sales b{font-size:36px;color:var(--ink);font-weight:800;margin-left:6px}
.bars{display:flex;justify-content:space-between;align-items:flex-end;gap:6px;margin-top:14px}
.bar{flex:1;text-align:center;font-size:13px;color:var(--mut)}
.bar i{display:flex;align-items:flex-end;width:22px;height:88px;margin:0 auto 6px;background:#eceaf6;border-radius:999px;overflow:hidden}
.bar i s{display:block;width:100%;background:#7ab3e8;border-radius:999px}
.bar.today{color:var(--p);font-weight:800}
.row-item{display:flex;align-items:center;gap:12px;padding:12px 0}
.row-item+.row-item{border-top:1px solid var(--line)}
.row-item .ib{width:46px;height:46px;border-radius:50%;display:grid;place-items:center;flex:none}
.row-item .n{margin-inline-start:auto;font-size:26px;font-weight:800}
.ib.red{background:var(--bad-soft);color:var(--bad)}
.ib.amb{background:var(--warn-soft);color:var(--warn)}
.row-item b{display:block;font-size:16px}
.row-item small{color:var(--mut)}
.head-line{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
.head-line a{color:var(--p);font-weight:600;font-size:14px}
/* filters */
.filters{display:none;gap:8px;overflow-x:auto;margin:-4px 0 14px;padding-bottom:2px}
.filters.show{display:flex}
.f{flex:none;border:1px solid var(--line);background:#fff;border-radius:999px;padding:7px 16px;font:inherit;font-size:13.5px;font-weight:600;color:var(--mut);cursor:pointer}
.f.on{background:var(--p);border-color:var(--p);color:#fff}
/* config cards (مثل عکس) */
.cfg{background:#fff;border-radius:30px;padding:16px;margin-bottom:14px;border:1px solid var(--line)}
.c-top{display:flex;align-items:center;gap:12px}
.c-ic{width:54px;height:54px;border-radius:50%;display:grid;place-items:center;flex:none}
.t-v{background:#ece8fb;color:var(--p)}.t-b{background:#dcecfb;color:#2b6cb0}.t-t{background:#d8f3ef;color:#0b7a6b}.t-o{background:#fdebdc;color:#c8600d}
.x-v{color:var(--p)}.x-b{color:#2b6cb0}.x-t{color:#0b7a6b}.x-o{color:#c8600d}
.c-id{min-width:0;flex:1}
.c-id b{display:block;font-size:18px;line-height:1.35;word-break:break-word}
.c-id small{color:var(--mut);font-size:13px;display:block;line-height:1.5}
.c-id small em{font-style:normal;font-weight:700}
.st{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;border-radius:999px;font-size:13px;font-weight:700;flex:none}
.st i{width:8px;height:8px;border-radius:50%;background:currentColor}
.st.ok{background:var(--ok-soft);color:var(--ok)}
.st.soon{background:var(--warn-soft);color:var(--warn)}
.st.expired{background:var(--bad-soft);color:var(--bad)}
.c-box{display:flex;align-items:center;gap:10px;background:var(--box);border-radius:24px;padding:12px 14px;margin-top:12px}
.ring{flex:none}
.ring .bg{fill:none;stroke:#e1ddf3;stroke-width:5}
.ring .fg{fill:none;stroke:var(--p);stroke-width:5;stroke-linecap:round}
.ring.soon .fg{stroke:#e8912d}.ring.expired .fg{stroke:var(--bad)}
.c-col{flex:1;min-width:0}
.c-col b{display:block;font-size:17px;line-height:1.35;font-weight:700}
.c-col small{display:block;color:var(--mut);font-size:12.5px;line-height:1.4}
.c-acts{display:flex;gap:8px;flex:none}
.rb{width:40px;height:40px;border-radius:50%;background:#fff;border:0;display:grid;place-items:center;color:var(--ink);cursor:pointer;padding:0;transition:background .1s,transform .08s}
.rb:active{background:var(--p-soft);transform:scale(.92)}
.rb.del{color:var(--bad)}
.rb.del:active{background:var(--bad-soft)}
.empty{text-align:center;color:var(--mut);padding:36px 10px}
/* form */
label.l{display:block;margin:16px 0 8px;font-weight:600;font-size:14px}
label.l small{color:var(--mut);font-weight:500}
.inp{width:100%;height:52px;border-radius:999px;border:1.5px solid var(--line);background:#fff;padding:0 18px;font:inherit;font-size:16px;color:var(--ink)}
.inp:focus{outline:none;border-color:var(--p);box-shadow:0 0 0 4px rgba(106,61,240,.12)}
.after{margin:12px 0 0;padding:10px 14px;background:var(--p-soft);border-radius:18px;color:var(--p-ink);font-size:13px}
.after .ar{display:flex;justify-content:space-between;gap:10px;line-height:1.9}
.after .ar span:first-child{color:#6b5fb0}
.after.bad{background:var(--bad-soft);color:var(--bad)}
.after.bad .ar span:first-child{color:var(--bad)}
/* flash */
.flash{border-radius:18px;padding:12px 16px;margin:6px 0 8px;font-weight:600;font-size:14px}
.flash.ok{background:var(--ok-soft);color:var(--ok)}
.flash.err{background:var(--bad-soft);color:var(--bad)}
.flash.warn{background:var(--warn-soft);color:var(--warn)}
/* bottom nav: لمس = مربع کم‌رنگ */
.nav{position:fixed;inset-inline:0;bottom:0;background:#fff;border-top:1px solid var(--line);padding-bottom:env(safe-area-inset-bottom,0px);z-index:30}
.nav .in{max-width:560px;margin:0 auto;display:flex;gap:6px;padding:6px 8px}
.nav a,.nav button{flex:1;position:relative;display:flex;flex-direction:column;align-items:center;gap:2px;padding:8px 4px 7px;color:var(--mut);font:inherit;font-size:13px;font-weight:600;background:none;border:0;border-radius:18px;cursor:pointer;transition:background .12s,transform .08s}
.nav a:active,.nav button:active,.nav .tap{background:rgba(106,61,240,.14);transform:scale(.96)}
.nav .on{color:var(--p)}
.nav .on::before{content:'';position:absolute;top:-6px;width:34px;height:3px;border-radius:0 0 4px 4px;background:var(--p)}
/* sheets */
.ov{position:fixed;inset:0;background:rgba(20,17,43,.45);opacity:0;pointer-events:none;transition:opacity .2s;z-index:40}
.ov.show{opacity:1;pointer-events:auto}
.sheet{position:fixed;inset-inline:0;bottom:0;background:#fff;border-radius:32px 32px 0 0;padding:10px 18px calc(22px + env(safe-area-inset-bottom,0px));max-height:88vh;overflow:auto;transform:translateY(105%);transition:transform .25s;z-index:50;max-width:560px;margin:0 auto}
.sheet.show{transform:none}
.sheet.tall{display:flex;flex-direction:column;overflow:hidden;max-height:92vh;padding-bottom:0}
.sheet.tall form{display:flex;flex-direction:column;min-height:0;flex:1}
.sheet .sc{overflow:auto;flex:1;padding:0 2px 10px;overscroll-behavior:contain}
.sheet .ft{border-top:1px solid var(--line);padding:10px 0 calc(14px + env(safe-area-inset-bottom,0px));background:#fff}
.grab{width:54px;height:5px;border-radius:9px;background:#d8d5e6;margin:0 auto 10px}
.sheet h2{margin:0;font-size:20px}
.sh-head{display:flex;justify-content:space-between;align-items:center;padding:6px 4px 12px;border-bottom:1px solid var(--line);margin-bottom:8px}
.sh-head button{background:none;border:0;cursor:pointer;color:var(--ink)}
.sec{color:var(--mut);font-size:13px;margin:16px 6px 6px}
.mi{display:flex;align-items:center;gap:14px;padding:13px 14px;border-radius:999px;color:var(--p-ink);font-weight:600;font-size:16px}
.mi:active{background:var(--p-soft)}
.mi.on{background:var(--p-soft)}
.hint{color:var(--mut);font-size:14px;margin:8px 4px 10px}
.tcards{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.tcards label{cursor:pointer}
.tcards input{position:absolute;opacity:0;pointer-events:none}
.tc{display:flex;flex-direction:column;align-items:center;gap:6px;text-align:center;padding:12px 4px;border:2px solid var(--line);border-radius:22px;background:#fff;font-size:12.5px;font-weight:700;line-height:1.4;height:100%;transition:background .1s,border-color .1s}
.tc i{width:38px;height:38px;border-radius:50%;display:grid;place-items:center}
.tcards input:checked+.tc{border-color:var(--p);background:var(--p-soft);color:var(--p-ink)}
.tc:active{transform:scale(.97)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px}
.bx{background:var(--box);border-radius:26px;padding:14px 12px}
.bx h4{margin:0 4px 8px;font-size:15px}
.stp{display:flex;align-items:center;justify-content:space-between;gap:4px}
.stp button{width:42px;height:42px;border-radius:50%;border:0;background:#fff;display:grid;place-items:center;cursor:pointer;color:var(--ink);padding:0;flex:none}
.stp button:active{background:var(--p-soft);transform:scale(.92)}
.stp input{width:100%;min-width:0;border:0;background:transparent;text-align:center;font:inherit;font-size:30px;font-weight:800;color:var(--ink);padding:0;outline:0}
.stp u{text-decoration:none;color:var(--mut);font-size:12px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;justify-content:center}
.chips button{border:0;background:#fff;border-radius:999px;min-width:40px;height:38px;padding:0 12px;font:inherit;font-size:14px;font-weight:700;color:var(--ink);cursor:pointer}
.chips button:active,.chips button.on{background:var(--p);color:#fff}
.frow{display:flex;gap:10px;align-items:center;margin-top:10px}
.frow .btn{flex:1}
.cstp{display:flex;align-items:center;background:var(--box);border-radius:999px;padding:4px;flex:none}
.cstp button{width:42px;height:42px;border-radius:50%;border:0;background:#fff;display:grid;place-items:center;cursor:pointer;padding:0;color:var(--ink)}
.cstp button:active{background:var(--p-soft)}
.cstp span{min-width:34px;text-align:center;font-weight:800;font-size:18px}
.cch{display:flex;align-items:center;gap:8px;margin-top:10px;color:var(--mut);font-size:13px}
.cch button{border:0;background:var(--box);border-radius:999px;height:32px;min-width:38px;font:inherit;font-weight:700;color:var(--ink);cursor:pointer}
.cch button:active{background:var(--p);color:#fff}
.drawer{position:fixed;top:0;bottom:0;right:0;width:min(82vw,340px);background:#fff;z-index:50;transform:translateX(105%);transition:transform .25s;padding:calc(18px + env(safe-area-inset-top,0px)) 16px 24px;overflow:auto}
.drawer.show{transform:none}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.brand .avatar{width:54px;height:54px}
.brand b{color:var(--p);font-size:19px;display:block;line-height:1.3}
.brand small{color:var(--mut)}
.brand .round{margin-inline-start:auto;width:44px;height:44px;background:#f1effa;border:0}
/* login */
.lg{min-height:100dvh;display:flex;align-items:center;justify-content:center;padding:24px 18px;background:radial-gradient(700px 420px at 90% -10%,rgba(106,61,240,.22),transparent 60%),var(--bg)}
.lg .box{width:100%;max-width:400px}
.lg .logo{width:72px;height:72px;border-radius:50%;margin:0 auto 14px;display:grid;place-items:center;background:linear-gradient(160deg,#6c3cf2,#4c27b8);color:#fff}
.lg h1{text-align:center;margin:0;font-size:28px}
.lg p.s{text-align:center;color:var(--mut);margin:4px 0 22px}
.lg .fld{position:relative}
.lg .fld input{direction:ltr;text-align:left;padding-inline:16px 50px;font-family:ui-monospace,Menlo,Consolas,monospace}
.lg .fld button{position:absolute;right:8px;top:8px;width:36px;height:36px;border:0;background:none;color:var(--mut);cursor:pointer}

/* ساخت: چی به مشتری برسه */
.qh{margin:18px 4px 2px;font-size:21px;font-weight:800}
.qs{color:var(--mut);font-size:15px;margin:0 4px 14px}
.opts{display:flex;flex-wrap:wrap;gap:12px}
.opt{position:relative;cursor:pointer}
.opt input{position:absolute;opacity:0;pointer-events:none}
.op{display:flex;align-items:center;gap:10px;height:60px;padding:0 18px;border-radius:999px;border:2.5px solid var(--line);background:#fff;font-weight:800;font-size:17px;color:var(--ink);transition:background .12s,border-color .12s,transform .08s}
.op:active{transform:scale(.97)}
.op .ck{width:30px;height:30px;border-radius:9px;border:2px solid #dedbec;display:grid;place-items:center;color:#fff;flex:none}
.op .ck svg{opacity:0}
.op.rd .ck{border-radius:50%}
.op .ri{color:#8e8ba3;display:grid}
.opt input:checked+.op{border-color:var(--p);background:#eee9fd}
.opt input:checked+.op .ck{background:var(--p);border-color:var(--p)}
.opt input:checked+.op .ck svg{opacity:1}
.opt input:checked+.op .ri{color:var(--p)}
.dnsc{display:flex;align-items:center;gap:14px;background:#fcebdf;border-radius:32px;padding:22px 20px;margin-top:18px}
.dnsc.off{display:none}
.dnsc .di{color:#c8600d;flex:none}
.dnsc .tx{flex:1;min-width:0}
.dnsc .tx b{display:block;font-size:19px;line-height:1.5}
.dnsc .tx small{display:block;color:#8a8498;font-size:15px;line-height:1.7}
.sw{position:relative;width:62px;height:38px;flex:none}
.sw input{position:absolute;opacity:0;inset:0;width:100%;height:100%;margin:0;cursor:pointer;z-index:2}
.sw i{position:absolute;inset:0;border-radius:999px;background:#c7c5d2;transition:background .15s}
.sw i::after{content:'';position:absolute;top:4px;left:4px;width:30px;height:30px;border-radius:50%;background:#fff;box-shadow:0 2px 5px rgba(0,0,0,.25);transition:transform .15s}
.sw input:checked+i{background:var(--p)}
.sw input:checked+i::after{transform:translateX(24px)}
.recv{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:20px 4px 6px}
.recv>span.lb{color:var(--mut);font-size:16px}
.rc{display:inline-flex;align-items:center;gap:8px;background:#ece8fb;color:var(--p);border-radius:999px;padding:8px 16px;font-weight:800;font-size:16px}
.cfg{cursor:pointer}
/* صفحه‌ی جزئیات کانفیگ */
.dv{max-width:560px;margin:0 auto;padding:0 16px calc(40px + env(safe-area-inset-bottom,0px))}
.dh{position:sticky;top:0;z-index:20;background:rgba(247,246,253,.94);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);margin:0 -16px 14px;padding:calc(14px + env(safe-area-inset-top,0px)) 16px 10px}
.dh .r1{display:flex;align-items:center;gap:12px}
.gauge{width:74px;height:74px;border-radius:50%;background:#e9e4fb;color:var(--p);display:grid;place-items:center;flex:none;border:0;cursor:pointer;padding:0}
.dh .nm{flex:1;min-width:0}
.dn{font-size:30px;font-weight:800;line-height:1.3;direction:rtl;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:right}
.xb{width:56px;height:56px;border-radius:50%;background:#eceaf6;display:grid;place-items:center;color:var(--ink);flex:none}
.pen{color:var(--mut);flex:none;background:none;border:0;padding:4px;display:grid;cursor:pointer}
.dsp{display:flex;align-items:center;gap:10px;margin-top:4px;font-size:16px;flex-wrap:wrap}
.dsp .st{font-size:16px;padding:7px 16px}
.dsp .vb{color:var(--p);font-weight:800}
.dsp .cn{color:var(--mut)}
.dsp .sep{color:#cfcce0}
.dcs{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}
.dst{display:flex;align-items:center;justify-content:space-between;gap:6px;background:#f6f5fc;border:1.5px solid #e6e3f3;border-radius:34px;padding:16px 14px}
.dst .t{min-width:0}
.dst .t small{display:block;color:var(--mut);font-size:14.5px}
.dst .t b{display:block;font-size:30px;font-weight:800;line-height:1.35}
.dst .t em{display:block;font-style:normal;color:var(--mut);font-size:13.5px;white-space:nowrap}
.bring{position:relative;width:78px;height:78px;flex:none;display:grid;place-items:center}
.bring svg.rg{position:absolute;inset:0;width:100%;height:100%}
.bring .tk{fill:none;stroke:#e4e0f5;stroke-width:9}
.bring .pg{fill:none;stroke:var(--p);stroke-width:9;stroke-linecap:round}
.bring.soon .pg{stroke:#e8912d}.bring.expired .pg{stroke:var(--bad)}
.bring span{position:relative;font-weight:700;color:var(--p);font-size:16px;display:grid}
.dc{background:#fff;border:1.5px solid #e9e6f4;border-radius:34px;padding:20px;margin-bottom:16px;overflow:hidden}
.dc .ct{display:flex;align-items:center;justify-content:space-between;gap:10px}
.dc .ct h3{display:flex;align-items:center;gap:12px;margin:0;font-size:21px;font-weight:800}
.dc .ct h3 svg{color:var(--p)}
.dc .ct>span{color:var(--mut);font-size:17px}
.dc .ct>span b{color:var(--ink)}
.lk{background:#f4f2fb;border-radius:999px;padding:16px 22px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:15px;direction:ltr;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin:16px 0 12px;color:#3a3750}
.b2{display:flex;gap:12px}
.db{flex:1;display:inline-flex;align-items:center;justify-content:center;gap:10px;height:62px;border-radius:999px;border:0;font:inherit;font-size:19px;font-weight:800;cursor:pointer;background:#f1effa;color:var(--p-ink);transition:transform .08s,filter .08s;padding:0 12px}
.db:active{transform:scale(.97);filter:brightness(.95)}
.db.pr{flex:1.25;background:var(--p);color:#fff;box-shadow:0 12px 24px -10px rgba(106,61,240,.7)}
.db.gr{background:#f1f0f8;color:var(--ink)}
.db.dg{background:#fdeceb;color:var(--bad);width:100%;flex:none}
.rrow{display:flex;align-items:center;justify-content:space-between;margin-top:22px}
.rrow>span{font-size:19px;color:var(--mut);font-weight:700}
.pst{display:flex;align-items:center;gap:8px;background:#f4f2fb;border-radius:999px;padding:5px}
.pst button{width:54px;height:54px;border-radius:50%;border:0;background:#fff;display:grid;place-items:center;color:var(--ink);cursor:pointer;padding:0;box-shadow:0 1px 2px rgba(20,17,43,.06)}
.pst button:active{background:var(--p-soft)}
.pst input{width:52px;border:0;background:transparent;text-align:center;font:inherit;font-size:24px;font-weight:800;padding:0;outline:0;color:var(--ink)}
.pst u{text-decoration:none;color:var(--mut);font-size:17px}
.qc{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}
.qc button{height:60px;border-radius:999px;border:2px solid #e6e3f3;background:#fff;font:inherit;font-size:21px;font-weight:800;color:var(--ink);cursor:pointer}
.qc button:active{background:var(--p-soft)}
.rf{margin:20px -20px -20px;padding:16px 20px;background:#f3f2fa;display:flex;justify-content:flex-end}
.cf{height:62px;padding:0 36px;border-radius:999px;border:0;background:var(--p);color:#fff;font:inherit;font-weight:800;font-size:19px;cursor:pointer}
.cf[disabled]{background:#ddd7f7;pointer-events:none}
.sg{display:flex;width:fit-content;max-width:100%;background:#f1effa;border-radius:999px;padding:6px;gap:4px;margin-top:16px}
.sg button{height:54px;border-radius:999px;border:0;background:transparent;font:inherit;font-size:19px;font-weight:700;color:var(--mut);cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px;padding:0 20px;white-space:nowrap}
.sg button.on{background:#fff;color:var(--ink);box-shadow:0 2px 8px rgba(20,17,43,.1)}
.sg button .n{color:var(--mut);font-size:18px}
.sg.bl button.on{color:#2563eb}
.cuin{display:none;margin-top:12px}
.cr{display:flex;align-items:center;gap:12px;background:#f4f2fb;border-radius:999px;padding:8px 10px;margin-bottom:10px;min-height:60px}
.cr .cd{flex:none;min-width:66px;height:46px;border-radius:999px;background:#ebe7fa;color:var(--p);font-weight:700;font-size:19px;letter-spacing:.5px;display:grid;place-items:center}
.cr .cn2{flex:1;min-width:0;direction:ltr;text-align:right;font-size:19px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cr .rb{width:52px;height:52px}
.more{display:block;width:100%;text-align:center;border:0;background:none;color:var(--p);font:inherit;font-size:22px;font-weight:800;padding:18px 0 8px;cursor:pointer}
.hid{display:flex;align-items:center;gap:12px;color:var(--mut);font-size:19px;padding:14px 0 4px;background:none;border:0;font-family:inherit;width:100%;cursor:pointer;text-align:right}
.hd2{display:flex;align-items:center;gap:14px}
.hd2 .ic{width:70px;height:70px;border-radius:50%;display:grid;place-items:center;flex:none}
.hd2 .ic.bl{background:#dcecfb;color:#2b6cb0}
.hd2 .ic.vi{background:#ece8fb;color:var(--p)}
.hd2 .tt{flex:1;min-width:0}
.hd2 .tt b{display:block;font-size:21px;line-height:1.5}
.hd2 .tt small{color:var(--mut);font-size:16px;display:flex;align-items:center;gap:8px}
.hd2 .dt{width:12px;height:12px;border-radius:50%;background:#a09cb4;flex:none}
.hd2 .dt.on{background:var(--ok)}
.who{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:2px}
.who span{display:inline-flex;align-items:center;gap:8px;font-size:19px;font-weight:600}
.chg{height:54px;padding:0 28px;border-radius:999px;border:0;background:#f1f0f8;font:inherit;font-size:19px;font-weight:800;color:var(--ink);cursor:pointer;flex:none}
.dv textarea{width:100%;min-height:118px;border-radius:30px;border:0;background:#f4f2fb;padding:20px;font:inherit;font-size:19px;resize:vertical;color:var(--ink);margin-top:14px;outline:0}
.dv textarea:focus{box-shadow:0 0 0 3px rgba(106,61,240,.15)}
.qrbox{background:#fff;padding:14px;border-radius:24px;border:1px solid var(--line);max-width:300px;margin:14px auto}
.qrbox svg{width:100%;height:auto;display:block}
.sv{background:var(--box);border-radius:26px;padding:12px 14px;margin-bottom:10px}
.sv-t{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.sv-t .cd{min-width:48px;height:34px;border-radius:999px;background:#ebe7fa;color:var(--p);font-weight:700;display:grid;place-items:center;font-size:15px}
.sv-t small{color:var(--mut);direction:ltr;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;text-align:right}
.sv-h{display:flex;align-items:center;gap:8px;margin-top:8px;font-size:14px;color:var(--mut)}
.sv-h input{width:20px;height:20px;accent-color:var(--p)}
#toast{position:fixed;left:50%;bottom:calc(28px + env(safe-area-inset-bottom,0px));transform:translate(-50%,20px);background:#14112b;color:#fff;padding:12px 20px;border-radius:999px;font-size:14px;opacity:0;pointer-events:none;transition:.2s;z-index:80;max-width:90vw;text-align:center}
#toast.show{opacity:1;transform:translate(-50%,0)}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<div id="pbar"></div>
__BODY__
<div id="toast"></div>
<script>
var CHECK='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7"/></svg>';
function openEl(id){document.getElementById(id).classList.add('show');document.getElementById('ov').classList.add('show')}
function closeAll(){document.querySelectorAll('.sheet,.drawer').forEach(function(e){e.classList.remove('show')});document.getElementById('ov').classList.remove('show')}
function copyText(btn,txt){
  function done(){var o=btn.innerHTML;btn.innerHTML=btn.classList.contains('rb')?CHECK:'کپی شد ✓';setTimeout(function(){btn.innerHTML=o},1400)}
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(txt).then(done)}
  else{var t=document.createElement('textarea');t.value=txt;document.body.appendChild(t);t.select();try{document.execCommand('copy');done()}catch(e){}document.body.removeChild(t)}
}
function toast(m){var t=document.getElementById('toast');t.textContent=m;t.classList.add('show');clearTimeout(window._tt);window._tt=setTimeout(function(){t.classList.remove('show')},2200)}
function soon(){toast('این قابلیت هنوز به API وصل نشده')}
document.addEventListener('click',function(e){var c=e.target.closest('.cfg[data-href]');if(c&&!e.target.closest('button,a')){location.href=c.dataset.href}});
function askDelete(btn){
  var f=document.getElementById('delForm');f.action=btn.dataset.action;
  document.getElementById('delName').textContent=btn.dataset.name;
  document.getElementById('delGb').textContent=btn.dataset.gb;
  openEl('delSheet');
}
var d=document.getElementById('todayFa');
if(d){try{d.textContent=new Date().toLocaleDateString('fa-IR-u-nu-latn',{weekday:'long',day:'numeric',month:'long'})}catch(e){}}
/* بازخورد لمس + نوار پیشرفت */
(function(){
  var bar=document.getElementById('pbar');
  function go(){bar.className='run'}
  function clearTap(){document.querySelectorAll('.nav .tap').forEach(function(b){b.classList.remove('tap')})}
  document.addEventListener('pointerdown',function(e){var t=e.target.closest('.nav a,.nav button');if(t)t.classList.add('tap')},{passive:true});
  document.addEventListener('pointerup',function(){document.querySelectorAll('.nav button.tap').forEach(function(b){setTimeout(function(){b.classList.remove('tap')},220)})},{passive:true});
  document.addEventListener('pointercancel',clearTap,{passive:true});
  document.addEventListener('click',function(e){var a=e.target.closest('a[href]');if(a&&!a.target&&(a.getAttribute('href')||'').charAt(0)==='/'&&!e.defaultPrevented)go()});
  document.addEventListener('submit',function(e){if(!e.defaultPrevented)go()});
  window.addEventListener('pageshow',function(){bar.className='';clearTap()});
})();
/* شیت مشتری جدید */
(function(){
  var g=document.getElementById('nGb'); if(!g) return;
  var dy=document.getElementById('nDays'), cn=document.getElementById('nCnt'), cs=document.getElementById('nCntShow'),
      box=document.getElementById('nAfter'), btn=document.getElementById('nBtn');
  function fm(x){return String(Math.round(x*1000)/1000)}
  function row(a,b){return '<div class="ar"><span>'+a+'</span><span>'+b+'</span></div>'}
  window.stepF=function(id,dv,mn){var i=document.getElementById(id);var v=parseFloat(i.value)||0;i.value=Math.max(mn,Math.round((v+dv)*10)/10);calc()};
  window.setF=function(id,v){document.getElementById(id).value=v;calc()};
  window.stepC=function(dv){var v=(parseInt(cn.value)||1)+dv;v=Math.max(1,Math.min(20,v));cn.value=v;cs.textContent=v;calc()};
  window.setC=function(v){cn.value=v;cs.textContent=v;calc()};
  window.calc=function(){
    var bal=window.BAL||0, gv=parseFloat(g.value)||0, dv=parseInt(dy.value)||0, c=parseInt(cn.value)||1, tot=gv*c, rest=bal-tot;
    document.querySelectorAll('#nGbChips button').forEach(function(b){b.classList.toggle('on',parseFloat(b.dataset.v)===gv)});
    document.querySelectorAll('#nDayChips button').forEach(function(b){b.classList.toggle('on',parseInt(b.dataset.v)===dv)});
    var h=row('هر مشتری',fm(gv)+' GB · '+dv+' روز');
    if(c>1)h+=row('جمع '+c+' کانفیگ',fm(tot)+' GB');
    h+=row('اعتبار فعلی',fm(bal)+' GB');
    if(gv>0&&rest<0){box.className='after bad';h+=row('اعتبارت کافی نیست',fm(-rest)+' GB کمه')}
    else{box.className='after';h+=row('بعد از ساخت می‌مونه',fm(Math.max(rest,0))+' GB')}
    box.innerHTML=h;
    btn.disabled=!(gv>0&&dv>0&&rest>=-1e-9&&window.PARTS_OK!==false);
  };
  var cX=document.getElementById('pXray'),cW=document.getElementById('pWg'),cO=document.getElementById('pOvpn'),cD=document.getElementById('pDns'),tg=document.getElementById('pDnsX'),dc=document.getElementById('dnsCard'),rl=document.getElementById('rcList');
  function parts(){
    var L=[];
    if(cD.checked)L.push(['DNS','game']);
    else{if(cX.checked)L.push(['Xray','bolt']);if(cW.checked)L.push(['WireGuard','shield0']);if(cO.checked)L.push(['OpenVPN','vpnkey']);if(tg.checked)L.push(['DNS','game'])}
    return L;
  }
  function upd(){
    var L=parts();dc.classList.toggle('off',cD.checked);
    rl.innerHTML=L.map(function(p){return '<span class="rc"><span dir="ltr">'+p[0]+'</span>'+window.RCI[p[1]]+'</span>'}).join('');
    window.PARTS_OK=L.length>0;calc();
  }
  [cX,cW,cO].forEach(function(i){i.addEventListener('change',function(){if(i.checked)cD.checked=false;upd()})});
  cD.addEventListener('change',function(){if(cD.checked){cX.checked=cW.checked=cO.checked=false;tg.checked=false}upd()});
  tg.addEventListener('change',upd);
  document.getElementById('newForm').addEventListener('submit',function(){btn.disabled=true;btn.lastChild.textContent=' در حال ساخت…'});
  [g,dy].forEach(function(i){i.addEventListener('input',calc)});
  upd();
  if(location.search.indexOf('new=1')>-1)openEl('newSheet');
})();
</script>
</body>
</html>
"""


def build_page(title, body):
    font = (f'<link rel="stylesheet" href="{esc(FONT_CSS_URL)}" media="print" onload="this.media=\'all\'">'
            if FONT_CSS_URL else '')
    return (BASE_HTML.replace('__TITLE__', esc(title)).replace('__FONT__', font).replace('__BODY__', body))


def menu_items(active):
    def item(key, href, icon, label):
        return f'<a class="mi {"on" if active == key else ""}" href="{href}">{ico(icon)}<span>{label}</span></a>'
    return (
        '<div class="sec">هر روز</div>'
        + item('dash', url_for('dashboard'), 'grid', 'پیشخوان')
        + item('cfgs', url_for('configs_page'), 'users', 'کانفیگ‌ها')
        + '<div class="sec">فروشگاه</div>'
        + f'<a class="mi" href="#" onclick="closeAll();openEl(\'newSheet\');return false">{ico("plus")}<span>مشتری جدید</span></a>'
        + '<div class="sec">مالی</div>'
        + item('credit', url_for('credits_page'), 'wallet', 'اعتبارها')
        + '<div class="sec">حساب</div>'
        + item('out', url_for('logout'), 'out', 'خروج')
    )


def _opt(iid, val, label, icon, checked, rd=False):
    return (f'<label class="opt"><input type="checkbox" name="part" value="{val}" id="{iid}" {"checked" if checked else ""}>'
            f'<span class="op{" rd" if rd else ""}"><span class="ck">{ico("check", 18)}</span>'
            f'<span class="ri">{ico(icon, 24)}</span><span dir="auto">{esc(label)}</span></span></label>')


def new_sheet(bal):
    """شیت «مشتری جدید» (ساخت کانفیگ) — مثل عکس‌ها."""
    gb_bal = float(bal.get('gb_balance', 0) or 0)
    rci = json.dumps({k: ico(k, 18) for k in ('bolt', 'shield0', 'vpnkey', 'game')})
    opts = (_opt('pXray', 'xray', 'Xray', 'bolt', True) + _opt('pWg', 'wg', 'WireGuard', 'shield0', True)
            + _opt('pOvpn', 'ovpn', 'OpenVPN', 'vpnkey', True)
            + _opt('pDns', 'dns', 'فقط DNS', 'game', False, rd=True))
    gb_chips = ''.join(f'<button type="button" data-v="{v}" onclick="setF(\'nGb\',{v})">{v}</button>' for v in (1, 5, 10, 20, 50, 100))
    day_chips = ''.join(f'<button type="button" data-v="{v}" onclick="setF(\'nDays\',{v})">{v}</button>' for v in (7, 30, 60, 90))
    cnt_chips = ''.join(f'<button type="button" onclick="setC({v})">{v}</button>' for v in (5, 10, 20))
    return f"""
    <div id="newSheet" class="sheet tall">
      <div class="grab"></div>
      <div class="sh-head"><h2>مشتری جدید</h2><button type="button" onclick="closeAll()" aria-label="بستن">{ico('x', 22)}</button></div>
      <form id="newForm" method="post" action="{url_for('create_config')}" autocomplete="off">
        <div class="sc">
          <div class="qh">چی به مشتری برسه</div>
          <p class="qs">هرچی باید بگیره رو تیک بزن</p>
          <div class="opts">{opts}</div>

          <div class="dnsc" id="dnsCard">
            <span class="di">{ico('game', 30)}</span>
            <div class="tx"><b>«سرویس DNS» هم باهاش بره</b>
              <small>پینگ بازی و یوتیوب رو درست می‌کنه. مصرفش هم از همین حجم کم می‌شه.</small></div>
            <label class="sw"><input type="checkbox" name="part" value="dns_extra" id="pDnsX"><i></i></label>
          </div>
          <div class="recv"><span class="lb">دست مشتری می‌رسه</span><span id="rcList" style="display:contents"></span></div>

          <label class="l" for="nLabel">اسم مشتری <small>· اختیاری</small></label>
          <input class="inp" id="nLabel" type="text" name="label" maxlength="40" placeholder="مثلاً: احمدی">

          <div class="two">
            <div class="bx">
              <h4>حجم</h4>
              <div class="stp">
                <button type="button" onclick="stepF('nGb',-1,1)" aria-label="کم">{ico('minus', 20)}</button>
                <div style="text-align:center"><input id="nGb" name="gb" type="number" step="0.1" min="0.1" inputmode="decimal" value="5" required><u>GB</u></div>
                <button type="button" onclick="stepF('nGb',1,1)" aria-label="زیاد">{ico('plus', 20)}</button>
              </div>
              <div class="chips" id="nGbChips">{gb_chips}</div>
            </div>
            <div class="bx">
              <h4>مدت اعتبار</h4>
              <div class="stp">
                <button type="button" onclick="stepF('nDays',-1,1)" aria-label="کم">{ico('minus', 20)}</button>
                <div style="text-align:center"><input id="nDays" name="days" type="number" step="1" min="1" inputmode="numeric" value="30" required><u>روز</u></div>
                <button type="button" onclick="stepF('nDays',1,1)" aria-label="زیاد">{ico('plus', 20)}</button>
              </div>
              <div class="chips" id="nDayChips">{day_chips}</div>
            </div>
          </div>
        </div>
        <div class="ft">
          <div id="nAfter" class="after"></div>
          <div class="frow">
            <div class="cstp">
              <button type="button" onclick="stepC(1)" aria-label="زیاد">{ico('plus', 20)}</button>
              <span id="nCntShow">1</span>
              <button type="button" onclick="stepC(-1)" aria-label="کم">{ico('minus', 20)}</button>
            </div>
            <button id="nBtn" class="btn" type="submit">{ico('plus', 20)}<span> ساخت کانفیگ</span></button>
          </div>
          <input type="hidden" name="count" id="nCnt" value="1">
          <div class="cch"><span>چند تا کانفیگ با هم می‌خوای؟</span>{cnt_chips}</div>
        </div>
      </form>
      <script>window.BAL={gb_bal};window.RCI={rci};</script>
    </div>"""


def render_page(title, content_html, bal, active='dash', q='', sub=None, action=''):
    flashes = ''.join(f'<div class="flash {esc(k)}">{esc(m)}</div>' for k, m in session.pop('_flash', []))
    credit_chip = f'{int(bal.get("gb_balance", 0))} GB' if bal else '— GB'
    nav = lambda key, href, icon, label: (
        f'<a href="{href}" class="{"on" if active == key else ""}">{ico(icon, 24)}<span>{label}</span></a>')
    sub_html = sub if sub is not None else '<small id="todayFa"></small>'
    title_html = (f'<div class="ptitle"><div><h1>{esc(title)}</h1>{sub_html}</div>{action}</div>') if title else ''
    body = f"""
    <div class="wrap">
      <header class="hdr">
        <div class="avatar">{ico('shield', 24)}</div>
        <div class="chip">{ico('wallet', 20)}<span>{esc(credit_chip)}</span></div>
        <form class="search" action="{url_for('configs_page')}" method="get">
          {ico('search', 20)}<input name="q" value="{esc(q)}" placeholder="دنبال چی می‌گردی؟ کانفیگ…" autocomplete="off">
        </form>
        <button class="round" type="button" aria-label="منو" onclick="openEl('drawer')">{ico('menu', 22)}</button>
      </header>
      {title_html}
      {flashes}
      {content_html}
    </div>

    <nav class="nav"><div class="in">
      {nav('dash', url_for('dashboard'), 'grid', 'پیشخوان')}
      {nav('cfgs', url_for('configs_page'), 'users', 'کانفیگ‌ها')}
      {nav('credit', url_for('credits_page'), 'wallet', 'اعتبارها')}
      <button type="button" onclick="openEl('allSheet')">{ico('grid', 24)}<span>همه</span></button>
    </div></nav>

    <div id="ov" class="ov" onclick="closeAll()"></div>

    <aside id="drawer" class="drawer">
      <div class="brand">
        <div class="avatar">{ico('shield', 26)}</div>
        <div><b>{esc(BRAND)}</b><small>پنل فروشندگی</small></div>
        <button class="round" type="button" onclick="closeAll()" aria-label="بستن">{ico('x', 20)}</button>
      </div>
      {menu_items(active)}
    </aside>

    <div id="allSheet" class="sheet">
      <div class="grab"></div>
      <div class="sh-head"><h2>همه بخش‌ها</h2><button type="button" onclick="closeAll()" aria-label="بستن">{ico('x', 22)}</button></div>
      {menu_items(active)}
    </div>

    <div id="delSheet" class="sheet">
      <div class="grab"></div>
      <div class="sh-head"><h2>حذف کانفیگ</h2><button type="button" onclick="closeAll()" aria-label="بستن">{ico('x', 22)}</button></div>
      <p style="margin:14px 4px 4px">کانفیگ «<b id="delName"></b>» حذف بشه؟</p>
      <p style="margin:0 4px 18px;color:var(--mut);font-size:14px">
        حجم این کانفیگ (<b id="delGb"></b> گیگ) بعد از حذف به اعتبارت اضافه می‌شه و کانفیگ از لیستت پاک می‌شه.</p>
      <form id="delForm" method="post" style="display:flex;gap:8px">
        <button class="btn danger" style="flex:1" type="submit">بله، حذف کن</button>
        <button class="btn soft" style="flex:1" type="button" onclick="closeAll()">انصراف</button>
      </form>
    </div>
    {new_sheet(bal) if bal else ''}
    """
    return build_page((title or 'پنل فروشندگی') + ' | ' + BRAND, body)


# ---------------------------------------------------------------------------
# لاگین
# ---------------------------------------------------------------------------
def login_page(error=None):
    err = f'<div class="flash err">{esc(error)}</div>' if error else ''
    body = f"""
    <div class="lg"><div class="box">
      <div class="logo">{ico('shield', 34)}</div>
      <h1>{esc(BRAND)}</h1>
      <p class="s">پنل فروشندگی</p>
      <form method="post" class="card" autocomplete="off" style="padding:22px">
        <h3 style="font-size:20px">خوش اومدی 👋</h3>
        <p style="color:var(--mut);font-size:13.5px;margin:4px 0 6px">کلید API فروشندگی‌ات رو که با <b dir="ltr">rk</b> شروع می‌شه وارد کن.</p>
        {err}
        <label class="l" for="api_key">کلید API</label>
        <div class="fld">
          <input class="inp" id="api_key" type="password" name="api_key" placeholder="rk_xxxxxxxx" required autofocus
                 autocapitalize="off" autocorrect="off" spellcheck="false">
          <button type="button" aria-label="نمایش کلید"
                  onclick="var i=document.getElementById('api_key');i.type=i.type==='password'?'text':'password'">{ico('eye', 20)}</button>
        </div>
        <button class="btn full" type="submit" style="margin-top:20px">ورود به پنل</button>
      </form>
    </div></div>
    <div id="ov" class="ov"></div>
    """
    return build_page('ورود | ' + BRAND, body)


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        api_key = request.form.get('api_key', '').strip()
        if not api_key.startswith('rk'):
            error = 'کلید API باید با rk شروع بشه.'
        elif not turso_on():
            error = 'پنل هنوز تنظیم نشده (TURSO_DATABASE_URL / TURSO_AUTH_TOKEN)؛ به ادمین اطلاع بده.'
        else:
            row, db_err = db_seller_by_key(api_key)
            if db_err:
                error = 'اتصال به سرور برقرار نشد؛ کمی بعد دوباره تلاش کن.'
            elif not row:
                error = 'کلید API درست نیست.'
            elif row['status'] != 'active':
                error = 'حساب فروشندگی شما مسدود شده.'
            else:
                session.clear()
                session['api_key'] = api_key
                session['rid'] = row['rid']
                return redirect(url_for('dashboard'))
    return login_page(error)


@app.route('/logout')
def logout():
    invalidate()
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# اجزای مشترک
# ---------------------------------------------------------------------------
def credit_card(bal, n_active):
    gb = float(bal.get('gb_balance', 0) or 0)
    whole = int(gb)
    dec = f"{gb - whole:.3f}"[1:] if gb - whole else ''
    price = int(bal.get('price_per_gb', 0) or 0)
    return f"""
    <section class="credit">
      <div class="row">
        <div class="lbl"><span class="ib">{ico('wallet', 22)}</span><span>اعتبار</span></div>
      </div>
      <div class="big">{whole}<em>{dec}</em><u>GB</u></div>
      <div class="foot"><span>قیمت هر گیگ: {price:,} تومان</span><span>{n_active} کانفیگ فعال</span></div>
    </section>"""


def ring_svg(pct_left, state):
    c = 100.53
    off = c * (1 - max(0, min(100, pct_left)) / 100)
    return (f'<svg class="ring {state}" width="44" height="44" viewBox="0 0 40 40">'
            f'<circle class="bg" cx="20" cy="20" r="16"/>'
            f'<circle class="fg" cx="20" cy="20" r="16" stroke-dasharray="{c:.1f}" stroke-dashoffset="{off:.1f}" transform="rotate(-90 20 20)"/></svg>')


def cfg_card(c):
    info = cfg_info(c)
    st = info['state']
    st_txt = {'ok': 'فعال', 'soon': 'رو به انقضا', 'expired': 'منقضی'}[st]
    label = c.get('label') or c.get('config_id')
    name, icon, col = TYPE_META.get(c.get('type'), (c.get('type') or 'کانفیگ', 'shield', 'v'))
    sub = c.get('sub_url') or ''
    hay = esc(f"{label} {c.get('type', '')} {c.get('config_id', '')} {sub}".lower())

    gb_total = float(c.get('gb') or 0)
    rem = None
    for k in ('remaining_gb', 'left_gb'):
        if c.get(k) is not None:
            try:
                rem = float(c.get(k))
            except Exception:
                pass
            break
    if rem is None and c.get('used_gb') is not None:
        try:
            rem = max(0.0, gb_total - float(c.get('used_gb')))
        except Exception:
            rem = None
    if rem is not None and gb_total > 0:
        vol_main, vol_sub, frac = f'{fmt_gb(rem)} GB', f'مانده از {fmt_gb(gb_total)} GB', rem / gb_total * 100
    else:
        vol_main, vol_sub, frac = f'{fmt_gb(gb_total)} GB', 'حجم کل', 100 - info['pct']

    if info['left'] is None:
        d_main, d_sub = '—', ''
    elif info['left'] <= 0:
        d_main, d_sub = 'منقضی', jdate(info['expiry'])
    else:
        d_main, d_sub = f"{info['left']} روز", f"تا {jdate(info['expiry'])}"

    copy_btn = (f'<button class="rb" type="button" aria-label="کپی لینک" onclick="copyText(this, this.dataset.t)" '
                f'data-t="{esc(proxy_url(c))}">{ico("copy", 19)}</button>') if sub else ''
    return f"""
    <div class="cfg" data-s="{hay}" data-st="{st}" data-href="{url_for('config_detail', config_id=c['config_id'])}">
      <div class="c-top">
        <span class="c-ic t-{col}">{ico(icon, 26)}</span>
        <div class="c-id"><b>{esc(label)}</b><small><em class="x-{col}">{esc(name)}</em> · ساخت {jdate(info['created'])}</small></div>
        <span class="st {st}"><i></i>{st_txt}</span>
      </div>
      <div class="c-box">
        {ring_svg(frac, st)}
        <div class="c-col"><b>{esc(vol_main)}</b><small>{esc(vol_sub)}</small></div>
        <div class="c-col"><b>{esc(d_main)}</b><small>{esc(d_sub)}</small></div>
        <div class="c-acts">
          {copy_btn}
          <button class="rb del" type="button" aria-label="حذف" onclick="askDelete(this)"
                  data-action="{url_for('delete_config', config_id=c['config_id'])}"
                  data-name="{esc(label)}" data-gb="{esc(fmt_gb(c.get('gb')))}">{ico('trash', 19)}</button>
        </div>
      </div>
    </div>"""


NEW_BTN = f'<button class="btn top" type="button" onclick="openEl(\'newSheet\')">مشتری جدید {ico("plus", 20)}</button>'


# ---------------------------------------------------------------------------
# پیشخوان
# ---------------------------------------------------------------------------
PERSIAN_DAY = {0: 'د', 1: 'س', 2: 'چ', 3: 'پ', 4: 'ج', 5: 'ش', 6: 'ی'}  # Monday=0


@app.route('/')
@login_required
def dashboard():
    bal, configs, resp = load_data()
    if resp:
        return resp
    now = datetime.now()
    price = int(bal.get('price_per_gb', 0) or 0)

    per_day = [0.0] * 7
    week_gb, week_n = 0.0, 0
    for c in configs:
        cr = parse_dt(c.get('created_at'))
        if not cr:
            continue
        ago = (now.date() - cr.date()).days
        if 0 <= ago < 7:
            gb = float(c.get('gb') or 0)
            per_day[ago] += gb
            week_gb += gb
            week_n += 1
    mx = max(per_day) or 1
    bars = ''
    for i in range(7):
        d = now - timedelta(days=i)
        h = 0 if per_day[i] == 0 else max(10, int(per_day[i] / mx * 100))
        bars += (f'<div class="bar {"today" if i == 0 else ""}"><i><s style="height:{h}%"></s></i>'
                 f'{PERSIAN_DAY[d.weekday()]}</div>')
    week_toman = int(week_gb * price)
    sales_txt = f'<b>{week_toman // 1000:,}</b>هزار تومان' if week_toman >= 1000 else f'<b>{week_toman:,}</b>تومان'

    infos = [(c, cfg_info(c, now)) for c in configs]
    soon = [c for c, i in infos if i['state'] == 'soon']
    expired = [c for c, i in infos if i['state'] == 'expired']
    attention = ''
    if soon or expired:
        rows = ''
        if soon:
            rows += f"""<a class="row-item" href="{url_for('configs_page', f='soon')}"><span class="ib amb">{ico('clock')}</span>
              <div><b>رو به انقضا</b><small>تا ۳ روز دیگه</small></div><span class="n" style="color:var(--warn)">{len(soon)}</span></a>"""
        if expired:
            rows += f"""<a class="row-item" href="{url_for('configs_page', f='expired')}"><span class="ib red">{ico('clock')}</span>
              <div><b>منقضی‌شده</b><small>نیاز به حذف یا تمدید</small></div><span class="n" style="color:var(--bad)">{len(expired)}</span></a>"""
        attention = f'<div class="card"><h3>نیاز به توجه <span style="color:var(--mut);font-weight:500;font-size:14px">{len(soon) + len(expired)} کانفیگ</span></h3>{rows}</div>'

    recent = ''.join(cfg_card(c) for c in configs[:3])
    recent_html = f"""
    <div class="head-line"><h3 style="font-size:18px">آخرین کانفیگ‌ها</h3><a href="{url_for('configs_page')}">همه</a></div>
    {recent}""" if recent else ''

    content = f"""
    <div style="display:flex;gap:10px;margin-bottom:14px">
      <button class="btn" type="button" style="flex:1" onclick="openEl('newSheet')">{ico('plus', 20)} مشتری جدید</button>
    </div>
    <div class="stats">
      <div class="pill g"><i class="dot"></i><b>{len(configs)}</b><span>کانفیگ<br>فعال</span></div>
      <div class="pill v"><i class="dot"></i><b>{sum(float(c.get('gb') or 0) for c in configs):g}</b><span>گیگ<br>در کانفیگ‌ها</span></div>
    </div>
    {credit_card(bal, len(configs))}
    <div class="card">
      <div class="chart-head">
        <div class="t"><span class="ib">{ico('bag', 22)}</span><span>فروش این هفته</span></div>
      </div>
      <div class="sales">{sales_txt}</div>
      <div style="color:var(--mut);font-size:14px">{week_n} سفارش • {week_gb:g} گیگ</div>
      <div class="bars">{bars}</div>
    </div>
    {attention}
    {recent_html}
    """
    return render_page('پیشخوان', content, bal, 'dash')


# ---------------------------------------------------------------------------
# کانفیگ‌ها
# ---------------------------------------------------------------------------
@app.route('/configs')
@login_required
def configs_page():
    bal, configs, resp = load_data()
    if resp:
        return resp
    q = request.args.get('q', '').strip()
    f = request.args.get('f', 'all')
    if f not in ('all', 'soon', 'expired'):
        f = 'all'
    now = datetime.now()
    n_att = sum(1 for c in configs if cfg_info(c, now)['state'] in ('soon', 'expired'))
    cards = ''.join(cfg_card(c) for c in configs)
    if not cards:
        cards = '<div class="empty">هنوز کانفیگی نساختی.<br><br><button class="btn" type="button" onclick="openEl(\'newSheet\')">مشتری جدید</button></div>'
    filt = ''.join(
        f'<button type="button" class="f {"on" if k == f else ""}" data-f="{k}">{lbl}</button>'
        for k, lbl in (('all', f'همه ({len(configs)})'), ('soon', 'رو به انقضا'), ('expired', 'منقضی')))
    content = f"""
    <div class="sbar">
      <div class="search2">{ico('search', 22)}<input id="q2" value="{esc(q)}" placeholder="اسم، شناسه یا لینک sub" autocomplete="off"></div>
      <button class="round" id="fbtn" type="button" aria-label="فیلتر">{ico('filter', 24)}</button>
    </div>
    <div class="filters {'show' if f != 'all' else ''}" id="filters">{filt}</div>
    <div id="list">{cards}</div>
    <div id="none" class="empty" style="display:none">چیزی پیدا نشد.</div>
    <script>
    (function(){{
      var qs=[document.querySelector('.search input'),document.getElementById('q2')], cur={f!r}, list=document.querySelectorAll('#list .cfg');
      function apply(){{
        var t=(qs[1].value||'').trim().toLowerCase(), n=0;
        list.forEach(function(el){{
          var ok=(cur==='all'||el.dataset.st===cur)&&(!t||el.dataset.s.indexOf(t)>-1);
          el.style.display=ok?'':'none'; if(ok)n++;
        }});
        document.getElementById('none').style.display=(list.length&&!n)?'':'none';
      }}
      qs.forEach(function(x){{x.addEventListener('input',function(){{qs.forEach(function(y){{if(y!==x)y.value=x.value}});apply()}})}});
      qs[0].closest('form').addEventListener('submit',function(e){{e.preventDefault();apply()}});
      var fl=document.getElementById('filters');
      document.getElementById('fbtn').onclick=function(){{fl.classList.toggle('show')}};
      document.querySelectorAll('#filters .f').forEach(function(b){{
        b.onclick=function(){{document.querySelectorAll('#filters .f').forEach(function(x){{x.classList.remove('on')}});b.classList.add('on');cur=b.dataset.f;apply()}};
      }});
      apply();
    }})();
    </script>
    """
    sub = f'<small>{len(configs)} فعال · {n_att} نیاز به توجه</small>'
    return render_page('کانفیگ‌ها', content, bal, 'cfgs', q=q, sub=sub, action=NEW_BTN)


# ---------------------------------------------------------------------------
# ساخت کانفیگ (از شیت «مشتری جدید»)
# ---------------------------------------------------------------------------
@app.route('/configs/new')
@login_required
def new_config():
    return redirect(url_for('configs_page', new=1))


@app.route('/configs/create', methods=['POST'])
@login_required
def create_config():
    parts = set(request.form.getlist('part'))
    if 'dns' in parts:
        comps = ['dns']
    else:
        comps = [k for k in ('xray', 'wg', 'ovpn') if k in parts]
        if 'dns_extra' in parts:
            comps.append('dns')
    if not comps:
        flash('حداقل یه مورد برای مشتری انتخاب کن.', 'err')
        return redirect(url_for('configs_page'))
    ctype, _native = components_to_type(comps)
    channels = [PART_TO_CH[k] for k in comps]
    try:
        gb = float(request.form.get('gb', ''))
        days = int(float(request.form.get('days', '')))
        count = max(1, min(20, int(request.form.get('count', '1') or 1)))
    except ValueError:
        flash('حجم و مدت باید عدد باشن.', 'err')
        return redirect(url_for('configs_page'))
    if gb <= 0 or days <= 0:
        flash('حجم و مدت باید بیشتر از صفر باشن.', 'err')
        return redirect(url_for('configs_page'))
    label = request.form.get('label', '').strip()[:40] or None
    rid = session.get('rid')
    cr = creds()
    defaults = load_names('')
    stamp = int(time.time())

    def one(i):
        lbl = (f'{label}-{i + 1}' if count > 1 else label) if label else f'{rid}-{stamp}-{i + 1}'
        if not db_deduct(rid, gb):                       # اول از استخر کم می‌شه
            return False, 0, {'error': 'insufficient_pool'}
        res = api_call('POST', '/api/v1/configs', json_body={'gb': gb, 'label': lbl, 'expiryDays': days, 'channels': channels},
                       timeout=25, **cr)
        data = res[2] if isinstance(res[2], dict) else {}
        cid = pick(data, 'id', 'uuid', 'username')
        if not res[0] or cid is None:                    # ساخت نشد → گیگ برمی‌گرده
            db_refund(rid, gb)
            return False, res[1], data if not res[0] else {'error': 'no_id', 'message': 'شناسه‌ی کانفیگ از API نیومد.'}
        sub = pick(data, 'subUrl', 'sub_url', default='')
        db_save_cfg(rid, cid, sub, gb, days, lbl, ctype)
        if defaults:                                     # اسم/مخفی‌سازی سرورها طبق تنظیمات ذخیره‌شده
            def put(kv):
                b = {'key': kv[0], 'hidden': bool(kv[1][1])}
                if kv[1][0]:
                    b['label'] = kv[1][0]
                return api_call('PUT', f'/api/v1/configs/{cid}/servers', json_body=b, **cr)
            with ThreadPoolExecutor(max_workers=4) as ex:
                list(ex.map(put, defaults.items()))
        return True, 200, {'id': cid, 'label': lbl}

    if count == 1:
        results = [one(0)]
    else:
        with ThreadPoolExecutor(max_workers=max(1, CREATE_WORKERS)) as ex:
            results = list(ex.map(one, range(count)))
    invalidate()

    oks = [d for ok, _s, d in results if ok]
    bads = [d for ok, _s, d in results if not ok]
    if oks:
        _o, _s, w = db_wallet(rid)
        rem_txt = f" استخر باقی‌مانده: {fmt_gb(w.get('gb_balance', 0))} گیگ." if _o else ''
        if count == 1:
            flash(f"✅ کانفیگ «{oks[0].get('label')}» ساخته شد.{rem_txt}")
        else:
            flash(f"✅ {len(oks)} کانفیگ ساخته شد.{rem_txt}")
    if bads:
        msg = api_msg(bads[0], 'خطا در ساخت کانفیگ.')
        flash(('⚠️ ' + msg) if not oks else f'⚠️ {len(bads)} مورد ساخته نشد: {msg}', 'err' if not oks else 'warn')
    return redirect(url_for('configs_page'))


# ---------------------------------------------------------------------------
# حذف کانفیگ + برگشت حجم
# ---------------------------------------------------------------------------
@app.route('/configs/<config_id>/delete', methods=['POST'])
@login_required
def delete_config(config_id):
    rid = session.get('rid')
    bad = owned_or_redirect(config_id)
    if bad:
        return bad
    gb_total = db_cfg_gb(rid, config_id)
    okl, _sl, live = api_call('GET', f'/api/v1/configs/{config_id}')
    used = pick(unwrap(live, 'config', 'data') if okl else {}, 'usedGb', 'used_gb', 'usedGB')
    ok, status, data = api_call('DELETE', f'/api/v1/configs/{config_id}')
    invalidate()
    if ok or status == 404:
        db_deactivate(rid, config_id)
        rg = pick(data, 'refundedGb') if isinstance(data, dict) else None
        back = _f(rg) if _f(rg) > 0 else (max(0.0, gb_total - _f(used)) if used is not None else None)
        if back is None:
            flash('کانفیگ حذف شد (مصرفش مشخص نبود، پس حجمی به استخر برنگشت).', 'warn')
        else:
            if back > 0.0005:
                db_refund(rid, back)
                flash(f'کانفیگ حذف شد و {fmt_gb(back)} گیگ به استخرت برگشت.')
            else:
                flash('کانفیگ حذف شد.')
    else:
        flash('⚠️ ' + api_msg(data, 'حذف ناموفق بود.'), 'err')
    return redirect(url_for('configs_page'))


# ---------------------------------------------------------------------------
# ترکیب اجزا ← نوع API
# ---------------------------------------------------------------------------
NATIVE_TYPES = {
    frozenset(['xray']): 'config',
    frozenset(['wg']): 'wireguard',
    frozenset(['xray', 'wg']): 'both',
    frozenset(['ovpn']): 'openvpn',
    frozenset(['dns']): 'dns',
}
PART_META = {'xray': ('Xray', 'bolt'), 'wg': ('WireGuard', 'shield0'),
             'ovpn': ('OpenVPN', 'vpnkey'), 'dns': ('DNS', 'game')}
TYPE_PARTS = {'config': ['xray'], 'wireguard': ['wg'], 'both': ['xray', 'wg'],
              'openvpn': ['ovpn'], 'dns': ['dns']}


def components_to_type(comps):
    """(نوع API، آیا ترکیب مستقیم تو API هست؟)"""
    key = frozenset(comps)
    if key in NATIVE_TYPES:
        return NATIVE_TYPES[key], True
    if 'xray' in key and 'wg' in key:
        return 'both', False
    if 'xray' in key:
        return 'config', False
    if 'wg' in key:
        return 'wireguard', False
    if 'ovpn' in key:
        return 'openvpn', False
    return 'dns', False


def parts_of(c):
    comps = c.get('components')
    if isinstance(comps, (list, tuple)) and comps:
        got = [k for k in ('xray', 'wg', 'ovpn', 'dns') if k in comps]
        if got:
            return got
    return TYPE_PARTS.get(c.get('type'), ['xray'])


# ---------------------------------------------------------------------------
# لینک‌های داخل کانفیگ (از API یا از لینک اشتراک)
# ---------------------------------------------------------------------------
SUB_CACHE = {}
COUNTRY_WORDS = [
    ('AMERICA', 'US'), ('USA', 'US'), ('UNITED STATES', 'US'), ('FINLAND', 'FI'), ('FRANCE', 'FR'),
    ('IRELAND', 'IE'), ('GERMANY', 'DE'), ('NETHERLANDS', 'NL'), ('HOLLAND', 'NL'), ('UK', 'GB'),
    ('ENGLAND', 'GB'), ('BRITAIN', 'GB'), ('TURKEY', 'TR'), ('CANADA', 'CA'), ('JAPAN', 'JP'),
    ('SINGAPORE', 'SG'), ('UAE', 'AE'), ('EMIRATES', 'AE'), ('SWEDEN', 'SE'), ('POLAND', 'PL'),
    ('ITALY', 'IT'), ('SPAIN', 'ES'), ('SWITZERLAND', 'CH'), ('RUSSIA', 'RU'), ('IRAN', 'IR'),
    ('ARMENIA', 'AM'), ('INDIA', 'IN'), ('KOREA', 'KR'), ('HONG KONG', 'HK'), ('AUSTRALIA', 'AU'),
]


def country_code(name, g):
    up = (name or '').upper()
    for w, cc in COUNTRY_WORDS:
        if re.search(r'(?<![A-Z])' + re.escape(w) + r'(?![A-Z])', up):
            return cc
    cps = [ord(ch) for ch in (name or '') if 0x1F1E6 <= ord(ch) <= 0x1F1FF]
    if len(cps) >= 2:
        return chr(cps[0] - 0x1F1E6 + 65) + chr(cps[1] - 0x1F1E6 + 65)
    return {'xray': 'XR', 'wg': 'WG', 'ovpn': 'OV'}.get(g, 'XR')


def parse_link(link, name=None):
    link = (link or '').strip()
    if '://' not in link:
        return None
    scheme = link.split('://', 1)[0].lower()
    g = 'wg' if scheme in ('wireguard', 'wg') else ('ovpn' if scheme == 'openvpn' else 'xray')
    if not name:
        if scheme == 'vmess':
            try:
                raw = link.split('://', 1)[1]
                name = json.loads(base64.b64decode(raw + '=' * (-len(raw) % 4)).decode('utf-8', 'ignore')).get('ps')
            except Exception:
                name = None
        if not name and '#' in link:
            name = unquote(link.split('#', 1)[1])
    name = (name or scheme.upper()).strip()
    return {'g': g, 'name': name, 'cc': country_code(name, g), 'link': link}


def fetch_sub_links(url):
    hit = SUB_CACHE.get(url)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    links = []
    try:
        r = HTTP.get(url, timeout=(3, 6), headers={'User-Agent': 'v2rayN/6.0'})
        txt = (r.text or '').strip()
        if '://' not in txt[:300]:
            txt = base64.b64decode(txt + '=' * (-len(txt) % 4)).decode('utf-8', 'ignore')
        links = [ln.strip() for ln in txt.splitlines() if '://' in ln]
    except Exception:
        links = []
    if len(SUB_CACHE) > 200:
        SUB_CACHE.clear()
    SUB_CACHE[url] = (time.time(), links)
    return links


def get_links(c):
    items = []
    raw = None
    for k in ('links', 'config_links', 'uris'):
        if isinstance(c.get(k), list) and c.get(k):
            raw = c[k]
            break
    if raw:
        for it in raw:
            if isinstance(it, str):
                items.append(parse_link(it))
            elif isinstance(it, dict):
                items.append(parse_link(it.get('link') or it.get('url') or it.get('uri'),
                                        it.get('name') or it.get('remark') or it.get('label')))
    elif c.get('sub_url'):
        items = [parse_link(x) for x in fetch_sub_links(c['sub_url'])]
    return [i for i in items if i]


# ---------------------------------------------------------------------------
# اسم سرورها (ذخیره تو Turso)
# ---------------------------------------------------------------------------
_TURSO = {'ready': False}
NAMES_CACHE = {}


def turso_on():
    return bool(TURSO_URL and TURSO_TOKEN)


def _targ(a):
    if a is None:
        return {'type': 'null'}
    if isinstance(a, (int, bool)):
        return {'type': 'integer', 'value': str(int(a))}
    return {'type': 'text', 'value': str(a)}


def turso_exec(stmts):
    """stmts: [(sql, args)] ← لیست ردیف‌های هر دستور، یا None اگه خطا/تنظیم‌نشده."""
    res = turso_run(stmts)
    return None if res is None else [r.get('rows', []) for r in res]


def turso_run(stmts):
    """مثل turso_exec ولی نتیجه‌ی کامل هر دستور (rows و affected_row_count) رو برمی‌گردونه."""
    if not turso_on():
        return None
    pre = []
    if not _TURSO['ready']:
        pre = [('CREATE TABLE IF NOT EXISTS server_names (owner TEXT NOT NULL, scope TEXT NOT NULL, '
                'skey TEXT NOT NULL, name TEXT NOT NULL DEFAULT "", hidden INTEGER NOT NULL DEFAULT 0, '
                'PRIMARY KEY (owner, scope, skey))', [])]
    reqs = [{'type': 'execute', 'stmt': {'sql': q, 'args': [_targ(a) for a in args]}} for q, args in pre + list(stmts)]
    reqs.append({'type': 'close'})
    try:
        r = HTTP.post(TURSO_URL + '/v2/pipeline', json={'requests': reqs},
                      headers={'Authorization': 'Bearer ' + TURSO_TOKEN}, timeout=(4, 8))
        if r.status_code >= 400:
            return None
        res = r.json().get('results', [])[:-1]
        out = []
        for x in res:
            if x.get('type') != 'ok':
                return None
            out.append(x['response']['result'])
        _TURSO['ready'] = True
        return out[len(pre):]
    except Exception:
        return None


def owner_id():
    rid = session.get('rid')
    if rid:
        return f'r{rid}'
    return hashlib.sha256(session.get('api_key', '').encode()).hexdigest()[:32]


def load_names(scope, owner=None):
    """{skey: (name, hidden)} — scope '' یعنی پیش‌فرض همه‌ی کانفیگ‌های این فروشنده."""
    owner = owner or owner_id()
    k = (owner, scope)
    hit = NAMES_CACHE.get(k)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    res = turso_exec([('SELECT skey, name, hidden FROM server_names WHERE owner=? AND scope=?', [owner, scope])])
    if res is None:
        return hit[1] if hit else {}
    m = {}
    for row in res[0]:
        v = [c.get('value') for c in row]
        m[v[0]] = (v[1] or '', int(v[2] or 0))
    if len(NAMES_CACHE) > 500:
        NAMES_CACHE.clear()
    NAMES_CACHE[k] = (time.time(), m)
    return m


def save_names(scope, items, also_default=False):
    """items: [(skey, name, hidden)] — اسم خالی و hidden=0 یعنی پاک شدن تنظیم."""
    owner = owner_id()
    stmts = []
    for sc in ([scope, ''] if also_default and scope else [scope]):
        for skey, name, hid in items:
            if not name and not hid:
                stmts.append(('DELETE FROM server_names WHERE owner=? AND scope=? AND skey=?', [owner, sc, skey]))
            else:
                stmts.append(('INSERT INTO server_names (owner, scope, skey, name, hidden) VALUES (?,?,?,?,?) '
                              'ON CONFLICT(owner, scope, skey) DO UPDATE SET name=excluded.name, hidden=excluded.hidden',
                              [owner, sc, skey, name, int(hid)]))
    ok = turso_exec(stmts) is not None if stmts else True
    for sc in (scope, ''):
        NAMES_CACHE.pop((owner, sc), None)
    return ok


def link_key(link, g):
    try:
        if link.lower().startswith('vmess://'):
            raw = link.split('://', 1)[1].split('#')[0]
            j = json.loads(base64.b64decode(raw + '=' * (-len(raw) % 4)).decode('utf-8', 'ignore'))
            return f"{g}|{j.get('add')}:{j.get('port')}"
        u = urlsplit(link)
        return f'{g}|{u.hostname}:{u.port}'
    except Exception:
        return f'{g}|?'


def rename_link(link, name):
    try:
        if link.lower().startswith('vmess://'):
            raw = link.split('://', 1)[1].split('#')[0]
            j = json.loads(base64.b64decode(raw + '=' * (-len(raw) % 4)).decode('utf-8', 'ignore'))
            j['ps'] = name
            return 'vmess://' + base64.b64encode(json.dumps(j, ensure_ascii=False).encode()).decode()
        return link.split('#', 1)[0] + '#' + quote(name)
    except Exception:
        return link


def decorate(links, cfg_map, def_map):
    """کلید یکتا + اعمال اسم/مخفی‌سازی: اول تنظیم همین مشتری، بعد پیش‌فرض فروشنده."""
    seen = {}
    for l in links:
        k = link_key(l['link'], l['g'])
        seen[k] = seen.get(k, 0) + 1
        l['skey'] = k if seen[k] == 1 else f'{k}#{seen[k]}'
        l['orig'] = l['name']
        cn, ch = cfg_map.get(l['skey'], ('', None))
        dn, dh = def_map.get(l['skey'], ('', 0))
        l['name'] = cn or dn or l['orig']
        l['hidden'] = bool(ch if l['skey'] in cfg_map else dh)
        if l['name'] != l['orig']:
            l['link'] = rename_link(l['link'], l['name'])
    return links



# ---------------------------------------------------------------------------
# لینک اشتراک خودِ پنل (اسم‌های دلخواه رو تو خروجی اعمال می‌کنه)
# ---------------------------------------------------------------------------
def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip('=')


def _sig(data):
    return _b64u(hmac.new(app.secret_key.encode(), data.encode(), hashlib.sha256).digest())[:24]


def proxy_url(c):
    """لینک اشتراک پنل برای این کانفیگ؛ اگه Turso تنظیم نباشه همون لینک اصلی."""
    orig = c.get('sub_url') or ''
    if not orig or not turso_on() or not PROXY_SUB:
        return orig
    payload = _b64u(json.dumps({'o': owner_id(), 'c': str(c.get('config_id')), 'u': orig},
                               separators=(',', ':')).encode())
    base = PUBLIC_URL or request.url_root.rstrip('/')
    return f'{base}/s/{payload}.{_sig(payload)}'


@app.route('/s/<token>')
def public_sub(token):
    try:
        payload, sig = token.rsplit('.', 1)
        if not hmac.compare_digest(sig, _sig(payload)):
            return Response('forbidden', 403)
        d = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        owner, cid, url = d['o'], d['c'], d['u']
    except Exception:
        return Response('bad token', 400)
    try:
        r = HTTP.get(url, timeout=(4, 10), headers={'User-Agent': request.headers.get('User-Agent', 'v2rayN/6.0')})
        raw = (r.text or '').strip()
        body = raw
        if '://' not in raw[:300]:
            body = base64.b64decode(raw + '=' * (-len(raw) % 4)).decode('utf-8', 'ignore')
    except Exception:
        return Response('upstream error', 502)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not any('://' in ln for ln in lines):       # فرمت ناشناخته → دست‌نخورده
        return Response(raw, mimetype='text/plain')
    items = [parse_link(ln) for ln in lines]
    links = [i for i in items if i]
    links = decorate(links, load_names(cid, owner), load_names('', owner))
    out = '\n'.join(l['link'] for l in links if not l['hidden'])
    resp = Response(base64.b64encode(out.encode()).decode(), mimetype='text/plain')
    for h in ('subscription-userinfo', 'profile-update-interval', 'profile-title', 'content-disposition'):
        if r.headers.get(h):
            resp.headers[h] = r.headers[h]
    return resp



def qr_svg(text):
    try:
        import qrcode
        qr = qrcode.QRCode(border=2)
        qr.add_data(text)
        qr.make(fit=True)
        m = qr.get_matrix()
        n = len(m)
        d = ''.join(f'M{x} {y}h1v1h-1z' for y, row in enumerate(m) for x, v in enumerate(row) if v)
        return (f'<svg viewBox="0 0 {n} {n}" shape-rendering="crispEdges" xmlns="http://www.w3.org/2000/svg">'
                f'<path d="{d}" fill="#14112b"/></svg>')
    except Exception:
        return ''


def usage_of(c):
    total = float(c.get('gb') or 0)
    used = rem = None
    for k in ('used_gb', 'usage_gb'):
        if c.get(k) is not None:
            try:
                used = float(c.get(k))
            except Exception:
                pass
            break
    for k in ('remaining_gb', 'left_gb'):
        if c.get(k) is not None:
            try:
                rem = float(c.get(k))
            except Exception:
                pass
            break
    known = used is not None or rem is not None
    if used is None and rem is not None:
        used = max(0.0, total - rem)
    if rem is None and used is not None:
        rem = max(0.0, total - used)
    if not known:
        used, rem = 0.0, total
    return total, used, rem, known


def fmt_size(gb):
    return f'{round(gb * 1024)}MB' if gb < 1 else f'{fmt_gb(gb)}GB'


def bring(frac, inner, state='ok'):
    cc = 2 * math.pi * 34
    off = cc * (1 - max(0.0, min(1.0, frac)))
    return (f'<div class="bring {state}"><svg class="rg" viewBox="0 0 80 80"><circle class="tk" cx="40" cy="40" r="34"/>'
            f'<circle class="pg" cx="40" cy="40" r="34" stroke-dasharray="{cc:.1f}" stroke-dashoffset="{off:.1f}" '
            f'transform="rotate(-90 40 40)"/></svg><span>{inner}</span></div>')


DETAIL_JS = r"""
var CUR='__CUR__',SHOW=false,LIM=6;
function rows(){
  var tot=0,n=0;
  document.querySelectorAll('.cr').forEach(function(r){
    if(r.dataset.g!==CUR){r.style.display='none';return}
    tot++;var v=SHOW||n<LIM;r.style.display=v?'':'none';if(v)n++;
  });
  var m=document.getElementById('more');
  if(m){m.style.display=tot>LIM?'':'none';m.textContent=SHOW?'نمایش کمتر':'نمایش همه '+tot+' کانفیگ'}
  var e=document.getElementById('noRows');if(e)e.style.display=tot?'none':'';
}
function tabTo(k){CUR=k;SHOW=false;document.querySelectorAll('.tb').forEach(function(b){b.classList.toggle('on',b.dataset.k===k)});rows()}
function toggleMore(){SHOW=!SHOW;rows()}
function copyAll(btn){
  var t=[];document.querySelectorAll('.cr').forEach(function(r){if(r.dataset.g===CUR)t.push(r.dataset.t)});
  if(!t.length){toast('چیزی برای کپی نیست');return}
  copyText(btn,t.join('\n'));
}
var rg=document.getElementById('rGb'),rdv=document.getElementById('rDays'),rbtn=document.getElementById('rBtn'),cu=document.getElementById('rCustom');
function chk(){var g=parseFloat(rg.value)||0;rbtn.disabled=!((g>0&&g<=window.BAL)||rdv.value!=='')}
function stepR(d){var v=parseFloat(rg.value)||0;rg.value=Math.max(0,Math.round((v+d)*10)/10);chk()}
function pickD(b,v){
  document.querySelectorAll('#dSeg button').forEach(function(x){x.classList.remove('on')});
  b.classList.add('on');
  if(v==='c'){cu.style.display='block';rdv.value=cu.value||'';cu.focus()}
  else{cu.style.display='none';rdv.value=v}
  chk();
}
cu.addEventListener('input',function(){rdv.value=cu.value;chk()});
rg.addEventListener('input',chk);
function renameCfg(){var n=prompt('اسم جدید مشتری:',LBL);if(n&&n.trim()&&n.trim()!==LBL){document.getElementById('lblIn').value=n.trim();document.getElementById('lblForm').submit()}}
function post(u,d,cb){var f=new URLSearchParams(d);fetch(u,{method:'POST',body:f,headers:{'Content-Type':'application/x-www-form-urlencoded'}}).then(function(r){return r.json()}).then(function(j){cb(j)}).catch(function(){cb({ok:false,message:'اتصال برقرار نشد'})})}
function segTg(b,k,save){
  document.querySelectorAll('#tgSeg button').forEach(function(x){x.classList.remove('on')});b.classList.add('on');
  var s=document.getElementById('tgSub'),d=document.getElementById('tgDot');
  var show=(k==='show');s.textContent=show?'به این مشتری نشون داده می‌شه':'به این مشتری نشون داده نمی‌شه';d.classList.toggle('on',show);
  if(save!==false)post('/configs/'+encodeURIComponent(ID)+'/tgproxy',{v:k},function(j){toast(j.ok?'ذخیره شد':(j.message||'ذخیره نشد'))});
}
var note=document.getElementById('note'),note0=note.value;
note.addEventListener('blur',function(){if(note.value===note0)return;post('/configs/'+encodeURIComponent(ID)+'/note',{note:note.value},function(j){if(j.ok){note0=note.value}toast(j.ok?'یادداشت ذخیره شد':(j.message||'ذخیره نشد'))})});
rows();chk();
"""


@app.route('/configs/<config_id>')
@login_required
def config_detail(config_id):
    bal, configs, resp = load_data()
    if resp:
        return resp
    c = next((x for x in configs if str(x.get('config_id')) == config_id), None)
    if not c:
        flash('کانفیگ پیدا نشد (شاید حذف شده).', 'warn')
        return redirect(url_for('configs_page'))

    c = dict(c)
    f_live = POOL.submit(api_call, 'GET', f"/api/v1/configs/{config_id}", **creds())
    f_srv = POOL.submit(api_call, 'GET', f"/api/v1/configs/{config_id}/servers", **creds())
    ok_l, _sl, ldata = f_live.result()
    if ok_l and isinstance(ldata, dict):          # مصرف/وضعیت زنده از سرویس‌دهنده
        lc = norm_cfg(unwrap(ldata, 'config', 'data'))
        for k in ('used_gb', 'remaining_gb', 'expires_at'):
            if lc.get(k) is not None:
                c[k] = lc[k]
        if lc.get('gb'):
            c['gb'] = lc['gb']
        c['paused'], c['status'] = lc['paused'], lc['status']
        if lc.get('sub_url'):
            c['sub_url'] = lc['sub_url']
    ok_s, _st_s, sdata = f_srv.result()
    servers = norm_servers(sdata) if ok_s else []
    info = cfg_info(c)
    st = info['state']
    st_txt = {'ok': 'فعال', 'soon': 'رو به انقضا', 'expired': 'منقضی'}[st]
    if c.get('paused'):
        st, st_txt = 'soon', 'قطع موقت'
    label = str(c.get('label') or c.get('config_id'))
    orig_sub = c.get('sub_url') or ''
    sub = proxy_url(c)
    total, used, rem, known = usage_of(c)
    vpct = int(round(rem / total * 100)) if total > 0 else 0
    connected = known and used > 0
    gb_bal = float(bal.get('gb_balance', 0) or 0)

    # زمان
    days = info['days']
    if not days:
        t_main, t_sub, t_frac, t_txt = '∞', 'بدون انقضا', 1.0, 'نامحدود'
    elif info['left'] is None:
        t_main, t_sub, t_frac, t_txt = '—', '', 0.0, '—'
    elif info['left'] <= 0:
        t_main, t_sub, t_frac, t_txt = 'منقضی', jdate(info['expiry']), 0.0, 'منقضی شده'
    else:
        t_main, t_sub = f"{info['left']} روز", f"تا {jdate(info['expiry'])}"
        t_frac = info['left'] / days
        t_txt = f"{info['left']} روز (تا {jdate(info['expiry'])})"

    used_txt = fmt_size(used) if known else 'نامشخص'
    links = get_links(c)
    groups = [('xray', 'Xray'), ('wg', 'WireGuard'), ('ovpn', 'OpenVPN')]
    counts = {k: sum(1 for l in links if l['g'] == k) for k, _ in groups}
    tabs_def = [(k, n) for k, n in groups if counts[k]] or [('xray', 'Xray')]
    first = tabs_def[0][0]
    tabs = ''.join(
        f'<button type="button" class="tb {"on" if k == first else ""}" data-k="{k}" onclick="tabTo(\'{k}\')">'
        f'<span dir="ltr">{n}</span><span class="n">{counts[k]}</span></button>' for k, n in tabs_def)
    rows = ''.join(
        f'<div class="cr" data-g="{l["g"]}" data-t="{esc(l["link"])}"><span class="cd">{esc(l["cc"])}</span>'
        f'<span class="cn2">{esc(l["name"])}</span>'
        f'<button class="rb cp" type="button" aria-label="کپی" onclick="copyText(this,this.parentNode.dataset.t)">{ico("copy", 21)}</button></div>'
        for l in links)
    empty_row = ('<div class="empty" id="noRows" style="display:none;padding:18px 6px">'
                 'کانفیگی پیدا نشد؛ از «لینک اشتراک» بالا استفاده کن.</div>')

    who = ''.join(
        f'<span><span dir="ltr">{PART_META[k][0]}</span><span style="color:var(--p)">{ico(PART_META[k][1], 20)}</span></span>'
        for k in parts_of(c))

    qr = qr_svg(sub) if sub else ''
    qr_html = (f'<div class="qrbox">{qr}</div>' if qr else
               '<p class="hint" style="text-align:center">برای QR باید <b dir="ltr">pip install qrcode</b> بزنی.</p>')
    if not servers:
        srv_body = '<p class="hint">لیست سرورها از API نیومد.</p>'
    else:
        srv_body = ''.join(
            f'<div class="sv"><input type="hidden" name="skey" value="{esc(v["key"])}">'
            f'<input type="hidden" name="sorig" value="{esc(v["orig"])}"><input type="hidden" name="slab0" value="{esc(v["custom"])}">'
            f'<input type="hidden" name="shid0" value="{1 if v["hidden"] else 0}">'
            f'<div class="sv-t"><span class="cd">{esc(v["cc"])}</span><small dir="ltr">{esc(v["orig"])}</small></div>'
            f'<input class="inp" name="sname" maxlength="60" value="{esc(v["custom"])}" placeholder="{esc(v["orig"])}">'
            f'<label class="sv-h"><input type="checkbox" name="shide" value="{esc(v["key"])}" {"checked" if v["hidden"] else ""}> مخفی باشه</label></div>'
            for v in servers)
    srv_sheet = f"""
    <div id="srvSheet" class="sheet tall"><div class="grab"></div>
      <div class="sh-head"><h2>اسم سرورها</h2><button type="button" onclick="closeAll()" aria-label="بستن">{ico('x', 22)}</button></div>
      <form method="post" action="{url_for('save_servers', config_id=c['config_id'])}">
        <div class="sc"><p class="hint">اسم دلخواه بنویس یا مخفی‌شون کن. خالی بذاری، اسم اصلی می‌مونه.</p>{srv_body}</div>
        <div class="ft"><label class="sv-h" style="margin:0 0 10px"><input type="checkbox" name="default" value="1" checked>
          برای کانفیگ‌های بعدی هم همین اسم‌ها باشه {'' if turso_on() else '(نیاز به Turso)'}</label>
          <button class="btn full" type="submit" {"" if servers else "disabled"}>ذخیره</button></div>
      </form>
    </div>"""
    flashes = ''.join(f'<div class="flash {esc(k)}">{esc(m)}</div>' for k, m in session.pop('_flash', []))
    back = url_for('configs_page')
    cid = esc(c['config_id'])

    body = f"""
    <div class="dv">
      <div class="dh">
        <div class="r1">
          <button class="gauge" type="button" aria-label="مصرف" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">{ico('gauge', 34)}</button>
          <div class="nm">
            <div class="dn"><bdi dir="ltr">{esc(label)}</bdi></div>
            <div class="dsp"><span class="st {st}"><i></i>{st_txt}</span><span class="vb">حجمی</span><span class="sep">·</span>
              <span class="cn">{'وصل شده' if connected else 'هنوز وصل نشده'}</span></div>
          </div>
          <button class="pen" type="button" aria-label="ویرایش اسم" onclick="renameCfg()">{ico('pencil', 24)}</button>
          <a class="xb" href="{back}" aria-label="بستن">{ico('x', 26)}</a>
        </div>
      </div>
      {flashes}

      <div class="dcs">
        <div class="dst"><div class="t"><small>حجم باقی‌مانده</small><b>{esc(fmt_gb(rem))} GB</b>
            <em>{esc(used_txt)} مصرف از {esc(fmt_gb(total))} GB</em></div>
          {bring(rem / total if total > 0 else 0, f'{vpct}%', st)}</div>
        <div class="dst"><div class="t"><small>زمان باقی‌مانده</small><b>{esc(t_main)}</b><em>{esc(t_sub)}</em></div>
          {bring(t_frac, ico('clock', 30), st)}</div>
      </div>

      <div class="dc">
        <div class="ct"><h3>{ico('link', 26)}لینک اشتراک</h3></div>
        <div class="lk">{esc(sub) if sub else '—'}</div>
        <div class="b2">
          <button class="db pr" type="button" {'onclick="copyText(this,this.dataset.t)"' if sub else 'disabled'} data-t="{esc(sub)}">{ico('copy', 24)}<span>کپی لینک</span></button>
          <button class="db" type="button" style="background:#ece8fb" {'onclick="openEl(\'qrSheet\')"' if sub else 'disabled'}>{ico('qr', 24)}<span>نمایش QR</span></button>
        </div>
        {f'<button class="hid" type="button" style="font-size:15px;padding-top:12px" data-t="{esc(orig_sub)}" onclick="copyText(this,this.dataset.t)">کپی لینک اصلی (بدون اسم‌های دلخواه)</button>' if orig_sub and sub != orig_sub else ''}
      </div>

      <form class="dc" method="post" action="{url_for('renew_config', config_id=c['config_id'])}">
        <div class="ct"><h3>{ico('refresh', 26)}تمدید</h3><span>اعتبار: <b dir="ltr">{esc(fmt_gb(gb_bal))} GB</b></span></div>
        <div class="rrow"><span>حجم اضافه</span>
          <div class="pst">
            <button type="button" onclick="stepR(-5)" aria-label="کم">{ico('minus', 24)}</button>
            <u>GB</u><input id="rGb" name="gb" type="number" min="0" step="0.1" inputmode="decimal" value="0">
            <button type="button" onclick="stepR(5)" aria-label="زیاد">{ico('plus', 24)}</button>
          </div>
        </div>
        <div class="qc"><button type="button" onclick="stepR(5)">+5</button><button type="button" onclick="stepR(10)">+10</button>
          <button type="button" onclick="stepR(20)">+20</button><button type="button" onclick="stepR(50)">+50</button></div>

        <div style="margin-top:26px" class="ct"><h3>{ico('calclock', 26)}تاریخ انقضا</h3><span>الان: <b>{esc(t_txt)}</b></span></div>
        <div class="sg" id="dSeg">
          <button type="button" onclick="pickD(this,'7')">7 روز</button>
          <button type="button" onclick="pickD(this,'30')">30 روز</button>
          <button type="button" onclick="pickD(this,'0')"><span>{ico('inf', 22)}</span>بی‌نهایت</button>
          <button type="button" onclick="pickD(this,'c')">دلخواه</button>
        </div>
        <input class="inp cuin" id="rCustom" type="number" min="1" inputmode="numeric" placeholder="چند روز؟" style="display:none">
        <input type="hidden" name="days" id="rDays" value="">
        <div class="rf"><button class="cf" id="rBtn" type="submit" disabled>تایید تمدید</button></div>
      </form>

      <div class="dc">
        <div class="ct"><h3>{ico('layers', 26)}کانفیگ‌ها</h3>
          <button class="db gr" type="button" style="flex:none;height:54px;font-size:18px;padding:0 22px" onclick="copyAll(this)">{ico('copy', 22)}<span>کپی همه</span></button></div>
        <div class="sg" style="margin-bottom:16px">{tabs}</div>
        {rows}{empty_row}
        <button class="more" id="more" type="button" onclick="toggleMore()"></button>
        <button class="hid" type="button" onclick="openEl('srvSheet')">{ico('filter', 26)}<span>مخفی یا تغییر اسم سرورها برای این مشتری</span></button>
      </div>

      <div class="dc">
        <div class="hd2"><span style="color:var(--p)">{ico('layers', 30)}</span>
          <div class="tt"><small>دست مشتری می‌رسه</small><div class="who">{who}</div></div>
          <button class="chg" type="button" onclick="soon()">تغییر</button></div>
      </div>

      <div class="dc">
        <div class="hd2"><span class="ic bl">{ico('send', 30)}</span>
          <div class="tt"><b>پروکسی تلگرام توی صفحه اشتراک</b><small><i class="dt" id="tgDot"></i><span id="tgSub">به این مشتری نشون داده نمی‌شه</span></small></div></div>
        <div class="sg bl" id="tgSeg"><button type="button" class="on" data-k="def" onclick="segTg(this,'def')">پیش‌فرض</button>
          <button type="button" data-k="show" onclick="segTg(this,'show')">نشون بده</button>
          <button type="button" data-k="hide" onclick="segTg(this,'hide')">نشون نده</button></div>
      </div>

      <div class="dc">
        <div class="hd2"><span class="ic vi">{ico('user', 30)}</span>
          <div class="tt"><b>خریدار</b><small>هنوز به کسی داده نشده</small></div></div>
        <button class="db gr" type="button" style="width:100%;margin-top:18px" onclick="soon()">{ico('userplus', 26)}<span>تحویل به خریدار</span></button>
      </div>

      <div class="dc">
        <div class="ct"><h3>{ico('note', 26)}یادداشت <small style="color:var(--mut);font-weight:500;font-size:17px">مشتری نمی‌بینه</small></h3></div>
        <textarea id="note" maxlength="500" placeholder="مثلا شماره تماس یا روش پرداخت…">{esc(c.get('note') or '')}</textarea>
      </div>

      <div class="b2" style="margin-bottom:12px">
        <form method="post" action="{url_for('resume_config' if c.get('paused') else 'pause_config', config_id=c['config_id'])}" style="flex:1;display:flex">
          <button class="db gr" type="submit">{ico('play' if c.get('paused') else 'pause', 26)}<span>{'وصل دوباره' if c.get('paused') else 'قطع موقت'}</span></button></form>
        <form method="post" action="{url_for('rotate_config', config_id=c['config_id'])}" style="flex:1;display:flex"
              onsubmit="return confirm('لینک قبلی از کار می‌افته. لینک جدید ساخته بشه؟')">
          <button class="db gr" type="submit">{ico('refresh', 26)}<span>لینک جدید</span></button></form>
      </div>
      <form id="lblForm" method="post" action="{url_for('label_config', config_id=c['config_id'])}"><input type="hidden" name="label" id="lblIn"></form>
      <button class="db dg" type="button" onclick="askDelete(this)" data-action="{url_for('delete_config', config_id=c['config_id'])}"
              data-name="{esc(label)}" data-gb="{esc(fmt_gb(c.get('gb')))}">{ico('trash', 26)}<span>حذف مشتری</span></button>
    </div>

    <div id="ov" class="ov" onclick="closeAll()"></div>
    {srv_sheet}
    <div id="qrSheet" class="sheet"><div class="grab"></div>
      <div class="sh-head"><h2>QR لینک اشتراک</h2><button type="button" onclick="closeAll()" aria-label="بستن">{ico('x', 22)}</button></div>
      {qr_html}</div>
    <div id="delSheet" class="sheet"><div class="grab"></div>
      <div class="sh-head"><h2>حذف کانفیگ</h2><button type="button" onclick="closeAll()" aria-label="بستن">{ico('x', 22)}</button></div>
      <p style="margin:14px 4px 4px">کانفیگ «<b id="delName"></b>» حذف بشه؟</p>
      <p style="margin:0 4px 18px;color:var(--mut);font-size:14px">حجم این کانفیگ (<b id="delGb"></b> گیگ) بعد از حذف به اعتبارت اضافه می‌شه.</p>
      <form id="delForm" method="post" style="display:flex;gap:8px">
        <button class="btn danger" style="flex:1" type="submit">بله، حذف کن</button>
        <button class="btn soft" style="flex:1" type="button" onclick="closeAll()">انصراف</button>
      </form></div>
    <script>var ID={json.dumps(str(c['config_id']))};var LBL={json.dumps(label)};window.BAL={gb_bal};{DETAIL_JS.replace('__CUR__', first)}</script>
    """
    return build_page(label + ' | ' + BRAND, body)


@app.route('/configs/<config_id>/servers', methods=['POST'])
@login_required
def save_servers(config_id):
    bad = owned_or_redirect(config_id)
    if bad:
        return bad
    f = request.form
    keys, labs0, hid0 = f.getlist('skey'), f.getlist('slab0'), f.getlist('shid0')
    names, hide = f.getlist('sname'), set(f.getlist('shide'))
    changed, items = [], []
    for i, k in enumerate(keys):
        nm = (names[i] if i < len(names) else '').strip()[:40]
        hid = k in hide
        if nm != (labs0[i] if i < len(labs0) else nm) or hid != ((hid0[i] if i < len(hid0) else '0') == '1'):
            changed.append((k, nm, hid))
        items.append((k, nm, 1 if hid else 0))
    cr = creds()
    res = list(POOL.map(lambda t: api_call('PUT', f'/api/v1/configs/{config_id}/servers',
                                            json_body={'key': t[0], 'label': t[1], 'hidden': t[2]}, **cr), changed))
    invalidate()
    badr = [r for r in res if not r[0]]
    if badr:
        flash('⚠️ ' + api_msg(badr[0][2], 'ذخیره‌ی بعضی سرورها ناموفق بود.'), 'err')
    elif changed:
        flash('✅ اسم سرورها ذخیره شد.')
    if f.get('default'):
        if turso_on():
            if save_names('', items):
                flash('برای کانفیگ‌های جدید هم همین اسم‌ها اعمال می‌شه.')
            else:
                flash('⚠️ ذخیره تو Turso ناموفق بود؛ TURSO_DATABASE_URL و TURSO_AUTH_TOKEN رو چک کن.', 'err')
        else:
            flash('برای ذخیره‌ی پیش‌فرض باید TURSO_DATABASE_URL و TURSO_AUTH_TOKEN تنظیم بشه.', 'warn')
    return redirect(url_for('config_detail', config_id=config_id))


@app.route('/configs/<config_id>/renew', methods=['POST'])
@login_required
def renew_config(config_id):
    rid = session.get('rid')
    bad = owned_or_redirect(config_id)
    if bad:
        return bad
    back = redirect(url_for('config_detail', config_id=config_id))
    try:
        gb = float(request.form.get('gb') or 0)
        d = request.form.get('days', '')
        days = int(float(d)) if d != '' else None      # 0 = بی‌نهایت، عدد = از همین الان به بعد
    except ValueError:
        flash('عدد واردشده نامعتبره.', 'err')
        return back
    if gb <= 0 and days is None:
        flash('حجم یا مدت جدید رو مشخص کن.', 'warn')
        return back
    done = []
    if gb > 0:
        if not db_deduct(rid, gb):
            flash('⚠️ ' + err_text('insufficient_pool'), 'err')
            return back
        ok, status, data = api_call('POST', f'/api/v1/configs/{config_id}/extend', json_body={'gb': gb}, timeout=25)
        if ok:
            db_extend_cfg(rid, config_id, gb, 0)
            done.append(f'{fmt_gb(gb)} گیگ اضافه شد')
        else:
            db_refund(rid, gb)
            flash('⚠️ ' + api_msg(data, 'افزودن حجم ناموفق بود.'), 'err')
            return back
    if days is not None:
        body = {'noExpiry': True} if days == 0 else {'days': days}
        ok, status, data = api_call('PUT', f'/api/v1/configs/{config_id}/expiry', json_body=body, timeout=25)
        if ok:
            done.append('انقضا بی‌نهایت شد' if days == 0 else f'انقضا روی {days} روز از امروز تنظیم شد')
        else:
            flash('⚠️ ' + api_msg(data, 'تغییر انقضا ناموفق بود.'), 'err')
    invalidate()
    if done:
        flash('✅ ' + ' و '.join(done) + '.')
    return back


def do_action(config_id, method, path, body, ok_msg, fail_msg, on_ok=None):
    bad = owned_or_redirect(config_id)
    if bad:
        return bad
    ok, status, data = api_call(method, f'/api/v1/configs/{config_id}{path}', json_body=body, timeout=25)
    invalidate()
    if ok:
        if on_ok:
            on_ok(data)
        flash(ok_msg)
    else:
        flash('⚠️ ' + api_msg(data, fail_msg), 'err')
    return redirect(url_for('config_detail', config_id=config_id))


@app.route('/configs/<config_id>/pause', methods=['POST'])
@login_required
def pause_config(config_id):
    return do_action(config_id, 'POST', '/pause', None, '⏸ کانفیگ قطع شد.', 'قطع موقت ناموفق بود.')


@app.route('/configs/<config_id>/resume', methods=['POST'])
@login_required
def resume_config(config_id):
    return do_action(config_id, 'POST', '/resume', None, '▶️ کانفیگ دوباره وصل شد.', 'وصل دوباره ناموفق بود.')


@app.route('/configs/<config_id>/rotate', methods=['POST'])
@login_required
def rotate_config(config_id):
    bad = owned_or_redirect(config_id)
    if bad:
        return bad
    ok, status, data = api_call('POST', f'/api/v1/configs/{config_id}/rotate-link', timeout=25)
    invalidate()
    if not ok:
        flash('⚠️ ' + api_msg(data, 'تغییر لینک ناموفق بود.'), 'err')
        return redirect(url_for('config_detail', config_id=config_id))
    new_id = str(pick(data, 'id', default=config_id))
    new_sub = pick(data, 'subUrl', 'sub_url', default='')
    turso_exec([('UPDATE reseller_api_configs SET config_id=?, sub_url=? WHERE reseller_id=? AND config_id=?',
                 [new_id, new_sub, session.get('rid'), str(config_id)])])
    flash('🔗 لینک جدید ساخته شد؛ لینک قبلی دیگه کار نمی‌کنه.')
    return redirect(url_for('config_detail', config_id=new_id))


@app.route('/configs/<config_id>/note', methods=['POST'])
@login_required
def note_config(config_id):
    if not db_owns(session.get('rid'), config_id):
        return Response('{"ok":false}', 403, mimetype='application/json')
    ok, _s, data = api_call('PUT', f'/api/v1/configs/{config_id}/note', json_body={'note': request.form.get('note', '')[:500]})
    return Response(json.dumps({'ok': ok, 'message': '' if ok else api_msg(data, 'ذخیره نشد.')}), mimetype='application/json')


@app.route('/configs/<config_id>/tgproxy', methods=['POST'])
@login_required
def tgproxy_config(config_id):
    if not db_owns(session.get('rid'), config_id):
        return Response('{"ok":false}', 403, mimetype='application/json')
    v = {'show': True, 'hide': False}.get(request.form.get('v', 'def'))   # def → null (تنظیم فروشگاه)
    ok, _s, data = api_call('PUT', f'/api/v1/configs/{config_id}/tg-proxy', json_body={'visible': v})
    return Response(json.dumps({'ok': ok, 'message': '' if ok else api_msg(data, 'ذخیره نشد.')}), mimetype='application/json')


@app.route('/configs/<config_id>/label', methods=['POST'])
@login_required
def label_config(config_id):
    label = request.form.get('label', '').strip()[:60]
    if not label:
        flash('اسم خالیه.', 'err')
        return redirect(url_for('config_detail', config_id=config_id))
    return do_action(config_id, 'PUT', '/label', {'label': label}, '✅ اسم عوض شد.', 'تغییر اسم ناموفق بود.',
                     lambda _d: db_set_label(session.get('rid'), config_id, label))


@app.route('/debug/api')
@login_required
def debug_api():
    """عیب‌یابی: وضعیت استخر و تعداد کانفیگ‌های همین فروشنده + جواب خام یه کانفیگ از سرویس‌دهنده."""
    rid = session.get('rid')
    out = {'rid': rid, 'wallet': db_wallet(rid)[2]}
    ok, _s, cfgs = db_configs(rid)
    out['configs_in_db'] = len(cfgs) if ok else 'db error'
    if ok and cfgs:
        okp, st, data = api_call('GET', f"/api/v1/configs/{cfgs[0]['config_id']}")
        out['sample_live'] = {'ok': okp, 'status': st, 'data': data}
    txt = json.dumps(out, ensure_ascii=False, indent=2)
    txt = re.sub(r'(https?://[^"\s]{25})[^"\s]*', r'\1…', txt)
    return Response(txt, mimetype='text/plain; charset=utf-8')


# ---------------------------------------------------------------------------
# اعتبار
# ---------------------------------------------------------------------------
@app.route('/credits')
@login_required
def credits_page():
    bal, configs, resp = load_data()
    if resp:
        return resp
    price = int(bal.get('price_per_gb', 0) or 0)
    gb_bal = float(bal.get('gb_balance', 0) or 0)
    in_cfg = sum(float(c.get('gb') or 0) for c in configs)
    content = f"""
    {credit_card(bal, len(configs))}
    <div class="card">
      <div class="row-item"><span class="ib" style="background:var(--p-soft);color:var(--p)">{ico('drop')}</span>
        <div><b>حجم داخل کانفیگ‌های فعال</b><small>با حذف هر کانفیگ به اعتبارت برمی‌گرده</small></div>
        <span class="n" style="font-size:20px;direction:ltr">{in_cfg:g} GB</span></div>
      <div class="row-item"><span class="ib" style="background:var(--ok-soft);color:var(--ok)">{ico('wallet')}</span>
        <div><b>ارزش اعتبار فعلی</b><small>با قیمت هر گیگ {price:,} تومان</small></div>
        <span class="n" style="font-size:20px">{int(gb_bal * price):,} <small style="font-size:12px">تومان</small></span></div>
    </div>
    """
    return render_page('اعتبارها', content, bal, 'credit')


if __name__ == '__main__':
    port = int(os.getenv('PORT') or os.getenv('RESELLER_PANEL_PORT', '8089'))
    print(f'🧑\u200d💼 پنل فروشندگی روی پورت {port} بالا اومد.')
    app.run(host='0.0.0.0', port=port, threaded=True)
