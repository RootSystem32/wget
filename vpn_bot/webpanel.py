# -*- coding: utf-8 -*-
"""
DubikVPN Web Panel v5
Порт 4441, доступ по IP-белому списку.
"""
import os
import json
import re
import logging
from datetime import datetime, timedelta
from functools import wraps
from collections import Counter, defaultdict
from flask import Flask, request, jsonify, Response

# ============ АБСОЛЮТНЫЕ ПУТИ ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

USERS_FILES = [
    os.path.join(BASE_DIR, "users.json"),
    os.path.join(BASE_DIR, "data", "users.json"),
]
ACTIVITY_FILES = [
    os.path.join(BASE_DIR, "activity.json"),
    os.path.join(BASE_DIR, "activity_log.json"),
    os.path.join(BASE_DIR, "logs.json"),
    os.path.join(BASE_DIR, "user_activity.json"),
    os.path.join(BASE_DIR, "data", "activity.json"),
    os.path.join(BASE_DIR, "data", "activity_log.json"),
    os.path.join(BASE_DIR, "data", "logs.json"),
]
TRANSACTIONS_FILES = [
    os.path.join(BASE_DIR, "transactions.json"),
    os.path.join(BASE_DIR, "data", "transactions.json"),
]
ADMINS_FILES = [
    os.path.join(BASE_DIR, "admins.json"),
    os.path.join(BASE_DIR, "data", "admins.json"),
]
IP_CONFIG_FILE = os.path.join(BASE_DIR, "allowed_ips.json")

# ============ КОНФИГ ============
PANEL_PORT = 4441
PANEL_HOST = "0.0.0.0"
PANEL_PUBLIC_IP = "144.31.247.251"
ONLINE_THRESHOLD_MIN = 5
DEFAULT_ALLOWED_IPS = ["46.43.210.150", "127.0.0.1", "::1", "144.31.247.251"]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# ============ ИМПОРТ ИЗ БОТА (опционально) ============
try:
    from database import get_users, get_user, save_user, get_admins, add_admin, get_servers
except Exception as e:
    logging.warning(f"database не импортирован: {e}")
    def get_users(): return {}
    def get_user(uid): return {}
    def save_user(uid, d): pass
    def get_admins(): return []
    def add_admin(uid): pass
    def get_servers(): return []

try:
    from panel_api import get_client_usage
except Exception:
    def get_client_usage(*a, **k): return None

app = Flask(__name__)


