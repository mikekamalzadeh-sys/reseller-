# -*- coding: utf-8 -*-
"""
reseller_panel.py  (نسخه‌ی جدید — ظاهر شبیه پنل seller.vaslpro.com)
====================================================================
پنل وب مستقل فروشنده‌ها. مثل قبل فقط با HTTP به reseller_api.py حرف می‌زنه
(GET /api/v1/balance ، GET/POST /api/v1/configs ، DELETE /api/v1/configs/<id>).

تغییرات این نسخه:
  • ظاهر کامل موبایل‌محور بنفش: پیشخوان، کانفیگ‌ها، اعتبار، منوی «همه»
  • کانفیگی که حذف می‌شه از لیست «کانفیگ‌های ساخته‌شده» کامل محو می‌شه
    (فقط کانفیگ‌های active نمایش داده می‌شن)
  • بعد از حذف، موجودی قبل و بعد چک می‌شه و به کاربر نشون می‌دیم چند گیگ
    به اعتبارش برگشته. اگه API چیزی برنگردونده باشه، هشدار می‌ده.
    (خودِ برگشت حجم باید تو reseller_api.py انجام بشه.)

اجرا:
    pip install flask requests
    export SECRET_KEY="یه رشته‌ی تصادفی طولانی"
    export DEFAULT_API_BASE="https://your-reseller-api.up.railway.app"
    python3 reseller_panel.py
"""

import os
import math
import html as html_lib
from datetime import datetime, timedelta

import requests
from flask import Flask, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key-in-production")
API_BASE = os.getenv("DEFAULT_API_BASE", "https://web-production-2f5065.up.railway.app").strip().rstrip("/")
BRAND = os.getenv("PANEL_BRAND", "SKY TUNNEL")

TYPE_LABELS = {
    'config': '🛡 کانفیگ',
    'wireguard': '🔒 وایرگارد',
    'both': '🧩 کانفیگ + وایرگارد',
    'openvpn': '📱 اوپن‌وی‌پی‌ان',
    'dns': '🎮 DNS بازی',
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
# ارتباط با reseller_api.py
# ---------------------------------------------------------------------------
def api_call(method, path, api_base=None, api_key=None, api_secret=None, json_body=None, timeout=15):
    api_base = (api_base or session.get('api_base', '')).rstrip('/')
    api_key = api_key or session.get('api_key', '')
    api_secret = api_secret or session.get('api_secret', '')
    headers = {'X-Api-Key': api_key}
    if api_secret:
        headers['X-Api-Secret'] = api_secret
    try:
        res = requests.request(method, api_base + path, headers=headers, json=json_body, timeout=timeout)
        try:
            data = res.json()
        except Exception:
            data = {'success': False, 'error': 'bad_response'}
        return res.status_code < 400, res.status_code, data
    except requests.exceptions.RequestException:
        return False, 0, {'success': False, 'error': 'connection_failed',
                           'message': 'اتصال به آدرس API برقرار نشد.'}


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
# داده‌ها: فقط کانفیگ‌های فعال (حذف‌شده‌ها کلاً از لیست محو می‌شن)
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


def load_data():
    """(bal, configs, redirect_response). configs فقط active و جدیدترین اول."""
    ok, status, bal = api_call('GET', '/api/v1/balance')
    if not ok:
        if expired_session(status):
            return None, None, redirect(url_for('login'))
        return None, None, render_page('', f'<div class="card"><p>{esc(err_text(bal.get("error"), "خطا در دریافت اطلاعات."))}</p></div>', None)
    ok2, _st, cfgs = api_call('GET', '/api/v1/configs')
    configs = [c for c in (cfgs.get('configs', []) if ok2 else []) if c.get('active')]
    configs.sort(key=lambda c: str(c.get('created_at', '')), reverse=True)
    return bal, configs, None


# ---------------------------------------------------------------------------
# آیکون‌ها
# ---------------------------------------------------------------------------
def ico(name, size=22):
    paths = {
        'grid': '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
        'users': '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
        'wallet': '<path d="M20 12V8a2 2 0 0 0-2-2H5a2 2 0 0 1 0-4h13"/><path d="M3 5v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-4"/><path d="M17 14h4v-4h-4a2 2 0 0 0 0 4z"/>',
        'plus': '<path d="M12 5v14M5 12h14"/>',
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
        'key': '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.3-9.3M17 6l3 3M14 9l2 2"/>',
        'eye': '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
        'chev': '<path d="m15 18-6-6 6-6"/>',
    }
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths[name]}</svg>')


