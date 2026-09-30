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
import math
import time
import html as html_lib
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from flask import Flask, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key-in-production")
try:  # اگه نصب باشه، خروجی HTML فشرده می‌شه (اختیاری)
    from flask_compress import Compress
    Compress(app)
except Exception:
    pass

API_BASE = os.getenv("DEFAULT_API_BASE", "https://web-production-2f5065.up.railway.app").strip().rstrip("/")
BRAND = os.getenv("PANEL_BRAND", "SKY TUNNEL")
FONT_CSS_URL = os.getenv("PANEL_FONT_CSS", "").strip()      # اختیاری: آدرس CSS فونت (مثلاً فونت خودت)
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
    return {'api_base': session.get('api_base', ''), 'api_key': session.get('api_key', ''),
            'api_secret': session.get('api_secret', '')}


def api_call(method, path, api_base=None, api_key=None, api_secret=None, json_body=None, timeout=12):
    if api_base is None:
        api_base = session.get('api_base', '')
    if api_key is None:
        api_key = session.get('api_key', '')
    if api_secret is None:
        api_secret = session.get('api_secret', '')
    headers = {'X-Api-Key': api_key}
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
        if not session.get('api_key'):
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


def flash(msg, kind='ok'):
    session.setdefault('_flash', []).append([kind, msg])
    session.modified = True


def expired_session(status):
    if status in (401, 403):
        session.clear()
        flash('نشست شما منقضی شده یا کلید تغییر کرده؛ دوباره وارد شوید.', 'err')
        return True
    return False


# ---------------------------------------------------------------------------
# داده‌ها
# ---------------------------------------------------------------------------
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
    info = {'created': created, 'days': days, 'expiry': None, 'left': None, 'pct': 0, 'state': 'ok'}
    if created and days:
        expiry = created + timedelta(days=days)
        left = math.ceil((expiry - now).total_seconds() / 86400)
        info['expiry'] = expiry
        info['left'] = left
        elapsed = (now - created).total_seconds() / (days * 86400)
        info['pct'] = max(0, min(100, int(elapsed * 100)))
        info['state'] = 'expired' if left <= 0 else ('soon' if left <= 3 else 'ok')
    return info


def load_data(force=False):
    """(bal, configs, redirect_response). balance و configs موازی گرفته می‌شن و ۱۵ ثانیه کش می‌شن."""
    key = session.get('api_key', '')
    hit = CACHE.get(key)
    if hit and not force and time.time() - hit[0] < CACHE_TTL:
        return hit[1], hit[2], None
    cr = creds()
    f_bal = POOL.submit(api_call, 'GET', '/api/v1/balance', **cr)
    f_cfg = POOL.submit(api_call, 'GET', '/api/v1/configs', **cr)
    ok, status, bal = f_bal.result()
    ok2, _st, cfgs = f_cfg.result()
    if not ok:
        if expired_session(status):
            return None, None, redirect(url_for('login'))
        return None, None, render_page('', f'<div class="card"><p>{esc(err_text(bal.get("error"), "خطا در دریافت اطلاعات."))}</p></div>', None)
    configs = [c for c in (cfgs.get('configs', []) if ok2 else []) if c.get('active')]
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
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<div id="pbar"></div>
__BODY__
<script>
var CHECK='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7"/></svg>';
function openEl(id){document.getElementById(id).classList.add('show');document.getElementById('ov').classList.add('show')}
function closeAll(){document.querySelectorAll('.sheet,.drawer').forEach(function(e){e.classList.remove('show')});document.getElementById('ov').classList.remove('show')}
function copyText(btn,txt){
  function done(){var o=btn.innerHTML;btn.innerHTML=btn.classList.contains('rb')?CHECK:'کپی شد ✓';setTimeout(function(){btn.innerHTML=o},1400)}
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(txt).then(done)}
  else{var t=document.createElement('textarea');t.value=txt;document.body.appendChild(t);t.select();try{document.execCommand('copy');done()}catch(e){}document.body.removeChild(t)}
}
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
    btn.disabled=!(gv>0&&dv>0&&rest>=-1e-9);
  };
  document.getElementById('newForm').addEventListener('submit',function(){btn.disabled=true;btn.lastChild.textContent=' در حال ساخت…'});
  [g,dy].forEach(function(i){i.addEventListener('input',calc)});
  calc();
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