# ============================================================
#              IP WHITELIST (файл + API)
# ============================================================
def load_allowed_ips():
    """Читает белый список. Если файла нет — создаёт с дефолтными."""
    if os.path.exists(IP_CONFIG_FILE):
        try:
            with open(IP_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception as e:
            logging.error(f"load_allowed_ips: {e}")

    # Создаём дефолтный
    try:
        with open(IP_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_ALLOWED_IPS, f, ensure_ascii=False, indent=2)
        os.chmod(IP_CONFIG_FILE, 0o600)
        logging.info(f"✅ Создан {IP_CONFIG_FILE}")
    except Exception as e:
        logging.error(f"Не удалось создать {IP_CONFIG_FILE}: {e}")
    return list(DEFAULT_ALLOWED_IPS)


def save_allowed_ips(ips):
    """Атомарно сохраняет белый список."""
    tmp = IP_CONFIG_FILE + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(ips, f, ensure_ascii=False, indent=2)
    os.replace(tmp, IP_CONFIG_FILE)
    try:
        os.chmod(IP_CONFIG_FILE, 0o600)
    except Exception:
        pass
    logging.info(f"💾 Сохранено {len(ips)} IP → {IP_CONFIG_FILE}")


def get_client_ip():
    cf = request.headers.get('CF-Connecting-IP')
    if cf:
        return cf.strip()
    xff = request.headers.get('X-Forwarded-For')
    if xff:
        return xff.split(',')[0].strip()
    xri = request.headers.get('X-Real-IP')
    if xri:
        return xri.strip()
    return request.remote_addr or ""


def ip_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        cip = get_client_ip()
        allowed = load_allowed_ips()
        if "*" in allowed or cip in allowed:
            return f(*args, **kwargs)
        logging.warning(f"🚫 Доступ запрещён: {cip}")
        return Response(
            f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>403</title>
            <style>body{{font-family:system-ui;padding:60px;background:#0f0f14;color:#fff}}
            h1{{color:#ff4d4d}}b{{color:#ffb84d}}</style></head>
            <body><h1>🚫 403 — Доступ запрещён</h1>
            <p>Ваш IP: <b>{cip}</b></p>
            <p>Этот IP не входит в белый список панели.</p></body></html>""",
            status=403, mimetype="text/html"
        )
    return wrapper


# ============================================================
#              ЗАГРУЗКА ДАННЫХ
# ============================================================
def _read_json_first(paths):
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"read {p}: {e}")
    return None


def get_all_users_dict():
    try:
        u = get_users()
        if isinstance(u, dict) and u:
            return u
    except Exception:
        pass
    d = _read_json_first(USERS_FILES)
    return d if isinstance(d, dict) else {}


def get_admins_list():
    try:
        a = get_admins()
        if a:
            return list(a)
    except Exception:
        pass
    d = _read_json_first(ADMINS_FILES)
    return d if isinstance(d, list) else []


def get_activity_log():
    for p in ACTIVITY_FILES:
        if not os.path.exists(p):
            continue
        try:
            with open(p, 'r', encoding='utf-8') as f:
                d = json.load(f)
            if isinstance(d, list):
                return d
            if isinstance(d, dict):
                flat = []
                for uid, events in d.items():
                    if isinstance(events, list):
                        for e in events:
                            if isinstance(e, dict):
                                e = dict(e)
                                e['user_id'] = int(uid) if str(uid).lstrip('-').isdigit() else uid
                                flat.append(e)
                return flat
        except Exception as e:
            logging.error(f"activity {p}: {e}")
    return []


def get_transactions_list():
    d = _read_json_first(TRANSACTIONS_FILES)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        flat = []
        for uid, items in d.items():
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        it = dict(it)
                        it['user_id'] = int(uid) if str(uid).lstrip('-').isdigit() else uid
                        flat.append(it)
        return flat
    return []


def get_servers_list():
    try:
        s = get_servers()
        if s:
            return s
    except Exception:
        pass
    return []


def get_activity_source_file():
    for p in ACTIVITY_FILES:
        if os.path.exists(p):
            return p
    return None


# ============================================================
#              ХЕЛПЕРЫ
# ============================================================
def parse_dt(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            v = val / 1000 if val > 10_000_000_000 else val
            return datetime.fromtimestamp(v)
        except Exception:
            return None
    if isinstance(val, str):
        s = val.replace('Z', '').split('+')[0].strip()
        try:
            return datetime.fromisoformat(s)
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S.%f",
                    "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M",
                    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
    return None


def fmt_dt(val):
    dt = parse_dt(val)
    return dt.strftime('%d.%m.%Y %H:%M') if dt else '—'


def humanize_delta(delta):
    if not delta or delta.total_seconds() < 0:
        return '—'
    secs = int(delta.total_seconds())
    days = secs // 86400
    hours = (secs % 86400) // 3600
    minutes = (secs % 3600) // 60
    if days > 0:
        return f"{days} д {hours} ч"
    if hours > 0:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def days_until(expiry_dt, now=None):
    if not expiry_dt:
        return 0
    if now is None:
        now = datetime.now()
    delta = expiry_dt - now
    if delta.total_seconds() <= 0:
        return 0
    return int(delta.total_seconds() // 86400)


def detect_sub_source(s):
    if not isinstance(s, dict):
        return 'unknown'
    src = s.get('source')
    if src in ('purchase', 'free', 'admin'):
        return src
    if s.get('is_free'):
        return 'free'
    if s.get('created_by'):
        return 'admin'
    return 'purchase'


def calc_user_stats(user_id, user_data, activity_log, servers):
    subs = user_data.get('subscriptions', []) or []
    now = datetime.now()

    total_keys = 0
    active_keys = 0
    expired_keys = 0
    free_keys = 0
    admin_keys = 0
    purchase_keys = 0
    days_left = 0
    days_purchased = 0
    tariff = "Нет"
    vpn_ids = []
    online = 0
    devices_ok = True
    best_expiry = None
    best_sub = None
    subs_enriched = []

    for s in subs:
        if not isinstance(s, dict):
            continue
        total_keys += 1
        src = detect_sub_source(s)
        if src == 'free':
            free_keys += 1
        elif src == 'admin':
            admin_keys += 1
        else:
            purchase_keys += 1

        expiry = parse_dt(s.get('expiry_date'))
        is_active = bool(expiry and expiry > now)
        sub_days_left = days_until(expiry, now) if is_active else 0

        if is_active:
            active_keys += 1
            if best_expiry is None or expiry > best_expiry:
                best_expiry = expiry
                best_sub = s
        elif expiry:
            expired_keys += 1

        s2 = dict(s)
        s2['_days_left'] = sub_days_left
        s2['_is_active'] = is_active
        s2['_source'] = src
        subs_enriched.append(s2)

        cid = s.get('client_id')
        if cid:
            vpn_ids.append(str(cid))

    if best_expiry:
        days_left = days_until(best_expiry, now)
        days_purchased = int(best_sub.get('days', 0) or 0)

    if best_sub:
        d = int(best_sub.get('days', 0) or 0)
        src = detect_sub_source(best_sub)
        if src == 'free':
            tariff = f"🎁 Пробный ({d}д)"
        elif d == 30:
            tariff = "1 месяц"
        elif d == 90:
            tariff = "3 месяца"
        elif d == 180:
            tariff = "6 месяцев"
        elif d > 0:
            tariff = f"{d} дней"
        else:
            tariff = "Активна"
        if src == 'admin':
            tariff = f"👑 {tariff}"

    if best_sub and best_sub.get('uuid') and servers:
        try:
            usage = get_client_usage(servers[0], best_sub.get('uuid'))
            if usage and 'online' in usage:
                online = int(usage.get('online', 0) or 0)
            else:
                devices_ok = False
        except Exception:
            devices_ok = False
    elif best_sub:
        devices_ok = False

    first_seen = None
    last_seen = None
    events_count = 0
    actions = Counter()
    for e in activity_log:
        try:
            if int(e.get('user_id', -1)) != int(user_id):
                continue
        except Exception:
            continue
        ts = parse_dt(e.get('timestamp') or e.get('time') or e.get('date'))
        if ts:
            if first_seen is None or ts < first_seen:
                first_seen = ts
            if last_seen is None or ts > last_seen:
                last_seen = ts
        events_count += 1
        actions[str(e.get('action') or e.get('event') or '?')[:60]] += 1

    if first_seen is None:
        for key in ('registered_at', 'first_seen', 'created_at', 'join_date'):
            dt = parse_dt(user_data.get(key))
            if dt:
                first_seen = dt
                break

    time_in_bot = humanize_delta(now - first_seen) if first_seen else '—'
    online_now = bool(last_seen and (now - last_seen).total_seconds() < ONLINE_THRESHOLD_MIN * 60)

    return {
        'user_id': user_id,
        'first_name': user_data.get('first_name') or user_data.get('name') or '',
        'username': user_data.get('username') or '',
        'balance': float(user_data.get('balance', 0) or 0),
        'total_keys': total_keys,
        'active_keys': active_keys,
        'expired_keys': expired_keys,
        'free_keys': free_keys,
        'admin_keys': admin_keys,
        'purchase_keys': purchase_keys,
        'days_left': days_left,
        'days_purchased': days_purchased,
        'tariff': tariff,
        'vpn_ids': vpn_ids,
        'online_devices': online,
        'devices_ok': devices_ok,
        'got_free': bool(user_data.get('got_free', False)),
        'is_admin': int(user_id) in [int(x) for x in get_admins_list() if str(x).lstrip('-').isdigit()],
        'first_seen': first_seen.isoformat() if first_seen else None,
        'last_seen': last_seen.isoformat() if last_seen else None,
        'online_now': online_now,
        'events_count': events_count,
        'top_actions': actions.most_common(8),
        'time_in_bot': time_in_bot,
        'subs_raw': subs_enriched,
    }


# ============================================================
#              HTML
# ============================================================
HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DubikVPN • Admin Panel</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0b0d14;color:#e6e8ee;font-size:14px}
a{color:#4da3ff;text-decoration:none}
.header{background:linear-gradient(135deg,#151823,#1d2230);padding:18px 28px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #232838;position:sticky;top:0;z-index:100;flex-wrap:wrap;gap:10px}
.header h1{font-size:20px;font-weight:600;display:flex;align-items:center;gap:10px}
.header .badge{background:#1e2c46;color:#6db3ff;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:500}
.ip-info{font-size:12px;color:#8b93a7}
.ip-info b{color:#6db3ff}
.container{padding:22px 28px;max-width:1700px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:22px}
.card{background:#141824;border:1px solid #232838;border-radius:12px;padding:18px;transition:.2s;position:relative}
.card:hover{border-color:#3a4a6e;transform:translateY(-2px)}
.card .label{color:#8b93a7;font-size:12px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}
.card .value{font-size:26px;font-weight:700;color:#fff}
.card .value.green{color:#4ade80}
.card .value.blue{color:#6db3ff}
.card .value.orange{color:#ffb84d}
.card .value.purple{color:#c084fc}
.card .sub{font-size:12px;color:#6b7280;margin-top:4px}
.card .pulse{position:absolute;top:12px;right:12px;width:9px;height:9px;border-radius:50%;background:#4ade80;animation:pulse 2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(74,222,128,.7)}70%{box-shadow:0 0 0 10px rgba(74,222,128,0)}100%{box-shadow:0 0 0 0 rgba(74,222,128,0)}}
.tabs{display:flex;gap:6px;background:#141824;border-radius:10px;padding:5px;margin-bottom:20px;border:1px solid #232838;flex-wrap:wrap}
.tab{padding:10px 18px;border-radius:7px;cursor:pointer;font-weight:500;color:#8b93a7;transition:.15s;user-select:none;font-size:13px}
.tab:hover{color:#e6e8ee}
.tab.active{background:#1e2c46;color:#6db3ff}
.section{display:none}
.section.active{display:block}
.toolbar{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap;align-items:center}
input[type=text],input[type=number]{background:#141824;border:1px solid #232838;color:#e6e8ee;padding:10px 14px;border-radius:8px;font-size:14px;outline:none;font-family:inherit}
input[type=text]:focus{border-color:#4da3ff}
input[type=text]{flex:1;min-width:200px}
button{background:#1e2c46;color:#6db3ff;border:1px solid #2d4a73;padding:10px 16px;border-radius:8px;cursor:pointer;font-weight:500;font-size:13px;transition:.15s;font-family:inherit}
button:hover{background:#2d4a73;color:#a3ccff}
button.primary{background:#2563eb;color:#fff;border-color:#2563eb}
button.primary:hover{background:#1d4ed8}
button.danger{background:#7f1d1d;color:#ffb4b4;border-color:#991b1b}
button.danger:hover{background:#991b1b;color:#fff}
button.success{background:#166534;color:#bbf7d0;border-color:#15803d}
button.sm{padding:6px 10px;font-size:12px}
button.period{padding:7px 12px;font-size:12px}
button.period.active{background:#2563eb;color:#fff;border-color:#2563eb}
table{width:100%;border-collapse:collapse;background:#141824;border-radius:12px;overflow:hidden;border:1px solid #232838}
th{background:#1a1f2e;padding:12px 10px;text-align:left;font-size:11px;text-transform:uppercase;color:#8b93a7;letter-spacing:.5px;font-weight:600;white-space:nowrap}
td{padding:11px 10px;border-top:1px solid #1d2333;font-size:13px;vertical-align:middle}
tr:hover td{background:#181d2b}
.tag{display:inline-block;padding:3px 8px;border-radius:5px;font-size:11px;font-weight:600;white-space:nowrap}
.tag.green{background:#052e16;color:#4ade80}
.tag.red{background:#450a0a;color:#f87171}
.tag.blue{background:#0c2d5c;color:#6db3ff}
.tag.orange{background:#3d2408;color:#ffb84d}
.tag.purple{background:#2a0e4d;color:#c084fc}
.tag.gray{background:#1f2937;color:#9ca3af}
.tag.bang{background:#3d2408;color:#ffb84d;font-weight:700}
.tag.online{background:#052e16;color:#4ade80;font-size:10px}
.mono{font-family:'JetBrains Mono',Consolas,monospace;font-size:12px;color:#a3ccff}
.muted{color:#6b7280;font-size:12px}
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:1000;align-items:center;justify-content:center;padding:20px}
.modal-bg.show{display:flex}
.modal{background:#141824;border:1px solid #2d3a52;border-radius:14px;max-width:820px;width:100%;max-height:90vh;overflow:auto;padding:24px}
.modal h2{margin-bottom:16px;font-size:18px}
.modal h3{margin:18px 0 10px;font-size:14px;color:#8b93a7;text-transform:uppercase}
.modal .row{display:flex;padding:8px 0;border-bottom:1px solid #1d2333;font-size:13px}
.modal .row .k{width:200px;color:#8b93a7;flex-shrink:0}
.modal .row .v{flex:1;word-break:break-word}
.modal .actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px;padding-top:18px;border-top:1px solid #232838}
.chart-wrap{background:#141824;border:1px solid #232838;border-radius:12px;padding:20px;margin-bottom:22px}
.chart-wrap h3{margin-bottom:14px;font-size:15px;font-weight:600;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
.chart-wrap canvas{max-height:300px}
.chart-row{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:22px}
@media(max-width:900px){.chart-row{grid-template-columns:1fr}}
.period-btns{display:flex;gap:5px;flex-wrap:wrap}
.ip-list{background:#141824;border:1px solid #232838;border-radius:12px;padding:16px;margin-top:14px}
.ip-list .item{display:flex;justify-content:space-between;align-items:center;padding:10px 12px;background:#0f1219;border-radius:8px;margin-bottom:8px;font-family:monospace}
.ip-list .item:last-child{margin-bottom:0}
.empty{text-align:center;padding:40px;color:#6b7280}
.toast{position:fixed;bottom:24px;right:24px;background:#1e2c46;border:1px solid #2d4a73;color:#e6e8ee;padding:14px 20px;border-radius:10px;z-index:2000;box-shadow:0 10px 40px rgba(0,0,0,.5);opacity:0;transform:translateY(20px);transition:.3s;max-width:340px}
.toast.show{opacity:1;transform:translateY(0)}
.toast.success{border-color:#15803d;background:#0f2a1a}
.toast.error{border-color:#991b1b;background:#2a0f0f}
.live-feed{background:#141824;border:1px solid #232838;border-radius:12px;padding:16px;max-height:520px;overflow-y:auto}
.live-feed .ev{padding:9px 12px;border-left:3px solid #2d4a73;background:#0f1219;border-radius:6px;margin-bottom:7px;font-size:12.5px}
.live-feed .ev .time{color:#6b7280;font-size:11px;margin-right:8px}
.live-feed .ev .uid{color:#6db3ff;font-family:monospace;margin-right:6px}
.top-user{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;background:#0f1219;border-radius:8px;margin-bottom:6px}
.top-user .num{width:26px;height:26px;border-radius:50%;background:#1e2c46;color:#6db3ff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:12px;margin-right:10px}
.top-user .info{flex:1;display:flex;align-items:center}
.top-user .count{color:#4ade80;font-weight:600}
.sub-item{padding:10px 12px;background:#0f1219;border-radius:8px;margin-bottom:8px;border-left:3px solid #2d4a73;font-size:12.5px;position:relative}
.sub-item.expired{border-left-color:#991b1b;opacity:.65}
.sub-item.active{border-left-color:#15803d}
.sub-item.free{border-left-color:#ffb84d}
.sub-item.admin{border-left-color:#c084fc}
.sub-item .sub-line{display:flex;justify-content:space-between;margin:3px 0;gap:10px}
.sub-item .sub-line .lbl{color:#8b93a7;flex-shrink:0}
.sub-item .src-badge{position:absolute;top:10px;right:12px;font-size:10px}
.diag{background:#3d2408;border:1px solid #7c4a08;color:#ffb84d;padding:14px 18px;border-radius:10px;margin-bottom:18px;font-size:13px}
.diag b{color:#fff}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid #2d4a73;border-top-color:#6db3ff;border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>

<div class="header">
  <h1>🔒 DubikVPN <span class="badge">ADMIN v5</span></h1>
  <div class="ip-info">Ваш IP: <b id="myip">—</b> • <a href="#" onclick="showIPs();return false;">Управление доступом</a></div>
</div>

<div class="container">
  <div id="diagBox"></div>

  <div class="cards">
    <div class="card"><div class="pulse"></div><div class="label">Онлайн сейчас</div><div class="value green" id="stOnline">—</div><div class="sub">активны &lt;5 мин</div></div>
    <div class="card"><div class="label">Всего пользователей</div><div class="value blue" id="stTotal">—</div><div class="sub" id="stTotalSub"></div></div>
    <div class="card"><div class="label">Активных подписок</div><div class="value green" id="stActive">—</div><div class="sub" id="stActiveSub"></div></div>
    <div class="card"><div class="label">👑 Админ-ключей</div><div class="value purple" id="stAdminKeys">—</div><div class="sub">выдано вручную</div></div>
    <div class="card"><div class="label">🎁 Бесплатных</div><div class="value orange" id="stFreeKeys">—</div><div class="sub">free</div></div>
    <div class="card"><div class="label">🛒 Купленных</div><div class="value green" id="stPurchaseKeys">—</div><div class="sub">оплачено</div></div>
    <div class="card"><div class="label">Общий баланс</div><div class="value orange" id="stBalance">—</div><div class="sub">сумма балансов</div></div>
    <div class="card"><div class="label">Общий заработок</div><div class="value green" id="stEarn">—</div><div class="sub">по транзакциям</div></div>
    <div class="card"><div class="label">/start всего</div><div class="value purple" id="stStarts">—</div><div class="sub">уник. пользователей</div></div>
    <div class="card"><div class="label">Купили</div><div class="value blue" id="stBuyers">—</div><div class="sub" id="stConv">конверсия</div></div>
    <div class="card"><div class="label">Событий в логе</div><div class="value purple" id="stEvents">—</div><div class="sub" id="stEventsFile">источник</div></div>
  </div>

  <div class="chart-wrap">
    <h3>
      <span>📊 Динамика</span>
      <div class="period-btns">
        <button class="period" data-days="7" onclick="setPeriod(7,this)">7 дн</button>
        <button class="period active" data-days="30" onclick="setPeriod(30,this)">30 дн</button>
        <button class="period" data-days="90" onclick="setPeriod(90,this)">90 дн</button>
        <button class="period" data-days="180" onclick="setPeriod(180,this)">180 дн</button>
        <button class="period" data-days="365" onclick="setPeriod(365,this)">1 год</button>
        <button class="period" data-days="0" onclick="setPeriod(0,this)">Всё время</button>
      </div>
    </h3>
    <canvas id="mainChart"></canvas>
  </div>

  <div class="chart-row">
    <div class="chart-wrap">
      <h3>👥 Новые пользователи</h3>
      <canvas id="newUsersChart"></canvas>
    </div>
    <div class="chart-wrap">
      <h3>🔥 Топ действий</h3>
      <div id="topActions" style="max-height:280px;overflow-y:auto;padding-right:6px"></div>
    </div>
  </div>

  <div class="tabs">
    <div class="tab active" data-tab="users">👥 Пользователи</div>
    <div class="tab" data-tab="adminkeys">👑 Админ-ключи</div>
    <div class="tab" data-tab="live">🔴 Live</div>
    <div class="tab" data-tab="activity">📋 Активность</div>
    <div class="tab" data-tab="top">🏆 Топ</div>
    <div class="tab" data-tab="ips">🛡️ IP-доступ</div>
  </div>

  <div class="section active" id="tab-users">
    <div class="toolbar">
      <input type="text" id="searchInput" placeholder="🔍 Поиск: ID, username, имя, VPN ID...">
      <button class="primary" onclick="loadUsers()">🔄 Обновить</button>
    </div>
    <div style="overflow-x:auto">
      <table id="usersTable">
        <thead><tr>
          <th>Статус</th><th>ID</th><th>Имя</th><th>Username</th>
          <th>Ключи</th><th>Источники</th><th>Тариф</th>
          <th>Осталось/куплено</th><th>Баланс</th><th>Устр.</th><th>В боте</th><th></th>
        </tr></thead>
        <tbody><tr><td colspan="12" class="empty">Загрузка...</td></tr></tbody>
      </table>
    </div>
  </div>

  <div class="section" id="tab-adminkeys">
    <div class="toolbar">
      <input type="text" id="admKeySearch" placeholder="🔍 Поиск...">
      <button class="primary" onclick="loadAdminKeys()">🔄 Обновить</button>
    </div>
    <div style="overflow-x:auto">
      <table id="adminKeysTable">
        <thead><tr>
          <th>Дата выдачи</th><th>Кому</th><th>Кто выдал</th>
          <th>Client ID</th><th>Срок/осталось</th><th>До</th><th>Статус</th>
        </tr></thead>
        <tbody><tr><td colspan="7" class="empty">Загрузка...</td></tr></tbody>
      </table>
    </div>
  </div>

  <div class="section" id="tab-live">
    <div class="toolbar"><button class="primary" onclick="loadLive()">🔄 Обновить</button><span class="muted">Авто 10 сек</span></div>
    <div class="live-feed" id="liveFeed"></div>
  </div>

  <div class="section" id="tab-activity">
    <div class="toolbar">
      <input type="text" id="actSearch" placeholder="🔍 Фильтр...">
      <button class="primary" onclick="loadActivity()">🔄 Обновить</button>
    </div>
    <div style="overflow-x:auto">
      <table id="actTable">
        <thead><tr><th>Время</th><th>User ID</th><th>Username</th><th>Действие</th></tr></thead>
        <tbody><tr><td colspan="4" class="empty">Загрузка...</td></tr></tbody>
      </table>
    </div>
  </div>

  <div class="section" id="tab-top">
    <div class="toolbar"><span class="muted">Топ-20 по событиям</span><button class="primary" onclick="loadTop()">🔄 Обновить</button></div>
    <div id="topUsers"></div>
  </div>

  <div class="section" id="tab-ips">
    <h3 style="margin-bottom:14px">🛡️ Белый список IP</h3>
    <p class="muted" style="margin-bottom:14px">Заходить в панель можно только с этих адресов.</p>
    <div class="toolbar">
      <input type="text" id="newIp" placeholder="Например: 192.168.1.1">
      <button class="primary" onclick="addIp()">+ Добавить</button>
      <button onclick="loadIPs()">🔄</button>
    </div>
    <div class="ip-list" id="ipList"></div>
  </div>
</div>

<div class="modal-bg" id="modalBg"><div class="modal" id="modalContent"></div></div>
<div class="toast" id="toast"></div>

<script>
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

$$('.tab').forEach(t => t.onclick = () => {
  $$('.tab').forEach(x => x.classList.remove('active'));
  $$('.section').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  $('#tab-' + t.dataset.tab).classList.add('active');
  if (t.dataset.tab === 'activity') loadActivity();
  if (t.dataset.tab === 'live') loadLive();
  if (t.dataset.tab === 'top') loadTop();
  if (t.dataset.tab === 'ips') loadIPs();
  if (t.dataset.tab === 'adminkeys') loadAdminKeys();
});

function toast(msg, type='') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  clearTimeout(t._tm);
  t._tm = setTimeout(() => t.className = 'toast ' + type, 3500);
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
  return d;
}

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

let PERIOD_DAYS = 30;
function setPeriod(days, btn) {
  PERIOD_DAYS = days;
  $$('.period').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  loadStats();
}

async function loadDiag() {
  try {
    const d = await api('/api/diag');
    let html = '';
    if (!d.activity_file) {
      html += `<div class="diag">⚠️ <b>Файл активности не найден.</b> Искал: <b>${d.activity_files_searched.join(', ')}</b></div>`;
    } else {
      html += `<div class="diag" style="background:#0f2a1a;border-color:#15803d;color:#4ade80">✅ Активность: <b>${d.activity_file}</b> — <b>${d.activity_count}</b> событий</div>`;
    }
    $('#diagBox').innerHTML = html;
  } catch(e) { console.error(e); }
}

let chartInstances = {};
async function loadStats() {
  try {
    const s = await api('/api/stats?days=' + PERIOD_DAYS);
    $('#stOnline').textContent = s.online_now;
    $('#stTotal').textContent = s.total_users;
    $('#stTotalSub').textContent = s.total_admins + ' админов • ' + s.with_sub + ' с подпиской';
    $('#stActive').textContent = s.active_subs;
    $('#stActiveSub').textContent = s.total_keys + ' ключей всего';
    $('#stAdminKeys').textContent = s.admin_keys || 0;
    $('#stFreeKeys').textContent = s.free_keys || 0;
    $('#stPurchaseKeys').textContent = s.purchase_keys || 0;
    $('#stBalance').textContent = s.total_balance.toFixed(0) + ' ₽';
    $('#stEarn').textContent = s.total_earnings.toFixed(0) + ' ₽';
    $('#stStarts').textContent = s.starts_count;
    $('#stBuyers').textContent = s.buyers_count;
    $('#stConv').textContent = s.conversion + '% конверсия';
    $('#stEvents').textContent = s.events_total;
    $('#stEventsFile').textContent = (s.activity_file||'').split('/').pop() || '—';
    renderMainChart(s.chart);
    renderNewUsersChart(s.new_users_chart);
    renderTopActions(s.top_actions);
  } catch(e) { console.error('stats', e); }
}

function renderMainChart(data) {
  const ctx = $('#mainChart').getContext('2d');
  if (chartInstances.main) chartInstances.main.destroy();
  chartInstances.main = new Chart(ctx, {
    type: 'line',
    data: {
      labels: data.labels,
      datasets: [
        { label: '💰 Заработок (₽)', data: data.values,
          borderColor: '#4ade80', backgroundColor: 'rgba(74,222,128,0.12)',
          fill: true, tension: .3, borderWidth: 2, pointRadius: 2,
          pointBackgroundColor: '#4ade80', yAxisID: 'y' },
        { label: '📊 События', data: data.act_values,
          borderColor: '#c084fc', backgroundColor: 'rgba(192,132,252,0.10)',
          fill: true, tension: .3, borderWidth: 2, pointRadius: 2,
          pointBackgroundColor: '#c084fc', yAxisID: 'y1' },
        { label: '👥 Новые юзеры', data: data.new_values,
          borderColor: '#6db3ff', backgroundColor: 'rgba(109,179,255,0.08)',
          fill: false, tension: .3, borderWidth: 2, pointRadius: 2,
          pointBackgroundColor: '#6db3ff', yAxisID: 'y1' }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: '#8b93a7' } } },
      scales: {
        x: { ticks: { color: '#8b93a7', maxRotation: 0, autoSkip: true, maxTicksLimit: 15 }, grid: { color: '#1d2333' } },
        y: { position: 'left', ticks: { color: '#4ade80' }, grid: { color: '#1d2333' }, beginAtZero: true },
        y1: { position: 'right', ticks: { color: '#c084fc' }, grid: { display: false }, beginAtZero: true }
      }
    }
  });
}
function renderNewUsersChart(data) {
  const ctx = $('#newUsersChart').getContext('2d');
  if (chartInstances.newUsers) chartInstances.newUsers.destroy();
  chartInstances.newUsers = new Chart(ctx, {
    type: 'bar',
    data: { labels: data.labels, datasets: [{
      label: 'Новых', data: data.values,
      backgroundColor: 'rgba(109,179,255,0.55)', borderColor: '#6db3ff', borderWidth: 1, borderRadius: 4
    }]},
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#8b93a7' } } },
      scales: { x: { ticks: { color: '#8b93a7', maxRotation: 0, autoSkip: true, maxTicksLimit: 12 }, grid: { color: '#1d2333' } },
                y: { ticks: { color: '#8b93a7' }, grid: { color: '#1d2333' }, beginAtZero: true } } }
  });
}
function renderTopActions(actions) {
  if (!actions || !actions.length) { $('#topActions').innerHTML = '<div class="empty">Нет данных</div>'; return; }
  const max = actions[0][1] || 1;
  $('#topActions').innerHTML = actions.map(([name, count]) => {
    const pct = Math.round(count / max * 100);
    return `<div style="margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;margin-bottom:4px;font-size:13px">
        <span>${escapeHtml(name)}</span><span style="color:#c084fc;font-weight:600">${count}</span>
      </div>
      <div style="background:#0f1219;border-radius:4px;height:6px;overflow:hidden">
        <div style="width:${pct}%;height:100%;background:linear-gradient(90deg,#6db3ff,#c084fc)"></div>
      </div>
    </div>`;
  }).join('');
}

let USERS_CACHE = [];
async function loadUsers() {
  const tb = $('#usersTable tbody');
  tb.innerHTML = '<tr><td colspan="12" class="empty"><span class="spinner"></span> Загрузка...</td></tr>';
  try {
    const u = await api('/api/users');
    USERS_CACHE = u.users;
    renderUsers(USERS_CACHE);
  } catch(e) { tb.innerHTML = '<tr><td colspan="12" class="empty">Ошибка: '+e.message+'</td></tr>'; }
}

function renderUsers(users) {
  const tb = $('#usersTable tbody');
  if (!users.length) { tb.innerHTML = '<tr><td colspan="12" class="empty">Нет пользователей</td></tr>'; return; }
  tb.innerHTML = users.map(u => {
    const dev = u.devices_ok ? `<span class="tag blue">${u.online_devices}</span>` : `<span class="tag bang">!</span>`;
    const tariffCls = u.active_keys ? 'green' : (u.total_keys ? 'red' : 'gray');
    const adminTag = u.is_admin ? ' <span class="tag purple">ADMIN</span>' : '';
    const onlineDot = u.online_now ? '<span class="tag online">● ONLINE</span>' : '<span class="tag gray">offline</span>';
    const keyStr = u.expired_keys > 0
      ? `<span style="color:#4ade80">${u.active_keys}</span>/${u.total_keys} <span class="muted">(${u.expired_keys}❌)</span>`
      : `<span style="color:#4ade80">${u.active_keys}</span>/${u.total_keys}`;
    let srcHtml = '';
    if (u.purchase_keys) srcHtml += `<span class="tag green" style="margin:1px">🛒${u.purchase_keys}</span>`;
    if (u.free_keys) srcHtml += `<span class="tag orange" style="margin:1px">🎁${u.free_keys}</span>`;
    if (u.admin_keys) srcHtml += `<span class="tag purple" style="margin:1px">👑${u.admin_keys}</span>`;
    let daysCell = '<span class="muted">—</span>';
    if (u.active_keys) {
      const color = u.days_left <= 3 ? '#f87171' : (u.days_left <= 7 ? '#ffb84d' : '#4ade80');
      daysCell = `<b style="color:${color}">${u.days_left}</b> <span class="muted">/${u.days_purchased}</span>`;
    }
    return `<tr>
      <td>${onlineDot}</td>
      <td class="mono">${u.user_id}</td>
      <td>${escapeHtml(u.first_name)}${adminTag}</td>
      <td>${u.username ? '<a href="https://t.me/'+escapeHtml(u.username)+'" target="_blank">@'+escapeHtml(u.username)+'</a>' : '<span class="muted">—</span>'}</td>
      <td>${keyStr}</td>
      <td>${srcHtml||'<span class="muted">—</span>'}</td>
      <td><span class="tag ${tariffCls}">${u.tariff}</span></td>
      <td>${daysCell}</td>
      <td class="${u.balance>0?'':'muted'}">${u.balance.toFixed(0)} ₽</td>
      <td>${dev}</td>
      <td class="muted">${u.time_in_bot}</td>
      <td><button class="sm" onclick="openUser(${u.user_id})">Открыть</button></td>
    </tr>`;
  }).join('');
}

$('#searchInput').addEventListener('input', e => {
  const q = e.target.value.toLowerCase().trim();
  if (!q) return renderUsers(USERS_CACHE);
  renderUsers(USERS_CACHE.filter(u =>
    String(u.user_id).includes(q) ||
    (u.username||'').toLowerCase().includes(q) ||
    (u.first_name||'').toLowerCase().includes(q) ||
    u.vpn_ids.some(v => v.toLowerCase().includes(q))
  ));
});

function srcBadge(src){
  if (src === 'admin') return '<span class="tag purple">👑 админ</span>';
  if (src === 'free') return '<span class="tag orange">🎁 free</span>';
  return '<span class="tag green">🛒 покупка</span>';
}

async function openUser(uid) {
  try {
    const d = await api('/api/user/' + uid);
    const u = d.user;
    const subsHtml = (u.subs_raw || []).map(s => {
      const src = s._source || 'purchase';
      const exp = s.expiry_date ? new Date(s.expiry_date).toLocaleString('ru-RU') : '—';
      const isActive = s._is_active;
      const cls = !isActive ? 'expired' : (src === 'admin' ? 'admin' : (src === 'free' ? 'free' : 'active'));
      const leftDays = s._days_left || 0;
      const purchasedDays = s.days || 0;
      let byLine = '';
      if (src === 'admin' && s.created_by) {
        byLine = `<div class="sub-line"><span class="lbl">Выдал админ:</span><span class="mono">#${s.created_by}</span></div>`;
      }
      if (src === 'admin' && s.created_at) {
        byLine += `<div class="sub-line"><span class="lbl">Выдано:</span><span>${new Date(s.created_at).toLocaleString('ru-RU')}</span></div>`;
      }
      const leftColor = !isActive ? '#6b7280' : (leftDays <= 3 ? '#f87171' : (leftDays <= 7 ? '#ffb84d' : '#4ade80'));
      return `<div class="sub-item ${cls}">
        <div class="src-badge">${srcBadge(src)}</div>
        <div class="sub-line"><span class="lbl">ID:</span><span class="mono">${escapeHtml(s.client_id||'—')}</span></div>
        <div class="sub-line"><span class="lbl">Срок / осталось:</span><span><b>${purchasedDays}</b> → <b style="color:${leftColor}">${leftDays}</b> дн</span></div>
        <div class="sub-line"><span class="lbl">До:</span><span>${exp} ${isActive?'<span class="tag green">активна</span>':'<span class="tag red">истекла</span>'}</span></div>
        ${byLine}
      </div>`;
    }).join('') || '<div class="muted">Нет подписок</div>';

    const daysLeftColor = u.days_left <= 3 ? '#f87171' : (u.days_left <= 7 ? '#ffb84d' : '#4ade80');

    $('#modalContent').innerHTML = `
      <h2>👤 Пользователь #${u.user_id} ${u.online_now?'<span class="tag online">● ONLINE</span>':''}</h2>
      <div class="row"><div class="k">Имя</div><div class="v">${escapeHtml(u.first_name)||'—'}</div></div>
      <div class="row"><div class="k">Username</div><div class="v">${u.username?'<a href="https://t.me/'+escapeHtml(u.username)+'" target="_blank">@'+escapeHtml(u.username)+'</a>':'—'}</div></div>
      <div class="row"><div class="k">Telegram ID</div><div class="v mono">${u.user_id}</div></div>
      <div class="row"><div class="k">VPN ID</div><div class="v mono">${u.vpn_ids.join(', ')||'—'}</div></div>
      <div class="row"><div class="k">Баланс</div><div class="v" style="color:#ffb84d;font-weight:600">${u.balance.toFixed(2)} ₽</div></div>
      <div class="row"><div class="k">Ключей (акт/истёк/всего)</div><div class="v">${u.active_keys} / ${u.expired_keys} / ${u.total_keys}</div></div>
      <div class="row"><div class="k">Источники</div><div class="v">🛒 ${u.purchase_keys} • 🎁 ${u.free_keys} • 👑 ${u.admin_keys}</div></div>
      <div class="row"><div class="k">Тариф</div><div class="v">${u.tariff}</div></div>
      <div class="row"><div class="k">Дней / куплено</div><div class="v">${u.active_keys ? `<b style="color:${daysLeftColor}">${u.days_left}</b> / ${u.days_purchased}` : '—'}</div></div>
      <div class="row"><div class="k">Устройств</div><div class="v">${u.devices_ok ? u.online_devices : '<span class="tag bang">!</span>'}</div></div>
      <div class="row"><div class="k">Админ</div><div class="v">${u.is_admin?'👑 Да':'❌ Нет'}</div></div>
      <div class="row"><div class="k">Первый вход</div><div class="v">${u.first_seen?new Date(u.first_seen).toLocaleString('ru-RU'):'—'}</div></div>
      <div class="row"><div class="k">Последний</div><div class="v">${u.last_seen?new Date(u.last_seen).toLocaleString('ru-RU'):'—'}</div></div>
      <div class="row"><div class="k">В боте</div><div class="v">${u.time_in_bot}</div></div>
      <h3>📋 Все подписки (${(u.subs_raw||[]).length})</h3>
      ${subsHtml}
      <div class="actions">
        <button class="success" onclick="actAddBalance(${u.user_id})">+ Пополнить</button>
        <button onclick="actSetBalance(${u.user_id}, ${u.balance})">= Установить</button>
        <button class="${u.is_admin?'danger':'primary'}" onclick="actAdmin(${u.user_id}, ${!u.is_admin})">${u.is_admin?'Снять админа':'Сделать админом'}</button>
        <button class="danger" onclick="actReset(${u.user_id})">Обнулить</button>
        <button onclick="closeModal()">Закрыть</button>
      </div>
    `;
    $('#modalBg').classList.add('show');
  } catch(e) { toast('Ошибка: ' + e.message, 'error'); }
}
function closeModal() { $('#modalBg').classList.remove('show'); }
$('#modalBg').addEventListener('click', e => { if (e.target.id === 'modalBg') closeModal(); });

async function doAction(payload) {
  try {
    const d = await api('/api/action', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    toast(d.message || 'Готово', 'success');
    await loadStats();
    await loadUsers();
    if (d.user) openUser(d.user);
  } catch(e) { toast('Ошибка: ' + e.message, 'error'); }
}
async function actAddBalance(uid) {
  const v = prompt('Сумма:');
  if (!v) return;
  doAction({action:'add_balance', user_id: uid, amount: parseFloat(v)});
}
async function actSetBalance(uid, cur) {
  const v = prompt('Установить баланс:', cur);
  if (v === null) return;
  doAction({action:'set_balance', user_id: uid, amount: parseFloat(v)});
}
function actAdmin(uid, make) {
  if (!confirm(make?'Сделать администратором?':'Снять?')) return;
  doAction({action:'admin', user_id: uid, value: make});
}
function actReset(uid) {
  if (!confirm('Обнулить подписки, баланс и админку?')) return;
  doAction({action:'reset', user_id: uid});
}

let ADM_KEYS_CACHE = [];
async function loadAdminKeys() {
  const tb = $('#adminKeysTable tbody');
  tb.innerHTML = '<tr><td colspan="7" class="empty"><span class="spinner"></span> Загрузка...</td></tr>';
  try {
    const d = await api('/api/admin_keys');
    ADM_KEYS_CACHE = d.keys;
    renderAdminKeys(ADM_KEYS_CACHE);
  } catch(e) { tb.innerHTML = '<tr><td colspan="7" class="empty">Ошибка: '+e.message+'</td></tr>'; }
}
function renderAdminKeys(keys) {
  const tb = $('#adminKeysTable tbody');
  if (!keys.length) { tb.innerHTML = '<tr><td colspan="7" class="empty">Админ-ключей нет</td></tr>'; return; }
  tb.innerHTML = keys.map(k => {
    const isActive = k.is_active;
    const status = isActive ? '<span class="tag green">активна</span>' : '<span class="tag red">истекла</span>';
    const leftColor = !isActive ? '#6b7280' : (k.days_left <= 3 ? '#f87171' : (k.days_left <= 7 ? '#ffb84d' : '#4ade80'));
    return `<tr>
      <td class="muted">${k.created_at_str}</td>
      <td>${k.username?'<a href="https://t.me/'+escapeHtml(k.username)+'" target="_blank">@'+escapeHtml(k.username)+'</a> ':'<span class="muted">'+escapeHtml(k.first_name||'')+'</span> '}<span class="mono">#${k.user_id}</span></td>
      <td><span class="tag purple">👑 #${k.created_by}</span></td>
      <td class="mono">${escapeHtml(k.client_id||'—')}</td>
      <td><b>${k.days}</b> → <b style="color:${leftColor}">${k.days_left}</b> дн</td>
      <td>${k.expiry_str}</td>
      <td>${status}</td>
    </tr>`;
  }).join('');
}
$('#admKeySearch').addEventListener('input', e => {
  const q = e.target.value.toLowerCase().trim();
  if (!q) return renderAdminKeys(ADM_KEYS_CACHE);
  renderAdminKeys(ADM_KEYS_CACHE.filter(k =>
    String(k.user_id).includes(q) ||
    String(k.created_by).includes(q) ||
    (k.username||'').toLowerCase().includes(q) ||
    (k.client_id||'').toLowerCase().includes(q)
  ));
});

async function loadLive() {
  try {
    const d = await api('/api/live');
    const feed = $('#liveFeed');
    const events = d.events || [];
    if (!events.length) { feed.innerHTML = '<div class="empty">Нет событий</div>'; return; }
    feed.innerHTML = events.map(e => `<div class="ev">
      <span class="time">${e.time}</span>
      <span class="uid">#${e.user_id}</span>
      ${e.username?'<span style="color:#6db3ff">@'+escapeHtml(e.username)+'</span> ':''}
      <span>${escapeHtml(e.action)}</span>
    </div>`).join('');
  } catch(e) { console.error(e); }
}

let ACT_CACHE = [];
async function loadActivity() {
  const tb = $('#actTable tbody');
  tb.innerHTML = '<tr><td colspan="4" class="empty"><span class="spinner"></span> Загрузка...</td></tr>';
  try {
    const d = await api('/api/activity');
    ACT_CACHE = d.events;
    if (!ACT_CACHE.length) { tb.innerHTML = '<tr><td colspan="4" class="empty">Нет событий</td></tr>'; return; }
    renderActivity(ACT_CACHE);
  } catch(e) { tb.innerHTML = '<tr><td colspan="4" class="empty">Ошибка: '+e.message+'</td></tr>'; }
}
function renderActivity(events) {
  const tb = $('#actTable tbody');
  if (!events.length) { tb.innerHTML = '<tr><td colspan="4" class="empty">Ничего не найдено</td></tr>'; return; }
  tb.innerHTML = events.map(e => `<tr>
    <td class="muted">${e.time}</td>
    <td class="mono">${e.user_id}</td>
    <td>${e.username?'<a href="https://t.me/'+escapeHtml(e.username)+'" target="_blank">@'+escapeHtml(e.username)+'</a>':'—'}</td>
    <td>${escapeHtml(e.action)}</td>
  </tr>`).join('');
}
$('#actSearch').addEventListener('input', e => {
  const q = e.target.value.toLowerCase().trim();
  if (!q) return renderActivity(ACT_CACHE);
  renderActivity(ACT_CACHE.filter(ev =>
    String(ev.user_id).includes(q) ||
    (ev.username||'').toLowerCase().includes(q) ||
    (ev.action||'').toLowerCase().includes(q)
  ));
});

async function loadTop() {
  try {
    const d = await api('/api/top');
    const list = d.users || [];
    if (!list.length) { $('#topUsers').innerHTML = '<div class="empty">Нет данных</div>'; return; }
    $('#topUsers').innerHTML = list.map((u, i) => `
      <div class="top-user">
        <div class="info">
          <div class="num">${i+1}</div>
          <div>
            <div>${escapeHtml(u.first_name)||'Без имени'} <span class="mono muted">#${u.user_id}</span> ${u.username?'<a href="https://t.me/'+escapeHtml(u.username)+'" target="_blank">@'+escapeHtml(u.username)+'</a>':''}</div>
            <div class="muted" style="font-size:11px">${u.top_action||''}</div>
          </div>
        </div>
        <div class="count">${u.events_count}</div>
      </div>
    `).join('');
  } catch(e) { toast('Ошибка: ' + e.message, 'error'); }
}

// ============ IP-УПРАВЛЕНИЕ ============
async function loadIPs() {
  try {
    const d = await api('/api/allowed_ips');
    $('#myip').textContent = d.my_ip || '—';
    if (!d.ips.length) { $('#ipList').innerHTML = '<div class="empty">Нет IP</div>'; return; }
    $('#ipList').innerHTML = d.ips.map(ip => `
      <div class="item">
        <span>${escapeHtml(ip)} ${ip === d.my_ip ? '<span class="tag green">ВЫ</span>' : ''}</span>
        <button class="sm danger" onclick="delIp('${escapeHtml(ip).replace(/'/g,"\\'")}')">Удалить</button>
      </div>`).join('');
  } catch(e) { toast('Ошибка загрузки IP: ' + e.message, 'error'); }
}

async function addIp() {
  const input = $('#newIp');
  const ip = (input.value || '').trim();
  if (!ip) { toast('Введите IP', 'error'); return; }
  try {
    const d = await api('/api/allowed_ips', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ip })
    });
    toast(d.message || 'IP добавлен', 'success');
    input.value = '';
    loadIPs();
  } catch(e) { toast('Ошибка: ' + e.message, 'error'); }
}

async function delIp(ip) {
  if (!confirm('Удалить IP ' + ip + '?')) return;
  try {
    const d = await api('/api/allowed_ips', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ip })
    });
    toast(d.message || 'IP удалён', 'success');
    loadIPs();
  } catch(e) { toast('Ошибка: ' + e.message, 'error'); }
}

function showIPs() {
  $$('.tab').forEach(t => t.classList.remove('active'));
  $$('.section').forEach(x => x.classList.remove('active'));
  document.querySelector('.tab[data-tab="ips"]').classList.add('active');
  $('#tab-ips').classList.add('active');
  loadIPs();
}

// init
loadDiag();
loadStats();
loadUsers();
loadIPs();
setInterval(loadStats, 30000);
setInterval(() => { if ($('#tab-live').classList.contains('active')) loadLive(); }, 10000);
</script>
</body></html>
"""


# ============================================================
#              ROUTES
# ============================================================
@app.route('/')
@ip_required
def index():
    return HTML


@app.route('/api/diag')
@ip_required
def api_diag():
    act_file = get_activity_source_file()
    return jsonify({
        'activity_file': act_file,
        'activity_count': len(get_activity_log()) if act_file else 0,
        'activity_files_searched': ACTIVITY_FILES,
        'users_file': next((p for p in USERS_FILES if os.path.exists(p)), None),
        'base_dir': BASE_DIR,
    })


@app.route('/api/stats')
@ip_required
def api_stats():
    try:
        days = int(request.args.get('days', 30))
    except Exception:
        days = 30
    all_time = (days == 0)

    users = get_all_users_dict()
    activity = get_activity_log()
    txns = get_transactions_list()
    now = datetime.now()

    total_users = len(users)
    total_admins = len(get_admins_list())

    total_keys = 0
    active_subs = 0
    total_balance = 0.0
    with_sub = 0
    buyers = set()
    free_total = admin_total = purchase_total = 0

    last_seen_map = {}
    first_seen_map = {}
    for e in activity:
        try:
            uid = int(e.get('user_id'))
        except Exception:
            continue
        ts = parse_dt(e.get('timestamp') or e.get('time') or e.get('date'))
        if not ts:
            continue
        if uid not in last_seen_map or ts > last_seen_map[uid]:
            last_seen_map[uid] = ts
        if uid not in first_seen_map or ts < first_seen_map[uid]:
            first_seen_map[uid] = ts

    for uid_str, ud in users.items():
        try:
            uid = int(uid_str)
        except Exception:
            continue
        if uid not in first_seen_map and isinstance(ud, dict):
            for k in ('registered_at', 'first_seen', 'created_at', 'join_date'):
                dt = parse_dt(ud.get(k))
                if dt:
                    first_seen_map[uid] = dt
                    break

    online_now = set()
    for uid, ts in last_seen_map.items():
        if (now - ts).total_seconds() < ONLINE_THRESHOLD_MIN * 60:
            online_now.add(uid)

    for uid_str, ud in users.items():
        if not isinstance(ud, dict):
            continue
        try:
            uid = int(uid_str)
        except Exception:
            uid = uid_str

        try:
            total_balance += float(ud.get('balance', 0) or 0)
        except Exception:
            pass

        subs = ud.get('subscriptions', []) or []
        has_active = False
        has_paid = False
        for s in subs:
            if not isinstance(s, dict):
                continue
            total_keys += 1
            src = detect_sub_source(s)
            if src == 'free':
                free_total += 1
            elif src == 'admin':
                admin_total += 1
            else:
                purchase_total += 1
            exp = parse_dt(s.get('expiry_date'))
            if exp and exp > now:
                active_subs += 1
                has_active = True
            if not s.get('is_free'):
                has_paid = True
        if has_active:
            with_sub += 1
        if has_paid:
            buyers.add(uid)

    starts_users = set()
    for e in activity:
        act = str(e.get('action') or e.get('event') or '').lower()
        if 'start' in act:
            try:
                starts_users.add(int(e.get('user_id')))
            except Exception:
                pass
    starts_count = len(starts_users)

    total_earnings = 0.0
    by_day = defaultdict(float)
    for t in txns:
        if not isinstance(t, dict):
            continue
        try:
            amt = float(t.get('amount', 0))
        except Exception:
            continue
        ttype = str(t.get('type') or '').lower()
        if amt > 0 and ttype in ('topup', 'payment', 'deposit', 'income', 'subscription'):
            total_earnings += amt
            dt = parse_dt(t.get('timestamp') or t.get('date') or t.get('time'))
            if dt:
                by_day[dt.strftime('%Y-%m-%d')] += amt

    if all_time:
        all_dates = set(by_day.keys())
        for e in activity:
            dt = parse_dt(e.get('timestamp') or e.get('time') or e.get('date'))
            if dt:
                all_dates.add(dt.strftime('%Y-%m-%d'))
        if all_dates:
            first_day = min(all_dates)
            try:
                first_dt = datetime.strptime(first_day, '%Y-%m-%d')
                total_days = min((now.date() - first_dt.date()).days + 1, 730)
            except Exception:
                total_days = 30
        else:
            total_days = 30
        start_date = now - timedelta(days=total_days - 1)
    else:
        total_days = days
        start_date = now - timedelta(days=total_days - 1)

    act_by_day = defaultdict(int)
    for e in activity:
        dt = parse_dt(e.get('timestamp') or e.get('time') or e.get('date'))
        if dt:
            act_by_day[dt.strftime('%Y-%m-%d')] += 1
    new_by_day = defaultdict(int)
    for uid, dt in first_seen_map.items():
        if dt:
            new_by_day[dt.strftime('%Y-%m-%d')] += 1

    labels, earn_values, act_values, new_values = [], [], [], []
    for i in range(total_days):
        d = start_date + timedelta(days=i)
        day = d.strftime('%Y-%m-%d')
        labels.append(d.strftime('%d.%m.%y') if total_days > 90 else d.strftime('%d.%m'))
        earn_values.append(round(by_day.get(day, 0), 2))
        act_values.append(act_by_day.get(day, 0))
        new_values.append(new_by_day.get(day, 0))

    actions_all = Counter()
    for e in activity:
        actions_all[str(e.get('action') or e.get('event') or '?')[:60]] += 1

    conversion = (len(buyers) / starts_count * 100) if starts_count else 0

    return jsonify({
        'total_users': total_users,
        'total_admins': total_admins,
        'with_sub': with_sub,
        'active_subs': active_subs,
        'total_keys': total_keys,
        'free_keys': free_total,
        'admin_keys': admin_total,
        'purchase_keys': purchase_total,
        'total_balance': round(total_balance, 2),
        'total_earnings': round(total_earnings, 2),
        'starts_count': starts_count,
        'buyers_count': len(buyers),
        'conversion': round(conversion, 1),
        'online_now': len(online_now),
        'events_total': len(activity),
        'activity_file': get_activity_source_file(),
        'period_days': total_days,
        'chart': {'labels': labels, 'values': earn_values, 'act_values': act_values, 'new_values': new_values},
        'new_users_chart': {'labels': labels, 'values': new_values},
        'top_actions': actions_all.most_common(10),
    })


@app.route('/api/users')
@ip_required
def api_users():
    users = get_all_users_dict()
    activity = get_activity_log()
    servers = get_servers_list()

    act_by_uid = defaultdict(list)
    for e in activity:
        try:
            uid = int(e.get('user_id'))
        except Exception:
            continue
        act_by_uid[uid].append(e)

    result = []
    for uid_str, ud in users.items():
        if not isinstance(ud, dict):
            continue
        try:
            uid = int(uid_str)
        except Exception:
            continue
        try:
            result.append(calc_user_stats(uid, ud, act_by_uid.get(uid, []), servers))
        except Exception as e:
            logging.error(f"calc_user_stats {uid}: {e}")

    result.sort(key=lambda x: (not x['online_now'], x['active_keys'] == 0, -x['balance'], -x['total_keys']))
    return jsonify({'users': result})


@app.route('/api/user/<int:uid>')
@ip_required
def api_user(uid):
    try:
        ud = get_user(uid) or {}
        if not ud:
            ud = get_all_users_dict().get(str(uid), {})
        activity = get_activity_log()
        act_user = []
        for e in activity:
            try:
                if int(e.get('user_id', -1)) == uid:
                    act_user.append(e)
            except Exception:
                pass
        st = calc_user_stats(uid, ud, act_user, get_servers_list())
        return jsonify({'user': st})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin_keys')
@ip_required
def api_admin_keys():
    users = get_all_users_dict()
    now = datetime.now()
    result = []
    for uid_str, ud in users.items():
        if not isinstance(ud, dict):
            continue
        try:
            uid = int(uid_str)
        except Exception:
            continue
        subs = ud.get('subscriptions', []) or []
        for s in subs:
            if not isinstance(s, dict):
                continue
            if detect_sub_source(s) != 'admin':
                continue
            expiry = parse_dt(s.get('expiry_date'))
            is_active = bool(expiry and expiry > now)
            result.append({
                'user_id': uid,
                'username': ud.get('username') or '',
                'first_name': ud.get('first_name') or '',
                'created_by': int(s.get('created_by') or 0),
                'created_at_str': fmt_dt(s.get('created_at') or s.get('purchase_date')),
                'client_id': s.get('client_id'),
                'days': int(s.get('days', 0) or 0),
                'days_left': days_until(expiry, now) if is_active else 0,
                'expiry_str': fmt_dt(s.get('expiry_date')),
                'is_active': is_active,
            })
    result.sort(key=lambda k: (
        parse_dt(k.get('created_at_str')).timestamp() if parse_dt(k.get('created_at_str')) else 0
    ), reverse=True)
    return jsonify({'keys': result})


@app.route('/api/activity')
@ip_required
def api_activity():
    users = get_all_users_dict()
    activity = get_activity_log()

    def ts_key(e):
        dt = parse_dt(e.get('timestamp') or e.get('time') or e.get('date'))
        return dt.timestamp() if dt else 0

    activity = sorted(activity, key=ts_key, reverse=True)[:1000]
    events = []
    for e in activity:
        try:
            uid = int(e.get('user_id'))
        except Exception:
            uid = e.get('user_id', '?')
        ud = users.get(str(uid), {}) if isinstance(uid, int) else {}
        events.append({
            'time': fmt_dt(e.get('timestamp') or e.get('time') or e.get('date')),
            'user_id': uid,
            'username': ud.get('username', '') if isinstance(ud, dict) else '',
            'action': str(e.get('action') or e.get('event') or '?')[:200],
        })
    return jsonify({'events': events})


@app.route('/api/live')
@ip_required
def api_live():
    users = get_all_users_dict()
    activity = get_activity_log()

    def ts_key(e):
        dt = parse_dt(e.get('timestamp') or e.get('time') or e.get('date'))
        return dt.timestamp() if dt else 0

    activity = sorted(activity, key=ts_key, reverse=True)[:30]
    out = []
    for e in activity:
        try:
            uid = int(e.get('user_id'))
        except Exception:
            uid = e.get('user_id', '?')
        ud = users.get(str(uid), {}) if isinstance(uid, int) else {}
        out.append({
            'time': fmt_dt(e.get('timestamp') or e.get('time') or e.get('date')),
            'user_id': uid,
            'username': ud.get('username', '') if isinstance(ud, dict) else '',
            'action': str(e.get('action') or e.get('event') or '?')[:200],
        })
    return jsonify({'events': out})


@app.route('/api/top')
@ip_required
def api_top():
    users = get_all_users_dict()
    activity = get_activity_log()
    cnt = Counter()
    for e in activity:
        try:
            cnt[int(e.get('user_id'))] += 1
        except Exception:
            continue
    result = []
    for uid, c in cnt.most_common(20):
        ud = users.get(str(uid), {}) if isinstance(users, dict) else {}
        actions = Counter()
        for e in activity:
            try:
                if int(e.get('user_id', -1)) == uid:
                    actions[str(e.get('action') or e.get('event') or '?')[:60]] += 1
            except Exception:
                pass
        result.append({
            'user_id': uid,
            'first_name': (ud.get('first_name') if isinstance(ud, dict) else '') or '',
            'username': (ud.get('username') if isinstance(ud, dict) else '') or '',
            'events_count': c,
            'top_action': f"топ: {actions.most_common(1)[0][0]}" if actions else '',
        })
    return jsonify({'users': result})


# ============ IP-УПРАВЛЕНИЕ ============
@app.route('/api/allowed_ips', methods=['GET', 'POST', 'DELETE', 'OPTIONS'])
@ip_required
def api_allowed_ips():
    if request.method == 'OPTIONS':
        return ('', 204)

    if request.method == 'GET':
        return jsonify({
            'ips': load_allowed_ips(),
            'my_ip': get_client_ip(),
            'file': IP_CONFIG_FILE,
            'file_exists': os.path.exists(IP_CONFIG_FILE),
        })

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        ip = str(data.get('ip', '')).strip()
        logging.info(f"[WEB] POST allowed_ips ip={ip!r}")

        if not ip:
            return jsonify({'error': 'IP пустой'}), 400

        # Разрешаем * (wildcard) и IPv4/IPv6
        if ip != '*' and not re.match(r'^[0-9a-fA-F:.]{3,45}$', ip):
            return jsonify({'error': f'Некорректный IP: {ip}'}), 400

        ips = load_allowed_ips()
        if ip in ips:
            return jsonify({'error': 'Уже добавлен'}), 400

        ips.append(ip)
        try:
            save_allowed_ips(ips)
        except Exception as e:
            logging.error(f"save_allowed_ips: {e}")
            return jsonify({'error': f'Не удалось сохранить: {e}'}), 500

        logging.info(f"✅ Добавлен IP: {ip}")
        return jsonify({'message': f'IP {ip} добавлен', 'ips': ips})

    if request.method == 'DELETE':
        data = request.get_json(silent=True) or {}
        ip = str(data.get('ip', '')).strip()
        logging.info(f"[WEB] DELETE allowed_ips ip={ip!r}")

        ips = load_allowed_ips()
        if ip not in ips:
            return jsonify({'error': 'Не найден'}), 404
        if ip == get_client_ip():
            return jsonify({'error': 'Нельзя удалить свой текущий IP'}), 400

        ips.remove(ip)
        try:
            save_allowed_ips(ips)
        except Exception as e:
            logging.error(f"save_allowed_ips: {e}")
            return jsonify({'error': f'Не удалось сохранить: {e}'}), 500

        logging.info(f"✅ Удалён IP: {ip}")
        return jsonify({'message': f'IP {ip} удалён', 'ips': ips})

    return jsonify({'error': 'Метод не поддерживается'}), 405


# ============ ACTIONS ============
@app.route('/api/action', methods=['POST'])
@ip_required
def api_action():
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    uid = data.get('user_id')
    if action is None or uid is None:
        return jsonify({'error': 'Нет action/user_id'}), 400
    try:
        uid = int(uid)
    except Exception:
        return jsonify({'error': 'Некорректный user_id'}), 400

    ud = get_user(uid) or {}
    if not isinstance(ud, dict):
        ud = {}

    if action == 'add_balance':
        try:
            amount = float(data.get('amount', 0))
        except Exception:
            return jsonify({'error': 'Некорректная сумма'}), 400
        ud['balance'] = float(ud.get('balance', 0) or 0) + amount
        save_user(uid, ud)
        logging.info(f"[WEB] +{amount}₽ → {uid}")
        return jsonify({'message': f'Баланс изменён на {amount:+.2f}₽', 'user': uid})

    if action == 'set_balance':
        try:
            amount = float(data.get('amount', 0))
        except Exception:
            return jsonify({'error': 'Некорректная сумма'}), 400
        ud['balance'] = amount
        save_user(uid, ud)
        logging.info(f"[WEB] ={amount}₽ → {uid}")
        return jsonify({'message': f'Баланс установлен на {amount:.2f}₽', 'user': uid})

    if action == 'admin':
        make = bool(data.get('value'))
        admins = get_admins_list()
        try:
            admins_int = [int(x) for x in admins]
        except Exception:
            admins_int = []

        if make and uid not in admins_int:
            try:
                add_admin(uid)
            except Exception:
                admins.append(uid)
                with open(ADMINS_FILES[0], 'w', encoding='utf-8') as f:
                    json.dump(admins, f, ensure_ascii=False, indent=2)
            logging.info(f"[WEB] 👑 +admin {uid}")
            return jsonify({'message': f'{uid} теперь администратор', 'user': uid})

        if not make and uid in admins_int:
            try:
                admins = [a for a in admins if int(a) != uid]
                with open(ADMINS_FILES[0], 'w', encoding='utf-8') as f:
                    json.dump(admins, f, ensure_ascii=False, indent=2)
            except Exception as e:
                return jsonify({'error': f'Не удалось снять: {e}'}), 500
            logging.info(f"[WEB] -admin {uid}")
            return jsonify({'message': f'Права администратора сняты с {uid}', 'user': uid})

        return jsonify({'message': 'Без изменений', 'user': uid})

    if action == 'reset':
        ud['balance'] = 0
        ud['subscriptions'] = []
        ud['got_free'] = False
        save_user(uid, ud)
        admins = get_admins_list()
        try:
            admins_int = [int(x) for x in admins]
        except Exception:
            admins_int = []
        if uid in admins_int:
            try:
                admins = [a for a in admins if int(a) != uid]
                with open(ADMINS_FILES[0], 'w', encoding='utf-8') as f:
                    json.dump(admins, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        logging.info(f"[WEB] Reset user {uid}")
        return jsonify({'message': f'Пользователь {uid} обнулён', 'user': uid})

    return jsonify({'error': 'Неизвестное действие'}), 400


# ============================================================
#              ЗАПУСК
# ============================================================
if __name__ == '__main__':
    ips = load_allowed_ips()
    print("=" * 60)
    print(f"🖥️  DubikVPN Web Panel v5")
    print(f"📁 BASE_DIR: {BASE_DIR}")
    print(f"🌐 Адрес: http://{PANEL_PUBLIC_IP}:{PANEL_PORT}")
    print(f"🔒 IP ({len(ips)}): {', '.join(ips)}")
    act = get_activity_source_file()
    print(f"📋 Активность: {act or '⚠️ НЕ НАЙДЕНА'}")
    print("=" * 60)
    app.run(host=PANEL_HOST, port=PANEL_PORT, debug=False)