# ---------------------------------------------------------------------------
# قالب صفحه (بدون Jinja؛ فقط جایگزینی رشته)
# ---------------------------------------------------------------------------
BASE_HTML = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#f7f6fd">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--p:#6a3df0;--p2:#4a26b5;--p-soft:#ece8fb;--p-ink:#3b1fa3;--bg:#f7f6fd;--card:#fff;--ink:#14112b;--mut:#7d7a94;
--line:#ebe9f5;--ok:#0f7a4d;--ok-soft:#d9f2e4;--bad:#c4302b;--bad-soft:#fde8e7;--warn:#b45f06;--warn-soft:#fff1dc;}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0}
body{font-family:'Vazirmatn',Tahoma,sans-serif;background:var(--bg);color:var(--ink);font-size:15px;line-height:1.7;
padding-bottom:calc(92px + env(safe-area-inset-bottom,0px))}
a{color:inherit;text-decoration:none}
.wrap{max-width:560px;margin:0 auto;padding:0 16px}
/* header */
.hdr{display:flex;align-items:center;gap:10px;padding:calc(12px + env(safe-area-inset-top,0px)) 0 8px}
.avatar{width:48px;height:48px;border-radius:50%;background:#fff;border:1px solid var(--line);display:grid;place-items:center;color:var(--p);flex:none}
.chip{background:#fff;border-radius:999px;padding:0 16px;height:48px;display:flex;align-items:center;gap:8px;font-weight:700;border:1px solid var(--line);flex:none;direction:ltr}
.chip svg{color:var(--p)}
.search{flex:1;min-width:0;background:#fff;border-radius:999px;height:48px;display:flex;align-items:center;gap:8px;padding:0 16px;border:1px solid var(--line)}
.search input{border:0;outline:0;background:transparent;width:100%;font:inherit;font-size:14px;color:var(--ink)}
.search svg{color:var(--mut);flex:none}
.round{width:48px;height:48px;border-radius:50%;background:#fff;border:1px solid var(--line);display:grid;place-items:center;flex:none;cursor:pointer;color:var(--ink);padding:0}
/* titles */
.ptitle{display:flex;align-items:flex-end;justify-content:space-between;gap:10px;margin:14px 0 14px}
.ptitle h1{margin:0;font-size:32px;line-height:1.2;font-weight:800}
.ptitle small{display:block;color:var(--mut);font-size:15px;font-weight:500;margin-top:2px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;height:54px;padding:0 22px;border-radius:999px;font:inherit;font-weight:700;font-size:16px;border:0;cursor:pointer;background:var(--p);color:#fff;box-shadow:0 10px 22px -8px rgba(106,61,240,.65)}
.btn.soft{background:#fff;color:var(--p-ink);box-shadow:none;border:1px solid var(--line)}
.btn.full{width:100%}
.btn.danger{background:var(--bad);box-shadow:none}
.btn.sm{height:38px;padding:0 14px;font-size:13.5px;box-shadow:none}
.btn.sm.soft{background:var(--p-soft);border:0}
.btn.sm.del{background:var(--bad-soft);color:var(--bad);border:0}
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
.credit .btn{height:42px;font-size:14px;background:#fff;color:var(--p-ink);box-shadow:none;padding:0 18px}
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
/* config cards */
.filters{display:flex;gap:8px;overflow-x:auto;margin:4px 0 14px;padding-bottom:2px}
.f{flex:none;border:1px solid var(--line);background:#fff;border-radius:999px;padding:7px 16px;font:inherit;font-size:13.5px;font-weight:600;color:var(--mut);cursor:pointer}
.f.on{background:var(--p);border-color:var(--p);color:#fff}
.cfg{background:#fff;border:1px solid var(--line);border-radius:26px;padding:16px 18px;margin-bottom:12px}
.cfg .top{display:flex;justify-content:space-between;align-items:center;gap:8px}
.cfg .top b{font-size:16.5px;word-break:break-word}
.badge{padding:3px 12px;border-radius:999px;font-size:12px;font-weight:700;flex:none}
.badge.ok{background:var(--ok-soft);color:var(--ok)}
.badge.soon{background:var(--warn-soft);color:var(--warn)}
.badge.expired{background:var(--bad-soft);color:var(--bad)}
.meta{color:var(--mut);font-size:13px;margin:4px 0 10px}
.meta span{white-space:nowrap}
.track{height:8px;background:#eceaf6;border-radius:999px;overflow:hidden}
.track s{display:block;height:100%;background:var(--p);border-radius:999px}
.track.soon s{background:#e8912d}.track.expired s{background:var(--bad)}
.tl{display:flex;justify-content:space-between;font-size:12px;color:var(--mut);margin:5px 0 10px}
.link{background:#f5f3fc;border-radius:14px;padding:9px 12px;font-size:11.5px;direction:ltr;text-align:left;word-break:break-all;color:#4a4566;margin-bottom:10px;font-family:ui-monospace,Menlo,Consolas,monospace}
.acts{display:flex;gap:8px}
.acts .btn{flex:1}
.empty{text-align:center;color:var(--mut);padding:36px 10px}
/* form */
label.l{display:block;margin:16px 0 8px;font-weight:600;font-size:14px}
.inp{width:100%;height:52px;border-radius:18px;border:1.5px solid var(--line);background:#fff;padding:0 16px;font:inherit;font-size:16px;color:var(--ink)}
.inp:focus{outline:none;border-color:var(--p);box-shadow:0 0 0 4px rgba(106,61,240,.12)}
.types{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.types label{cursor:pointer}
.types input{position:absolute;opacity:0;pointer-events:none}
.types span{display:block;text-align:center;padding:12px 8px;border:1.5px solid var(--line);border-radius:18px;font-size:13.5px;font-weight:600;background:#fff}
.types input:checked+span{border-color:var(--p);background:var(--p-soft);color:var(--p-ink)}
.presets{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.presets button{border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 14px;font:inherit;font-size:13px;cursor:pointer;color:var(--p-ink);font-weight:600}
.after{margin-top:14px;padding:12px 16px;background:var(--p-soft);border-radius:18px;color:var(--p-ink);font-size:13.5px}
.after.bad{background:var(--bad-soft);color:var(--bad)}
/* flash */
.flash{border-radius:18px;padding:12px 16px;margin:6px 0 8px;font-weight:600;font-size:14px}
.flash.ok{background:var(--ok-soft);color:var(--ok)}
.flash.err{background:var(--bad-soft);color:var(--bad)}
.flash.warn{background:var(--warn-soft);color:var(--warn)}
/* bottom nav */
.nav{position:fixed;inset-inline:0;bottom:0;background:#fff;border-top:1px solid var(--line);padding-bottom:env(safe-area-inset-bottom,0px);z-index:30}
.nav .in{max-width:560px;margin:0 auto;display:flex}
.nav a,.nav button{flex:1;position:relative;display:flex;flex-direction:column;align-items:center;gap:2px;padding:12px 4px 10px;color:var(--mut);font:inherit;font-size:13px;font-weight:600;background:none;border:0;cursor:pointer}
.nav .on{color:var(--p)}
.nav .on::before{content:'';position:absolute;top:0;width:34px;height:3px;border-radius:0 0 4px 4px;background:var(--p)}
/* sheets */
.ov{position:fixed;inset:0;background:rgba(20,17,43,.45);opacity:0;pointer-events:none;transition:opacity .2s;z-index:40}
.ov.show{opacity:1;pointer-events:auto}
.sheet{position:fixed;inset-inline:0;bottom:0;background:#fff;border-radius:32px 32px 0 0;padding:10px 18px calc(22px + env(safe-area-inset-bottom,0px));max-height:88vh;overflow:auto;transform:translateY(105%);transition:transform .25s;z-index:50;max-width:560px;margin:0 auto}
.sheet.show{transform:none}
.grab{width:54px;height:5px;border-radius:9px;background:#d8d5e6;margin:0 auto 10px}
.sheet h2{margin:0;font-size:20px}
.sh-head{display:flex;justify-content:space-between;align-items:center;padding:6px 4px 12px;border-bottom:1px solid var(--line);margin-bottom:8px}
.sh-head button{background:none;border:0;cursor:pointer;color:var(--ink)}
.sec{color:var(--mut);font-size:13px;margin:16px 6px 6px}
.mi{display:flex;align-items:center;gap:14px;padding:13px 14px;border-radius:999px;color:var(--p-ink);font-weight:600;font-size:16px}
.mi.on{background:var(--p-soft)}
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
__BODY__
<script>
function openEl(id){document.getElementById(id).classList.add('show');document.getElementById('ov').classList.add('show')}
function closeAll(){document.querySelectorAll('.sheet,.drawer').forEach(function(e){e.classList.remove('show')});document.getElementById('ov').classList.remove('show')}
function copyText(btn,txt){
  function done(){var o=btn.innerHTML;btn.textContent='کپی شد ✓';setTimeout(function(){btn.innerHTML=o},1400)}
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
</script>
</body>
</html>
"""


def menu_items(active):
    def item(key, href, icon, label):
        return f'<a class="mi {"on" if active == key else ""}" href="{href}">{ico(icon)}<span>{label}</span></a>'
    return (
        '<div class="sec">هر روز</div>'
        + item('dash', url_for('dashboard'), 'grid', 'پیشخوان')
        + item('cfgs', url_for('configs_page'), 'users', 'کانفیگ‌ها')
        + '<div class="sec">فروشگاه</div>'
        + item('new', url_for('new_config'), 'plus', 'ساخت کانفیگ')
        + '<div class="sec">مالی</div>'
        + item('credit', url_for('credits_page'), 'wallet', 'اعتبار')
        + '<div class="sec">حساب</div>'
        + item('out', url_for('logout'), 'out', 'خروج')
    )


def render_page(title, content_html, bal, active='dash', q=''):
    flashes = ''.join(f'<div class="flash {esc(k)}">{esc(m)}</div>' for k, m in session.pop('_flash', []))
    credit_chip = f'{int(bal.get("gb_balance", 0))} GB' if bal else '— GB'
    nav = lambda key, href, icon, label: (
        f'<a href="{href}" class="{"on" if active == key else ""}">{ico(icon, 24)}<span>{label}</span></a>')
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
      {('<div class="ptitle"><div><h1>' + esc(title) + '</h1><small id="todayFa"></small></div></div>') if title else ''}
      {flashes}
      {content_html}
    </div>

    <nav class="nav"><div class="in">
      {nav('dash', url_for('dashboard'), 'grid', 'پیشخوان')}
      {nav('cfgs', url_for('configs_page'), 'users', 'کانفیگ‌ها')}
      {nav('credit', url_for('credits_page'), 'wallet', 'اعتبار')}
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
      <form id="delForm" method="post" class="acts">
        <button class="btn danger" type="submit">بله، حذف کن</button>
        <button class="btn soft" type="button" onclick="closeAll()">انصراف</button>
      </form>
    </div>
    """
    page = BASE_HTML.replace('__TITLE__', esc((title or 'پنل فروشندگی') + ' | ' + BRAND)).replace('__BODY__', body)
    return page


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
    return BASE_HTML.replace('__TITLE__', 'ورود | ' + esc(BRAND)).replace('__BODY__', body)


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
            ok, status, data = api_call('GET', '/api/v1/balance', api_base=API_BASE, api_key=api_key)
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
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# اجزای مشترک
# ---------------------------------------------------------------------------
def credit_card(bal, n_active, with_charge_btn=False):
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


def cfg_card(c):
    info = cfg_info(c)
    st = info['state']
    badge = {'ok': '<span class="badge ok">فعال</span>',
             'soon': '<span class="badge soon">رو به انقضا</span>',
             'expired': '<span class="badge expired">منقضی</span>'}[st]
    if info['left'] is None:
        time_txt, exp_s = '', '-'
    else:
        time_txt = 'منقضی شده' if info['left'] <= 0 else f"{info['left']} روز مانده"
        exp_s = info['expiry'].strftime('%Y-%m-%d')
    created_s = info['created'].strftime('%Y-%m-%d') if info['created'] else '-'
    label = c.get('label') or c.get('config_id')
    typ = TYPE_LABELS.get(c.get('type'), c.get('type') or '')
    sub = c.get('sub_url') or ''
    hay = esc(f"{label} {c.get('type', '')} {c.get('config_id', '')}".lower())
    copy_btn = (f'<button class="btn sm soft" type="button" onclick="copyText(this, this.dataset.t)" '
                f'data-t="{esc(sub)}">{ico("copy", 16)} کپی لینک</button>') if sub else ''
    return f"""
    <div class="cfg" data-s="{hay}" data-st="{st}">
      <div class="top"><b>{esc(label)}</b>{badge}</div>
      <div class="meta"><span>{esc(typ)}</span> • <span>{esc(fmt_gb(c.get('gb')))} گیگ</span> • <span>{esc(c.get('days'))} روز</span></div>
      <div class="track {st}"><s style="width:{info['pct']}%"></s></div>
      <div class="tl"><span>ساخت: {created_s}</span><span>{esc(time_txt)}</span><span>انقضا: {exp_s}</span></div>
      {('<div class="link">' + esc(sub) + '</div>') if sub else ''}
      <div class="acts">
        {copy_btn}
        <button class="btn sm del" type="button" onclick="askDelete(this)"
                data-action="{url_for('delete_config', config_id=c['config_id'])}"
                data-name="{esc(label)}" data-gb="{esc(fmt_gb(c.get('gb')))}">{ico('trash', 16)} حذف</button>
      </div>
    </div>"""


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

    # فروش ۷ روز اخیر (فقط کانفیگ‌های فعال؛ حذف‌شده‌ها حساب نمی‌شن)
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
    for i in range(7):  # اولین = امروز (سمت راست در RTL)
        d = now - timedelta(days=i)
        h = 0 if per_day[i] == 0 else max(10, int(per_day[i] / mx * 100))
        bars += (f'<div class="bar {"today" if i == 0 else ""}"><i><s style="height:{h}%"></s></i>'
                 f'{PERSIAN_DAY[d.weekday()]}</div>')
    week_toman = int(week_gb * price)
    sales_txt = f'<b>{week_toman // 1000:,}</b>هزار تومان' if week_toman >= 1000 else f'<b>{week_toman:,}</b>تومان'

    soon = [c for c in configs if cfg_info(c)['state'] == 'soon']
    expired = [c for c in configs if cfg_info(c)['state'] == 'expired']
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
      <a class="btn" style="flex:1.3" href="{url_for('new_config')}">{ico('plus', 20)} ساخت کانفیگ</a>
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
# کانفیگ‌ها (فقط فعال‌ها)
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
    cards = ''.join(cfg_card(c) for c in configs)
    if not cards:
        cards = f'<div class="empty">هنوز کانفیگی نساختی.<br><br><a class="btn" href="{url_for("new_config")}">{ico("plus", 20)} ساخت کانفیگ</a></div>'
    filt = ''.join(
        f'<button type="button" class="f {"on" if k == f else ""}" data-f="{k}">{lbl}</button>'
        for k, lbl in (('all', f'همه ({len(configs)})'), ('soon', 'رو به انقضا'), ('expired', 'منقضی')))
    content = f"""
    <div class="filters" id="filters">{filt}</div>
    <div id="list">{cards}</div>
    <div id="none" class="empty" style="display:none">چیزی پیدا نشد.</div>
    <script>
    (function(){{
      var q=document.querySelector('.search input'), cur={f!r}, list=document.querySelectorAll('#list .cfg');
      function apply(){{
        var t=(q.value||'').trim().toLowerCase(), n=0;
        list.forEach(function(el){{
          var ok=(cur==='all'||el.dataset.st===cur)&&(!t||el.dataset.s.indexOf(t)>-1);
          el.style.display=ok?'':'none'; if(ok)n++;
        }});
        document.getElementById('none').style.display=(list.length&&!n)?'':'none';
      }}
      document.querySelectorAll('#filters .f').forEach(function(b){{
        b.onclick=function(){{document.querySelectorAll('#filters .f').forEach(function(x){{x.classList.remove('on')}});b.classList.add('on');cur=b.dataset.f;apply()}};
      }});
      q.addEventListener('input',apply);
      var form=q.closest('form'); form.addEventListener('submit',function(e){{e.preventDefault();apply()}});
      apply();
    }})();
    </script>
    """
    return render_page('کانفیگ‌ها', content, bal, 'cfgs', q=q)


# ---------------------------------------------------------------------------
# ساخت کانفیگ
# ---------------------------------------------------------------------------
@app.route('/configs/new')
@login_required
def new_config():
    bal, configs, resp = load_data()
    if resp:
        return resp
    types = ''.join(
        f'<label><input type="radio" name="type" value="{k}" {"checked" if i == 0 else ""}><span>{esc(v)}</span></label>'
        for i, (k, v) in enumerate(TYPE_LABELS.items()))
    gb_bal = float(bal.get('gb_balance', 0) or 0)
    content = f"""
    <form class="card" method="post" action="{url_for('create_config')}" style="padding:20px 20px 24px">
      <label class="l" style="margin-top:0">نوع کانفیگ</label>
      <div class="types">{types}</div>

      <label class="l" for="gb">حجم (گیگابایت)</label>
      <input class="inp" id="gb" type="number" name="gb" step="0.1" min="0.1" inputmode="decimal" required>
      <div class="presets" data-t="gb">{''.join(f'<button type="button" data-v="{v}">{v} GB</button>' for v in (5, 10, 20, 50, 100))}</div>

      <label class="l" for="days">مدت اعتبار (روز)</label>
      <input class="inp" id="days" type="number" name="days" min="1" inputmode="numeric" required>
      <div class="presets" data-t="days">{''.join(f'<button type="button" data-v="{v}">{v} روز</button>' for v in (7, 30, 60, 90))}</div>

      <label class="l" for="label">نام مشتری (اختیاری)</label>
      <input class="inp" id="label" type="text" name="label" placeholder="مثلاً: احمدی">

      <div class="after" id="after">اعتبار فعلی: {fmt_gb(gb_bal)} گیگ</div>
      <button class="btn full" type="submit" style="margin-top:18px">ساخت کانفیگ</button>
    </form>
    <script>
    (function(){{
      var bal={gb_bal}, gb=document.getElementById('gb'), box=document.getElementById('after');
      document.querySelectorAll('.presets').forEach(function(p){{
        p.querySelectorAll('button').forEach(function(b){{
          b.onclick=function(){{document.getElementById(p.dataset.t).value=b.dataset.v;upd()}};
        }});
      }});
      function upd(){{
        var v=parseFloat(gb.value)||0, rest=bal-v;
        if(v<=0){{box.className='after';box.textContent='اعتبار فعلی: '+bal.toFixed(3).replace(/\\.?0+$/,'')+' گیگ';return}}
        if(rest<0){{box.className='after bad';box.textContent='اعتبارت کافی نیست؛ '+(-rest).toFixed(2).replace(/\\.?0+$/,'')+' گیگ کمه.'}}
        else{{box.className='after';box.textContent='بعد از ساخت، '+rest.toFixed(3).replace(/\\.?0+$/,'')+' گیگ اعتبار می‌مونه.'}}
      }}
      gb.addEventListener('input',upd);
    }})();
    </script>
    """
    return render_page('ساخت کانفیگ', content, bal, 'new')


@app.route('/configs/create', methods=['POST'])
@login_required
def create_config():
    ctype = request.form.get('type', 'both')
    try:
        gb = float(request.form.get('gb', ''))
        days = int(request.form.get('days', ''))
    except ValueError:
        flash('حجم و مدت باید عدد باشن.', 'err')
        return redirect(url_for('new_config'))
    label = request.form.get('label', '').strip() or None
    body = {'gb': gb, 'days': days, 'type': ctype}
    if label:
        body['label'] = label
    ok, status, data = api_call('POST', '/api/v1/configs', json_body=body)
    if ok:
        flash(f"✅ کانفیگ «{data.get('label')}» ساخته شد. اعتبار باقی‌مانده: {fmt_gb(data.get('gb_balance_remaining'))} گیگ.")
        return redirect(url_for('configs_page'))
    if expired_session(status):
        return redirect(url_for('login'))
    flash('⚠️ ' + err_text(data.get('error'), data.get('message', 'خطا در ساخت کانفیگ.')), 'err')
    return redirect(url_for('new_config'))


# ---------------------------------------------------------------------------
# حذف کانفیگ + برگشت حجم به اعتبار
# ---------------------------------------------------------------------------
@app.route('/configs/<config_id>/delete', methods=['POST'])
@login_required
def delete_config(config_id):
    _okb, _stb, before = api_call('GET', '/api/v1/balance')
    ok, status, data = api_call('DELETE', f'/api/v1/configs/{config_id}')
    if ok:
        _oka, _sta, after = api_call('GET', '/api/v1/balance')
        try:
            diff = float(after.get('gb_balance', 0)) - float(before.get('gb_balance', 0))
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
    return render_page('اعتبار', content, bal, 'credit')


if __name__ == '__main__':
    port = int(os.getenv('PORT') or os.getenv('RESELLER_PANEL_PORT', '8089'))
    print(f'🧑\u200d💼 پنل فروشندگی روی پورت {port} بالا اومد.')
    app.run(host='0.0.0.0', port=port, threaded=True)
