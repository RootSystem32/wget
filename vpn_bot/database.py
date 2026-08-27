# database.py
import json
import os
import secrets
import string
from datetime import datetime, timedelta

def load_data(file_path, default=None):
    if default is None:
        default = []
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return default
    return default

def save_data(file_path, data):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ========== ГЛОБАЛЬНЫЙ СЧЁТЧИК ==========
COUNTER_FILE = "counter.json"

def get_counter():
    if os.path.exists(COUNTER_FILE):
        try:
            with open(COUNTER_FILE, 'r') as f:
                data = json.load(f)
                return data.get('last_client_number', 10019)
        except:
            return 10019
    return 10019

def increment_counter():
    current = get_counter()
    new_value = current + 1
    with open(COUNTER_FILE, 'w') as f:
        json.dump({'last_client_number': new_value}, f)
    return new_value

def get_next_client_number():
    return increment_counter()

# ========== Пользователи ==========
def get_users():
    users = load_data("users.json", {})
    if 'None' in users:
        del users['None']
        save_data("users.json", users)
    return users

def get_user(user_id):
    users = get_users()
    return users.get(str(user_id), {})

def save_user(user_id, user_data):
    if user_id is None:
        return
    users = get_users()
    users[str(user_id)] = user_data
    save_data("users.json", users)

def get_all_users():
    users = get_users()
    result = []
    for user_id in users.keys():
        try:
            if user_id != 'None' and user_id is not None:
                result.append(int(user_id))
        except (ValueError, TypeError):
            continue
    return result

# ========== Администраторы ==========
def get_admins():
    admins = load_data("admins.json", [])
    from config import MAIN_ADMIN_ID
    if MAIN_ADMIN_ID not in admins:
        admins.append(MAIN_ADMIN_ID)
        save_data("admins.json", admins)
    return admins

def add_admin(user_id):
    admins = get_admins()
    if user_id not in admins:
        admins.append(user_id)
        save_data("admins.json", admins)
        return True
    return False

def remove_admin(user_id):
    from config import MAIN_ADMIN_ID
    if user_id == MAIN_ADMIN_ID:
        return False
    admins = get_admins()
    if user_id in admins:
        admins.remove(user_id)
        save_data("admins.json", admins)
        return True
    return False

def is_admin(user_id):
    return user_id in get_admins()

# ========== Серверы ==========
def get_servers():
    return load_data("servers.json", [])

def save_servers(servers):
    save_data("servers.json", servers)

def add_server(server):
    servers = get_servers()
    max_id = max([s.get('id', 0) for s in servers]) if servers else 0
    server['id'] = max_id + 1
    server['used_slots'] = 0
    servers.append(server)
    save_servers(servers)
    return server

def delete_server(server_id):
    servers = get_servers()
    servers = [s for s in servers if s.get('id') != server_id]
    save_servers(servers)

def get_server_by_id(server_id):
    servers = get_servers()
    for s in servers:
        if s.get('id') == server_id:
            return s
    return None

def get_server_used_slots(server_id):
    users = get_users()
    used = 0
    now = datetime.now()
    for user_data in users.values():
        for sub in user_data.get('subscriptions', []):
            if sub.get('server_id') == server_id:
                try:
                    expiry = datetime.fromisoformat(sub['expiry_date'])
                    if expiry > now:
                        used += 1
                except:
                    pass
    return used

def get_available_slots(server_id):
    server = get_server_by_id(server_id)
    if not server:
        return 0
    max_slots = server.get('max_slots')
    if max_slots is None or max_slots == 0:
        return None
    used = get_server_used_slots(server_id)
    return max_slots - used

def is_slot_available(server_id):
    server = get_server_by_id(server_id)
    if not server:
        return False
    max_slots = server.get('max_slots')
    if max_slots is None or max_slots == 0:
        return True
    used = get_server_used_slots(server_id)
    return used < max_slots

def update_server_used_slots(server_id):
    servers = get_servers()
    for s in servers:
        if s.get('id') == server_id:
            s['used_slots'] = get_server_used_slots(server_id)
            save_servers(servers)
            return True
    return False

