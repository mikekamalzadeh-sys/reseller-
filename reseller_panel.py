# -*- coding: utf-8 -*-
"""
reseller_panel.py
==================
پنل وب مستقل و مخصوص خودِ فروشنده‌ها (نه پنل ادمین). این یه سرویس کاملاً جدا
از bot-9.py / web_panel.py / reseller_api.py هست — به هیچ دیتابیسی مستقیم
وصل نمی‌شه؛ فقط با HTTP به همون reseller_api.py که قبلاً ساختیم صحبت می‌کنه.

منطق لاگین: فروشنده تو فرم ورود سه‌تا چیز می‌زنه:
    - آدرس API (همون آدرسی که reseller_api.py روش بالاست)
    - API Key
    - API Secret
با همین‌ها یه درخواست GET /api/v1/balance به همون آدرس می‌زنیم؛ اگه ۲۰۰
برگردوند یعنی معتبره → یه سشن (کوکی امضاشده) می‌سازیم و وارد داشبورد می‌شه.
اگه غلط بود (۴۰۱/۴۰۳) یا اصلاً اون آدرس در دسترس نبود، همون‌جا خطا نشون
می‌دیم و اجازه‌ی ورود نمی‌دیم.

این پنل خودش هیچ رمزی رو تو دیتابیس ذخیره نمی‌کنه؛ فقط تو کوکی سمت کاربرِ
خودش (امضاشده با SECRET_KEY) نگه می‌داره، دقیقاً مثل یه سشن لاگین معمولی.

اجرا:
    pip install flask requests --break-system-packages
    export SECRET_KEY="یه رشته‌ی تصادفی طولانی و امن"
    export DEFAULT_API_BASE="https://reseller-api-xxxx.up.railway.app"   (اختیاری، فقط برای پرکردن خودکار فیلد آدرس)
    python3 reseller_panel.py

روی Railway: دقیقاً مثل reseller_api.py، یه سرویس جدا با Start Command:
    python3 reseller_panel.py
و یه Domain جدا براش Generate کن — این آدرس رو به فروشنده‌هات می‌دی تا وارد
پنل خودشون بشن (نه پنل ادمین!).
"""

import os
import html as html_lib
from datetime import datetime, timedelta

import requests
from flask import Flask, request, redirect, url_for, session, render_template_string

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key-in-production")
DEFAULT_API_BASE = os.getenv("DEFAULT_API_BASE", "")

TYPE_LABELS = {
    'config': '🛡 کانفیگ',
    'wireguard': '🔒 وایرگارد',
    'both': '🧩 کانفیگ + وایرگارد',
    'openvpn': '📱 اوپن‌وی‌پی‌ان',
    'dns': '🎮 DNS بازی',
}

ERROR_MESSAGES = {
    'missing_credentials': 'اطلاعات ورود ناقصه.',
    'invalid_key': 'کلید API یا آدرس اشتباهه.',
    'invalid_secret': 'رمز API اشتباهه.',
    'account_blocked': 'حساب فروشندگی شما مسدود شده. با ادمین تماس بگیرید.',
    'rate_limited': 'درخواست‌های شما زیاده؛ کمی صبر کنید.',
    'insufficient_balance': 'موجودی استخر گیگ کافی نیست.',
    'invalid_type': 'نوع کانفیگ نامعتبره.',
    'invalid_input': 'اطلاعات واردشده نامعتبره.',
    'panel_error': 'ساخت/حذف کانفیگ رو سرور ناموفق بود. بعداً دوباره تلاش کنید.',
    'not_found': 'کانفیگ مورد نظر پیدا نشد.',
}


def esc(v):
    return html_lib.escape('' if v is None else str(v), quote=True)


def err_text(code, fallback='خطای نامشخص.'):
    return ERROR_MESSAGES.get(code, fallback)


# ---------------------------------------------------------------------------
# ارتباط با reseller_api.py
# ---------------------------------------------------------------------------
def api_call(method, path, api_base=None, api_key=None, api_secret=None, json_body=None, timeout=15):
    """یه درخواست به reseller_api.py می‌زنه. اگه پارامترها داده نشن، از سشن
    فعلی می‌خونه. خروجی: (ok: bool, status_code: int, data: dict)."""
    api_base = (api_base or session.get('api_base', '')).rstrip('/')
    api_key = api_key or session.get('api_key', '')
    api_secret = api_secret or session.get('api_secret', '')
    headers = {'X-Api-Key': api_key, 'X-Api-Secret': api_secret}
    try:
        res = requests.request(method, api_base + path, headers=headers, json=json_body, timeout=timeout)
        try:
            data = res.json()
        except Exception:
            data = {'success': False, 'error': 'bad_response'}
        return res.status_code < 400, res.status_code, data
    except requests.exceptions.RequestException:
        return False, 0, {'success': False, 'error': 'connection_failed',
                           'message': 'اتصال به آدرس API برقرار نشد. آدرس رو چک کنید.'}


