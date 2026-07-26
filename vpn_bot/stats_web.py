# stats_web.py
from flask import Flask, render_template, jsonify, request, redirect, url_for
import json
import os
import secrets
import re
from datetime import datetime, timedelta

# Импортируем функции из database.py
from database import (
    get_users,
    get_user,
    get_user_transactions,
    get_user_activity,
    get_system_stats,
    get_users_list,
    save_user,
    add_transaction,
    load_data,
    save_data
)

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

ALLOWED_IPS_FILE = "allowed_ips.json"
DEFAULT_IP = "176.59.132.127"

# ========== IP ==========
def load_allowed_ips():
    if os.path.exists(ALLOWED_IPS_FILE):
        try:
            with open(ALLOWED_IPS_FILE, 'r') as f:
                return json.load(f)
        except:
            default_ips = [DEFAULT_IP]
            save_allowed_ips(default_ips)
            return default_ips
    default_ips = [DEFAULT_IP]
    save_allowed_ips(default_ips)
    return default_ips

def save_allowed_ips(ips):
    with open(ALLOWED_IPS_FILE, 'w') as f:
        json.dump(ips, f, indent=2)

def ip_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        client_ip = request.headers.get('X-Real-IP') or request.headers.get('X-Forwarded-For') or request.remote_addr
        if ',' in client_ip:
            client_ip = client_ip.split(',')[0].strip()
        allowed = load_allowed_ips()
        if client_ip not in allowed:
            return f"⛔ Доступ запрещён. Ваш IP: {client_ip}", 403
        return f(*args, **kwargs)
    return decorated_function

# ========== ГЛАВНАЯ ==========
@app.route('/')
@ip_required
def stats_index():
    return render_template('stats.html')

# ========== API: СТАТИСТИКА ==========
@app.route('/api/stats')
@ip_required
def api_stats():
    try:
        stats = get_system_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== API: ПОЛЬЗОВАТЕЛИ ==========
@app.route('/api/users')
@ip_required
def api_users():
    try:
        users = get_users_list()
        return jsonify(users)
    except Exception as e:
        print(f"API Users Error: {e}")
        return jsonify({'error': str(e)}), 500

# ========== API: ПОЛЬЗОВАТЕЛЬ ==========
@app.route('/api/user/<int:user_id>')
@ip_required
def api_user(user_id):
    try:
        user_data = get_user(user_id)
        if not user_data:
            return jsonify({'error': 'User not found'}), 404
        
        transactions = get_user_transactions(user_id)
        activity = get_user_activity(user_id)
        subs = user_data.get('subscriptions', [])
        now = datetime.now()
        
        active_subs = [s for s in subs if datetime.fromisoformat(s['expiry_date']) > now]
        
        return jsonify({
            'user': {
                'id': user_id,
                'username': user_data.get('username', ''),
                'first_name': user_data.get('first_name', ''),
                'balance': user_data.get('balance', 0),
                'first_seen': user_data.get('first_seen'),
                'last_active': user_data.get('last_active'),
                'got_free': user_data.get('got_free', False),
                'traffic_used': 0,
                'traffic_limit': 1000
            },
            'subscriptions': {
                'active': active_subs,
                'expired': [],
                'total': len(subs)
            },
            'transactions': transactions[-20:],
            'activity': activity[-20:],
            'stats': {
                'total_actions': len(activity),
                'devices_count': len(active_subs),
                'devices_warning': len(active_subs) > 3
            }
        })
    except Exception as e:
        print(f"API User Error: {e}")
        return jsonify({'error': str(e)}), 500

# ========== API: ПРЕВЫШЕНИЕ УСТРОЙСТВ ==========
@app.route('/api/devices_warning')
@ip_required
def api_devices_warning():
    try:
        users = get_users()
        now = datetime.now()
        warnings = []
        for uid_str, user_data in users.items():
            uid = int(uid_str)
            subs = user_data.get('subscriptions', [])
            active_subs = [s for s in subs if datetime.fromisoformat(s['expiry_date']) > now]
            if len(active_subs) > 3:
                warnings.append({
                    'user_id': uid,
                    'username': user_data.get('username', ''),
                    'first_name': user_data.get('first_name', ''),
                    'devices': len(active_subs),
                    'limit': 3
                })
        return jsonify(warnings)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== API: ГРАФИКИ ==========