# ========== Ожидающие платежи за подписки ==========
def get_pending():
    return load_data("pending.json", {})

def add_pending(user_id, order_data):
    pending = get_pending()
    pending[str(user_id)] = order_data
    save_data("pending.json", pending)

def get_pending_order(user_id):
    pending = get_pending()
    return pending.get(str(user_id))

def remove_pending(user_id):
    pending = get_pending()
    if str(user_id) in pending:
        del pending[str(user_id)]
        save_data("pending.json", pending)

# ========== Ожидающие пополнения баланса ==========
def get_pending_topups():
    return load_data("pending_topups.json", {})

def add_pending_topup(user_id, amount):
    pending = get_pending_topups()
    pending[str(user_id)] = {'amount': amount, 'date': datetime.now().isoformat()}
    save_data("pending_topups.json", pending)

def get_pending_topup(user_id):
    pending = get_pending_topups()
    return pending.get(str(user_id))

def remove_pending_topup(user_id):
    pending = get_pending_topups()
    if str(user_id) in pending:
        del pending[str(user_id)]
        save_data("pending_topups.json", pending)

# ========== Транзакции ==========
def get_transactions():
    return load_data("transactions.json", [])

def add_transaction(user_id, amount, trans_type, description=""):
    transactions = get_transactions()
    transactions.append({
        'user_id': user_id,
        'amount': amount,
        'type': trans_type,
        'description': description,
        'date': datetime.now().isoformat()
    })
    save_data("transactions.json", transactions)

def get_user_transactions(user_id):
    transactions = get_transactions()
    return [t for t in transactions if t['user_id'] == user_id]

# ========== Активность пользователей ==========
def log_user_activity(user_id, action):
    if user_id is None:
        return
    user_data = get_user(user_id)
    if 'first_seen' not in user_data:
        user_data['first_seen'] = datetime.now().isoformat()
    user_data['last_active'] = datetime.now().isoformat()
    if 'activity' not in user_data:
        user_data['activity'] = []
    user_data['activity'].append({
        'action': action,
        'timestamp': datetime.now().isoformat()
    })
    if len(user_data['activity']) > 100:
        user_data['activity'] = user_data['activity'][-100:]
    save_user(user_id, user_data)

def get_user_activity(user_id):
    user_data = get_user(user_id)
    return user_data.get('activity', [])

def get_user_stats(user_id):
    activity = get_user_activity(user_id)
    user_data = get_user(user_id)
    actions = {}
    for act in activity:
        action = act['action']
        actions[action] = actions.get(action, 0) + 1
    return {
        'first_seen': user_data.get('first_seen'),
        'last_active': user_data.get('last_active'),
        'total_actions': len(activity),
        'action_types': actions,
        'balance': user_data.get('balance', 0),
        'subscriptions': len(user_data.get('subscriptions', []))
    }

# ========== Автопродление ==========
def auto_renew_subscriptions():
    users = get_users()
    now = datetime.now()
    renewed = []
    from config import PRICES
    for uid_str, user_data in users.items():
        try:
            uid = int(uid_str)
        except (ValueError, TypeError):
            continue
        try:
            balance = user_data.get('balance', 0)
            for sub in user_data.get('subscriptions', []):
                expiry = datetime.fromisoformat(sub['expiry_date'])
                days_left = (expiry - now).days
                if 0 <= days_left <= 2 and not sub.get('auto_renewed', False):
                    price = PRICES.get(sub.get('days', 30), 150)
                    if balance >= price:
                        user_data['balance'] = balance - price
                        from panel_api import create_subscription
                        result = create_subscription(None, None, sub.get('days', 30), f"User {uid}")
                        if result['success']:
                            sub['expiry_date'] = datetime.fromtimestamp(result['expiry_date']/1000).isoformat()
                            sub['auto_renewed'] = True
                            sub['sub_link'] = result['sub_link']
                            sub['client_id'] = result['client_id']
                            sub['client_number'] = result.get('client_number')
                            sub['servers'] = result.get('servers', [])
                            sub['servers_count'] = result.get('servers_count', 1)
                            add_transaction(uid, -price, 'subscription', f'Автопродление на {sub.get("days", 30)} дней')
                            renewed.append(uid)
                            save_user(uid, user_data)
                            for server in get_servers():
                                update_server_used_slots(server['id'])
        except Exception as e:
            print(f"Ошибка автопродления для {uid_str}: {e}")
    return renewed