def login_required(view):
    def wrapper(*args, **kwargs):
        if not session.get('api_key'):
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


# ---------------------------------------------------------------------------
# ظاهر — با همون حال‌وهوای پنل ادمین (افق آبی سرمه‌ای + کهربایی) هماهنگ شده
# ---------------------------------------------------------------------------
BASE_HTML = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#13203a">
<title>پنل فروشندگی</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lalezar&family=Vazirmatn:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --ink:#13203a; --ink-2:#22335a; --bg:#edf1f7; --surface:#fff; --surface-2:#f4f6fb;
    --border:#d9e0ec; --muted:#5d6b85; --accent:#2f5bff; --accent-dim:rgba(47,91,255,.10);
    --sun:#ffb324; --ok:#12805c; --ok-dim:rgba(18,128,92,.12); --danger:#c73a3a; --danger-dim:rgba(199,58,58,.10);
    --display:'Lalezar','Vazirmatn',Tahoma,sans-serif;
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:'Vazirmatn',Tahoma,sans-serif; font-size:14.5px; line-height:1.75; background:var(--bg); color:var(--ink); }
  a { color:var(--accent); }
  .topbar-wrap { background:var(--ink); border-bottom:3px solid var(--sun); padding-top:env(safe-area-inset-top,0); }
  .topbar { max-width:640px; margin:0 auto; display:flex; align-items:center; justify-content:space-between; padding:14px 18px; }
  .topbar h1 { font-family:var(--display); font-weight:400; font-size:22px; margin:0; color:#fff; }
  .topbar .sub { font-size:12px; color:#a9b7d6; }
  .icon-btn { border:1px solid var(--ink-2); border-radius:10px; padding:8px 12px; color:#cfd9ef; text-decoration:none; font-size:13px; }
  .app { max-width:640px; margin:0 auto; padding:20px 16px 60px; }
  .page-title { font-family:var(--display); font-weight:400; font-size:26px; margin:0 0 16px; }
  .panel { background:var(--surface); border:1px solid var(--border); border-radius:16px; padding:16px 18px; margin-bottom:16px; }
  h3 { font-family:var(--display); font-weight:400; font-size:18px; margin:0 0 10px; }
  .hero { background:var(--ink); color:#fff; border-radius:20px; padding:20px 22px; margin-bottom:16px; position:relative; overflow:hidden; }
  .hero::after { content:''; position:absolute; left:-46px; bottom:-70px; width:150px; height:150px; border-radius:50%; background:var(--sun); opacity:.95; }
  .hero > * { position:relative; z-index:1; }
  .hero-label { font-size:13px; color:#a9b7d6; }
  .hero-number { font-family:var(--display); font-size:44px; margin:2px 0 10px; }
  .hero-unit { font-size:16px; color:#a9b7d6; font-family:'Vazirmatn',sans-serif; margin-right:6px; }
  .hero-meta { display:flex; gap:20px; padding-top:12px; border-top:1px solid var(--ink-2); font-size:12.5px; color:#a9b7d6; }
  input[type=text],input[type=password],input[type=number],select {
    background:var(--surface); border:1.5px solid var(--border); border-radius:10px;
    padding:11px 14px; font-size:14.5px; width:100%; font-family:inherit; color:var(--ink);
  }
  input:focus, select:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-dim); }
  label { display:block; margin:12px 0 6px; font-size:13px; color:var(--muted); font-weight:600; }
  button { background:var(--accent); color:#fff; border:1.5px solid var(--accent); border-radius:10px;
           padding:11px 18px; font-size:14px; font-weight:700; cursor:pointer; width:100%; }
  button.secondary { background:transparent; color:var(--ink); border-color:var(--border); }
  button.danger { background:transparent; color:var(--danger); border-color:var(--danger-dim); }
  .flash { background:var(--ok-dim); color:var(--ok); padding:11px 16px; border-radius:10px; margin-bottom:14px; font-weight:600; }
  .flash.error { background:var(--danger-dim); color:var(--danger); }
  .type-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:8px; }
  .type-grid label { display:flex; align-items:center; gap:6px; margin:0; padding:10px; border:1.5px solid var(--border); border-radius:10px; font-size:13px; color:var(--ink); font-weight:500; cursor:pointer; }
  .type-grid input { width:auto; }
  .cfg-row { padding:12px 0; border-bottom:1px solid var(--border); }
  .cfg-row:last-child { border-bottom:none; }
  .cfg-row .top { display:flex; justify-content:space-between; align-items:center; }
  .cfg-row b { font-size:14.5px; }
  .cfg-row small { color:var(--muted); font-size:12px; }
  .badge { padding:3px 10px; border-radius:6px; font-size:11.5px; font-weight:700; }
  .badge.on { background:var(--ok-dim); color:var(--ok); }
  .badge.off { background:var(--danger-dim); color:var(--danger); }
  .link-box { background:var(--surface-2); border:1px dashed var(--border); border-radius:8px; padding:8px 10px; font-size:11.5px; word-break:break-all; margin-top:6px; }
  .login-wrap { min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px; background:var(--ink); }
  .login-box { background:var(--surface); border-radius:18px; padding:26px 24px; width:100%; max-width:380px; }
  .login-box h2 { margin:0 0 4px; font-family:var(--display); font-weight:400; font-size:26px; text-align:center; }
  .login-box p.sub { text-align:center; color:var(--muted); margin:0 0 12px; font-size:13px; }
</style>
</head>
<body>{{ body|safe }}</body>
</html>
"""


def render_page(title, content_html):
    flashes = ''.join(f'<div class="flash">{esc(m)}</div>' for m in session.pop('_flash', []))
    body = f"""
    <header class="topbar-wrap">
      <div class="topbar">
        <div><h1>پنل فروشندگی</h1><div class="sub">SkyTunnel Reseller</div></div>
        <a href="{url_for('logout')}" class="icon-btn">خروج</a>
      </div>
    </header>
    <main class="app">
      <h2 class="page-title">{title}</h2>
      {flashes}
      {content_html}
    </main>
    """
    return render_template_string(BASE_HTML, body=body)


def flash(msg):
    session.setdefault('_flash', []).append(msg)
    session.modified = True


# ---------------------------------------------------------------------------
# لاگین
# ---------------------------------------------------------------------------
LOGIN_HTML = """
<div class="login-wrap">
  <form method="post" class="login-box">
    <h2>ورود فروشنده</h2>
    <p class="sub">با آدرس API، کلید و رمزی که از ربات گرفتید وارد شوید.</p>
    {% if error %}<div class="flash error">{{ error }}</div>{% endif %}
    <label for="api_base">آدرس API</label>
    <input id="api_base" type="text" name="api_base" placeholder="https://reseller-api-xxxx.up.railway.app"
           value="{{ api_base }}" autocapitalize="off" autocorrect="off" spellcheck="false" required>
    <label for="api_key">API Key</label>
    <input id="api_key" type="text" name="api_key" placeholder="rk_xxxxxxxxxx"
           autocapitalize="off" autocorrect="off" spellcheck="false" required>
    <label for="api_secret">API Secret</label>
    <input id="api_secret" type="password" name="api_secret" required>
    <div style="margin-top:18px;"><button type="submit">ورود</button></div>
  </form>
</div>
"""


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    api_base_prefill = DEFAULT_API_BASE
    if request.method == 'POST':
        api_base = request.form.get('api_base', '').strip()
        api_key = request.form.get('api_key', '').strip()
        api_secret = request.form.get('api_secret', '').strip()
        api_base_prefill = api_base
        if not (api_base.startswith('http://') or api_base.startswith('https://')):
            error = 'آدرس API باید با http:// یا https:// شروع بشه.'
        else:
            ok, status, data = api_call('GET', '/api/v1/balance', api_base=api_base,
                                         api_key=api_key, api_secret=api_secret)
            if ok:
                session['api_base'] = api_base
                session['api_key'] = api_key
                session['api_secret'] = api_secret
                return redirect(url_for('dashboard'))
            elif status == 0:
                error = data.get('message', 'اتصال به آدرس API برقرار نشد.')
            else:
                error = err_text(data.get('error'), 'اطلاعات ورود درست نیست.')
    return render_template_string(LOGIN_HTML, error=error, api_base=esc(api_base_prefill))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# داشبورد
# ---------------------------------------------------------------------------
@app.route('/')
@login_required
def dashboard():
    ok, status, bal = api_call('GET', '/api/v1/balance')
    if not ok and status in (401, 403):
        session.clear()
        flash('نشست شما منقضی شده یا کلید تغییر کرده؛ دوباره وارد شوید.')
        return redirect(url_for('login'))
    if not ok:
        return render_page('داشبورد', f'<div class="panel"><p>{esc(err_text(bal.get("error"), "خطا در دریافت اطلاعات."))}</p></div>')

    ok2, _st2, cfgs = api_call('GET', '/api/v1/configs')
    configs = cfgs.get('configs', []) if ok2 else []

    type_options = ''.join(
        f'<label><input type="radio" name="type" value="{k}" {"checked" if i == 0 else ""}> {esc(v)}</label>'
        for i, (k, v) in enumerate(TYPE_LABELS.items())
    )

    rows_html = ''
    for c in configs[:15]:
        try:
            created_dt = datetime.fromisoformat(c['created_at'])
            expiry_dt = created_dt + timedelta(days=c.get('days') or 0)
            created_s = created_dt.strftime('%Y-%m-%d')
            expiry_s = expiry_dt.strftime('%Y-%m-%d')
        except Exception:
            created_s = c.get('created_at', '-')
            expiry_s = '-'
        badge = '<span class="badge on">فعال</span>' if c.get('active') else '<span class="badge off">حذف‌شده</span>'
        del_btn = f"""
            <form method="post" action="{url_for('delete_config', config_id=c['config_id'])}"
                  onsubmit="return confirm('این کانفیگ حذف بشه؟');">
              <button type="submit" class="danger" style="width:auto;padding:4px 12px;font-size:12px;">حذف</button>
            </form>
        """ if c.get('active') else ''
        rows_html += f"""
        <div class="cfg-row">
          <div class="top">
            <b>{esc(c.get('label'))}</b>
            {badge}
          </div>
          <small>{esc(TYPE_LABELS.get(c.get('type'), c.get('type')))} — {c.get('gb')} گیگ — {c.get('days')} روز — ساخت: {created_s} — انقضا: {expiry_s}</small>
          <div class="link-box">{esc(c.get('sub_url'))}</div>
          {del_btn}
        </div>
        """

    content = f"""
    <section class="hero">
      <div class="hero-label">موجودی استخر گیگ</div>
      <div class="hero-number">{bal.get('gb_balance', 0):g}<span class="hero-unit">گیگابایت</span></div>
      <div class="hero-meta"><span>قیمت هر گیگ برای شما: {bal.get('price_per_gb', 0):,} تومان</span></div>
    </section>

    <div class="panel">
      <h3>🛠 ساخت کانفیگ جدید</h3>
      <form method="post" action="{url_for('create_config')}">
        <label>نوع کانفیگ</label>
        <div class="type-grid">{type_options}</div>
        <label for="gb">حجم (گیگابایت)</label>
        <input id="gb" type="number" name="gb" step="0.1" min="0.1" required>
        <label for="days">مدت اعتبار (روز)</label>
        <input id="days" type="number" name="days" min="1" required>
        <label for="label">نام/لیبل (اختیاری)</label>
        <input id="label" type="text" name="label" placeholder="مثلاً: مشتری-احمدی">
        <div style="margin-top:16px;"><button type="submit">ساخت کانفیگ</button></div>
      </form>
    </div>

    <div class="panel">
      <h3>📋 کانفیگ‌های ساخته‌شده</h3>
      {rows_html if rows_html else '<p style="color:var(--muted);">هنوز کانفیگی نساختید.</p>'}
    </div>
    """
    return render_page('داشبورد فروشندگی', content)


@app.route('/configs/create', methods=['POST'])
@login_required
def create_config():
    ctype = request.form.get('type', 'both')
    try:
        gb = float(request.form.get('gb', ''))
        days = int(request.form.get('days', ''))
    except ValueError:
        flash('حجم و مدت باید عدد باشن.')
        return redirect(url_for('dashboard'))
    label = request.form.get('label', '').strip() or None

    body = {'gb': gb, 'days': days, 'type': ctype}
    if label:
        body['label'] = label
    ok, status, data = api_call('POST', '/api/v1/configs', json_body=body)
    if ok:
        flash(f"✅ کانفیگ «{data.get('label')}» ساخته شد. موجودی باقیمونده: {data.get('gb_balance_remaining'):g} گیگ.")
    elif status in (401, 403):
        session.clear()
        flash('نشست شما منقضی شده؛ دوباره وارد شوید.')
        return redirect(url_for('login'))
    else:
        flash('⚠️ ' + err_text(data.get('error'), data.get('message', 'خطا در ساخت کانفیگ.')))
    return redirect(url_for('dashboard'))


@app.route('/configs/<config_id>/delete', methods=['POST'])
@login_required
def delete_config(config_id):
    ok, status, data = api_call('DELETE', f'/api/v1/configs/{config_id}')
    if ok:
        flash('کانفیگ حذف شد.')
    elif status in (401, 403):
        session.clear()
        flash('نشست شما منقضی شده؛ دوباره وارد شوید.')
        return redirect(url_for('login'))
    else:
        flash('⚠️ ' + err_text(data.get('error'), 'حذف ناموفق بود.'))
    return redirect(url_for('dashboard'))


if __name__ == '__main__':
    port = int(os.getenv('PORT') or os.getenv('RESELLER_PANEL_PORT', '8089'))
    print(f'🧑\u200d💼 پنل خودسرویس فروشندگی رو پورت {port} بالا اومد.')
    app.run(host='0.0.0.0', port=port, threaded=True)