@app.route('/api/charts')
@ip_required
def api_charts():
    try:
        users = get_users()
        users_by_day = {}
        purchases_by_day = {}
        revenue_by_day = {}
        
        for user_data in users.values():
            if user_data.get('first_seen'):
                date = datetime.fromisoformat(user_data['first_seen']).strftime('%Y-%m-%d')
                users_by_day[date] = users_by_day.get(date, 0) + 1
            for sub in user_data.get('subscriptions', []):
                if not sub.get('is_free', False):
                    date = datetime.fromisoformat(sub['purchase_date']).strftime('%Y-%m-%d')
                    purchases_by_day[date] = purchases_by_day.get(date, 0) + 1
                    revenue_by_day[date] = revenue_by_day.get(date, 0) + 150
        
        dates = sorted(set(list(users_by_day.keys()) + list(purchases_by_day.keys())))
        return jsonify({
            'dates': dates,
            'users_by_day': [users_by_day.get(d, 0) for d in dates],
            'purchases_by_day': [purchases_by_day.get(d, 0) for d in dates],
            'revenue_by_day': [revenue_by_day.get(d, 0) for d in dates]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== API: ДОБАВИТЬ БАЛАНС ==========
@app.route('/api/user/<int:user_id>/add_balance', methods=['POST'])
@ip_required
def api_add_balance(user_id):
    try:
        data = request.json
        amount = float(data.get('amount', 0))
        if amount <= 0:
            return jsonify({'error': 'Amount must be positive'}), 400
        user_data = get_user(user_id)
        if not user_data:
            return jsonify({'error': 'User not found'}), 404
        user_data['balance'] = user_data.get('balance', 0) + amount
        save_user(user_id, user_data)
        add_transaction(user_id, amount, 'topup', f'Администратор начислил {amount}₽')
        return jsonify({'success': True, 'new_balance': user_data['balance']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== API: УДАЛИТЬ ПОДПИСКУ ==========
@app.route('/api/user/<int:user_id>/delete_sub/<int:sub_index>', methods=['POST'])
@ip_required
def api_delete_subscription(user_id, sub_index):
    try:
        user_data = get_user(user_id)
        if not user_data:
            return jsonify({'error': 'User not found'}), 404
        subs = user_data.get('subscriptions', [])
        if sub_index < 0 or sub_index >= len(subs):
            return jsonify({'error': 'Subscription not found'}), 404
        subs.pop(sub_index)
        user_data['subscriptions'] = subs
        save_user(user_id, user_data)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== API: УДАЛИТЬ ПОЛЬЗОВАТЕЛЯ ==========
@app.route('/api/user/<int:user_id>/delete', methods=['POST'])
@ip_required
def api_delete_user(user_id):
    try:
        users = get_users()
        if str(user_id) not in users:
            return jsonify({'error': 'User not found'}), 404
        del users[str(user_id)]
        save_data("users.json", users)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== АДМИНКА IP ==========
@app.route('/admin')
@ip_required
def admin_panel():
    ips = load_allowed_ips()
    return render_template('admin.html', ips=ips)

@app.route('/admin/add', methods=['POST'])
@ip_required
def add_ip():
    new_ip = request.form.get('ip')
    if not new_ip:
        return "IP не указан", 400
    parts = new_ip.split('.')
    if len(parts) != 4:
        return "Неверный формат IP", 400
    for p in parts:
        if not p.isdigit() or int(p) < 0 or int(p) > 255:
            return "Неверный формат IP", 400
    ips = load_allowed_ips()
    if new_ip not in ips:
        ips.append(new_ip)
        save_allowed_ips(ips)
    return redirect(url_for('admin_panel'))

@app.route('/admin/remove/<ip>', methods=['POST'])
@ip_required
def remove_ip(ip):
    ips = load_allowed_ips()
    if ip in ips:
        if len(ips) <= 1:
            return "Нельзя удалить последний IP", 400
        ips.remove(ip)
        save_allowed_ips(ips)
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8444, debug=False)