# ========== Генерация ID ==========
def get_next_client_number():
    return increment_counter()

def get_next_client_id():
    return get_next_client_number()

# ========== Аналитика ==========
def get_user_analytics(user_id):
    user_data = get_user(user_id)
    transactions = get_user_transactions(user_id)
    total_spent = sum(t['amount'] for t in transactions if t['type'] == 'subscription')
    total_topups = sum(t['amount'] for t in transactions if t['type'] == 'topup')
    if total_spent == float('inf') or total_spent == float('-inf'):
        total_spent = 0
    if total_topups == float('inf') or total_topups == float('-inf'):
        total_topups = 0
    first_seen = user_data.get('first_seen')
    last_active = user_data.get('last_active')
    subs = user_data.get('subscriptions', [])
    active_subs = [s for s in subs if datetime.fromisoformat(s['expiry_date']) > datetime.now()]
    return {
        'user_id': user_id,
        'balance': user_data.get('balance', 0),
        'total_spent': -total_spent,
        'total_topups': total_topups,
        'first_seen': first_seen,
        'last_active': last_active,
        'total_subs': len(subs),
        'active_subs': len(active_subs),
        'username': user_data.get('username', ''),
        'first_name': user_data.get('first_name', '')
    }

def get_system_stats():
    users = get_users()
    total_users = len(users)
    active_users = 0
    total_balance = 0
    total_spent = 0
    total_starts = 0
    total_purchases = 0
    total_devices = 0
    users_with_subs = 0
    
    now = datetime.now()
    
    for uid_str, user_data in users.items():
        try:
            uid = int(uid_str)
        except (ValueError, TypeError):
            continue
        total_balance += user_data.get('balance', 0)
        if user_data.get('last_active'):
            last_active = datetime.fromisoformat(user_data['last_active'])
            if (now - last_active).days < 7:
                active_users += 1
        transactions = get_user_transactions(uid)
        total_spent += sum(t['amount'] for t in transactions if t['type'] == 'subscription')
        activity = user_data.get('activity', [])
        starts = [a for a in activity if a.get('action') == 'start']
        total_starts += len(starts)
        subs = user_data.get('subscriptions', [])
        paid_subs = [s for s in subs if not s.get('is_free', False)]
        total_purchases += len(paid_subs)
        if subs:
            users_with_subs += 1
        active_subs = [s for s in subs if datetime.fromisoformat(s['expiry_date']) > now]
        total_devices += len(active_subs)
    total_active_subs = 0
    for user_data in users.values():
        for sub in user_data.get('subscriptions', []):
            if datetime.fromisoformat(sub['expiry_date']) > now:
                total_active_subs += 1
    if total_spent == float('inf') or total_spent == float('-inf'):
        total_spent = 0
    return {
        'total_users': total_users,
        'active_users': active_users,
        'total_balance': total_balance,
        'total_spent': -total_spent,
        'total_active_subs': total_active_subs,
        'total_starts': total_starts,
        'total_purchases': total_purchases,
        'total_devices': total_devices,
        'users_with_subs': users_with_subs
    }