def new_sheet(bal):
    """شیت «مشتری جدید» (ساخت کانفیگ) — مثل عکس‌ها."""
    gb_bal = float(bal.get('gb_balance', 0) or 0)
    types = ''.join(
        f'<label><input type="radio" name="type" value="{k}" {"checked" if i == 0 else ""}>'
        f'<span class="tc"><i class="t-{m[2]}">{ico(m[1], 22)}</i>{esc(m[0])}</span></label>'
        for i, (k, m) in enumerate(TYPE_META.items()))
    gb_chips = ''.join(f'<button type="button" data-v="{v}" onclick="setF(\'nGb\',{v})">{v}</button>' for v in (1, 5, 10, 20, 50, 100))
    day_chips = ''.join(f'<button type="button" data-v="{v}" onclick="setF(\'nDays\',{v})">{v}</button>' for v in (7, 30, 60, 90))
    cnt_chips = ''.join(f'<button type="button" onclick="setC({v})">{v}</button>' for v in (5, 10, 20))
    return f"""
    <div id="newSheet" class="sheet tall">
      <div class="grab"></div>
      <div class="sh-head"><h2>مشتری جدید</h2><button type="button" onclick="closeAll()" aria-label="بستن">{ico('x', 22)}</button></div>
      <form id="newForm" method="post" action="{url_for('create_config')}" autocomplete="off">
        <div class="sc">
          <p class="hint">نوع رو انتخاب کن</p>
          <div class="tcards">{types}</div>

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
      <script>window.BAL={gb_bal};</script>
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
        elif not API_BASE.startswith(('http://', 'https://')) or 'xxxx' in API_BASE:
            error = 'پنل هنوز تنظیم نشده؛ به ادمین اطلاع بده.'
        else:
            ok, status, data = api_call('GET', '/api/v1/balance', api_base=API_BASE, api_key=api_key, api_secret='')
            if ok:
                session['api_base'] = API_BASE
                session['api_key'] = api_key
                session.pop('api_secret', None)
                return redirect(url_for('dashboard'))
            elif status == 0:
                error = 'اتصال به سرور برقرار نشد؛ کمی بعد دوباره تلاش کن.'
            else:
                error = err_text(data.get('error'), 'کلید API درست نیست.')
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
                f'data-t="{esc(sub)}">{ico("copy", 19)}</button>') if sub else ''
    return f"""
    <div class="cfg" data-s="{hay}" data-st="{st}">
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
    ctype = request.form.get('type', 'both')
    if ctype not in TYPE_LABELS:
        ctype = 'both'
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
    cr = creds()

    def one(i):
        body = {'gb': gb, 'days': days, 'type': ctype}
        if label:
            body['label'] = f'{label}-{i + 1}' if count > 1 else label
        return api_call('POST', '/api/v1/configs', json_body=body, timeout=25, **cr)

    if count == 1:
        results = [one(0)]
    else:
        with ThreadPoolExecutor(max_workers=max(1, CREATE_WORKERS)) as ex:
            results = list(ex.map(one, range(count)))
    invalidate()

    if any(expired_session(st) for ok, st, _d in results if not ok):
        return redirect(url_for('login'))
    oks = [d for ok, _s, d in results if ok]
    bads = [d for ok, _s, d in results if not ok]
    if oks:
        rem = [float(d.get('gb_balance_remaining')) for d in oks if d.get('gb_balance_remaining') is not None]
        rem_txt = f" اعتبار باقی‌مانده: {fmt_gb(min(rem))} گیگ." if rem else ''
        if count == 1:
            flash(f"✅ کانفیگ «{oks[0].get('label')}» ساخته شد.{rem_txt}")
        else:
            flash(f"✅ {len(oks)} کانفیگ ساخته شد.{rem_txt}")
    if bads:
        d0 = bads[0]
        msg = err_text(d0.get('error'), d0.get('message', 'خطا در ساخت کانفیگ.'))
        flash(('⚠️ ' + msg) if not oks else f'⚠️ {len(bads)} مورد ساخته نشد: {msg}', 'err' if not oks else 'warn')
    return redirect(url_for('configs_page'))


# ---------------------------------------------------------------------------
# حذف کانفیگ + برگشت حجم
# ---------------------------------------------------------------------------
@app.route('/configs/<config_id>/delete', methods=['POST'])
@login_required
def delete_config(config_id):
    key = session.get('api_key', '')
    hit = CACHE.get(key)
    before_bal = hit[1] if hit else None
    if before_bal is None:  # کش نبود: یک بار موجودی قبل رو می‌گیریم
        _okb, _stb, before_bal = api_call('GET', '/api/v1/balance')
    ok, status, data = api_call('DELETE', f'/api/v1/configs/{config_id}')
    invalidate()
    if ok:
        after_val = data.get('gb_balance')
        if after_val is None:
            after_val = data.get('gb_balance_remaining')
        if after_val is None:
            _oka, _sta, after = api_call('GET', '/api/v1/balance')
            after_val = after.get('gb_balance', 0)
        try:
            diff = float(after_val) - float(before_bal.get('gb_balance', 0))
        except Exception:
            diff = 0
        if diff > 0.0005:
            flash(f'کانفیگ حذف شد و {fmt_gb(diff)} گیگ به اعتبارت برگشت.')
        else:
            flash('کانفیگ حذف شد، ولی حجمش به اعتبار برنگشت. reseller_api.py باید موقع حذف، '
                  'حجم رو به gb_balance اضافه کنه.', 'warn')
    elif expired_session(status):
        return redirect(url_for('login'))
    elif data.get('error') == 'not_found':
        flash('این کانفیگ قبلاً حذف شده.', 'warn')
    else:
        flash('⚠️ ' + err_text(data.get('error'), 'حذف ناموفق بود.'), 'err')
    return redirect(url_for('configs_page'))


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