def get_users_list():
    users = get_users()
    result = []
    now = datetime.now()
    for uid_str, user_data in users.items():
        try:
            uid = int(uid_str)
        except (ValueError, TypeError):
            continue
        subs = user_data.get('subscriptions', [])
        active_subs = [s for s in subs if datetime.fromisoformat(s['expiry_date']) > now]
        days_left = 0
        if active_subs:
            nearest_expiry = min([datetime.fromisoformat(s['expiry_date']) for s in active_subs])
            days_left = (nearest_expiry - now).days
        transactions = get_user_transactions(uid)
        total_spent = sum(t['amount'] for t in transactions if t['type'] == 'subscription')
        total_topups = sum(t['amount'] for t in transactions if t['type'] == 'topup')
        if total_spent == float('inf') or total_spent == float('-inf'):
            total_spent = 0
        if total_topups == float('inf') or total_topups == float('-inf'):
            total_topups = 0
        activity = user_data.get('activity', [])
        first_seen = user_data.get('first_seen')
        last_active = user_data.get('last_active')
        purchases = len([s for s in subs if not s.get('is_free', False)])
        result.append({
            'id': uid,
            'username': user_data.get('username', ''),
            'first_name': user_data.get('first_name', ''),
            'balance': user_data.get('balance', 0),
            'first_seen': first_seen,
            'last_active': last_active,
            'total_subs': len(subs),
            'active_subs': len(active_subs),
            'expired_subs': len(subs) - len(active_subs),
            'purchases': purchases,
            'total_spent': -total_spent,
            'total_topups': total_topups,
            'got_free': user_data.get('got_free', False),
            'activity_count': len(activity),
            'days_left': days_left,
            'devices_count': len(active_subs),
            'devices_warning': len(active_subs) > 3
        })
    result.sort(key=lambda x: x['last_active'] or '', reverse=True)
    return result

# ========== Бесплатная подписка ==========
def has_free_sub(user_id):
    user_data = get_user(user_id)
    return user_data.get('got_free', False)

def mark_free_used(user_id):
    user_data = get_user(user_id)
    user_data['got_free'] = True
    save_user(user_id, user_data)

# ========== Бэкап ==========
def create_backup():
    backup_dir = "backups"
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{backup_dir}/backup_{timestamp}.zip"
    import zipfile
    with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in ["users.json", "servers.json", "admins.json", "pending.json", "pending_topups.json", "transactions.json", "payment_methods.json", "pending_withdrawals.json", "counter.json"]:
            if os.path.exists(file):
                zipf.write(file)
    return backup_file, timestamp

def get_backup_list():
    backup_dir = "backups"
    if not os.path.exists(backup_dir):
        return []
    backups = []
    for file in os.listdir(backup_dir):
        if file.endswith(".zip"):
            stat = os.stat(os.path.join(backup_dir, file))
            backups.append({'name': file, 'size': stat.st_size, 'modified': stat.st_mtime})
    backups.sort(key=lambda x: x['modified'], reverse=True)
    return backups

def restore_backup(backup_name):
    backup_path = f"backups/{backup_name}"
    if not os.path.exists(backup_path):
        return False, "Файл бэкапа не найден"
    import zipfile
    try:
        with zipfile.ZipFile(backup_path, 'r') as zipf:
            zipf.extractall(".")
        return True, "Бэкап восстановлен"
    except Exception as e:
        return False, str(e)

# ========== Поиск пользователя ==========
def search_user_by_id(search_query):
    users = get_users()
    search_query = str(search_query)
    if search_query in users:
        return search_query, users[search_query]
    for user_id, user_data in users.items():
        for sub in user_data.get('subscriptions', []):
            if str(sub.get('client_id')) == search_query:
                return user_id, user_data
            if search_query in sub.get('sub_link', ''):
                return user_id, user_data
    return None, None

# ========== ПРОВЕРКА ПОДПИСКИ НА КАНАЛ ==========
def is_user_verified(user_id):
    """Проверяет, прошел ли пользователь проверку подписки"""
    user_data = get_user(user_id)
    return user_data.get('verified', False)

def set_user_verified(user_id):
    """Отмечает пользователя как прошедшего проверку"""
    user_data = get_user(user_id)
    user_data['verified'] = True
    save_user(user_id, user_data)

# ========== АЛИАСЫ ДЛЯ stats_web.py ==========
db_get_user = get_user
db_get_user_transactions = get_user_transactions
db_get_user_activity = get_user_activity
db_get_system_stats = get_system_stats
db_get_users_list = get_users_list
