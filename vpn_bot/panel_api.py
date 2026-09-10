# panel_api.py
import requests
import uuid
import secrets
import string
import random
from datetime import datetime, timedelta
from urllib3.exceptions import InsecureRequestWarning
from config import SUBSCRIPTION_PATH, MAX_DEVICES
from database import get_next_client_number, get_servers, get_user, save_user

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

def generate_sub_id(length=8):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

# ========== HWID ФУНКЦИИ ==========

def get_client_hwids(server, client_email):
    """
    Получение списка зарегистрированных HWID устройств клиента
    POST /panel/api/clients/hwids/{email}
    """
    try:
        base_url = server['url'].rstrip('/')
        api_token = server['api_token']
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json"
        }
        response = requests.post(
            f"{base_url}/panel/api/clients/hwids/{client_email}",
            headers=headers,
            verify=False,
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                return data.get('obj', [])
        return []
    except Exception as e:
        return []

def get_hwid_count(server, client_email):
    """Получение количества HWID устройств"""
    hwids = get_client_hwids(server, client_email)
    return len(hwids)

def clear_all_hwids(server, client_email):
    """
    Очистка всех HWID устройств клиента
    DELETE /panel/api/clients/hwids/{email}
    """
    try:
        base_url = server['url'].rstrip('/')
        api_token = server['api_token']
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json"
        }
        response = requests.delete(
            f"{base_url}/panel/api/clients/hwids/{client_email}",
            headers=headers,
            verify=False,
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            return data.get('success', False)
        return False
    except Exception as e:
        return False

def remove_hwid_device(server, client_email, device_id):
    """
    Удаление одного HWID устройства
    DELETE /panel/api/clients/hwids/{email}/{id}
    """
    try:
        base_url = server['url'].rstrip('/')
        api_token = server['api_token']
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json"
        }
        response = requests.delete(
            f"{base_url}/panel/api/clients/hwids/{client_email}/{device_id}",
            headers=headers,
            verify=False,
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            return data.get('success', False)
        return False
    except Exception as e:
        return False

# ========== КЛИЕНТ ФУНКЦИИ ==========

def get_client_info(server, client_uuid):
    """Получение информации о клиенте с панели"""
    try:
        base_url = server['url'].rstrip('/')
        api_token = server['api_token']
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json"
        }
        response = requests.get(
            f"{base_url}/panel/api/clients/get/{client_uuid}",
            headers=headers,
            verify=False,
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                return data.get('obj', {})
        return None
    except Exception as e:
        return None

def get_client_usage(server, client_uuid, client_email=None):
    """Получение информации об использовании клиента (HWID)"""
    try:
        client_info = get_client_info(server, client_uuid)
        
        hwid_count = 0
        if client_email:
            hwid_count = get_hwid_count(server, client_email)
        
        if client_info:
            return {
                'online': hwid_count,
                'hwid': hwid_count,
                'limitIp': client_info.get('limitIp', MAX_DEVICES),
                'totalGB': client_info.get('totalGB', 0),
                'usedGB': client_info.get('usedGB', 0),
                'expiryTime': client_info.get('expiryTime', 0),
                'enable': client_info.get('enable', True)
            }
        return None
    except Exception as e:
        return None

def update_client_status(server, client_uuid, enable):
    """Включение/выключение клиента"""
    try:
        base_url = server['url'].rstrip('/')
        api_token = server['api_token']
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        payload = {"id": client_uuid, "enable": enable}
        response = requests.post(
            f"{base_url}/panel/api/clients/update/{client_uuid}",
            headers=headers,
            json=payload,
            verify=False,
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            return data.get('success', False)
        return False
    except Exception as e:
        return False

def delete_client(server, client_uuid):
    """Удаление клиента с панели"""
    try:
        base_url = server['url'].rstrip('/')
        api_token = server['api_token']
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json"
        }
        response = requests.post(
            f"{base_url}/panel/api/clients/del/{client_uuid}",
            headers=headers,
            verify=False,
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                return True
        return False
    except Exception as e:
        return False

# ========== ПРОВЕРКА HWID ==========

def check_device_limit(client_uuid, telegram_id):
    """
    Проверка количества HWID устройств и блокировка при превышении.
    Использует email из подписки, а не telegram_id!
    """
    try:
        all_servers = get_servers()
        if not all_servers:
            return {
                'success': False,
                'error': 'Нет серверов',
                'online': 0,
                'max_devices': MAX_DEVICES,
                'blocked': False
            }
        
        main_server = all_servers[0]
        
        # ⚠️ ВАЖНО: получаем email ИЗ ПОДПИСКИ, а не telegram_id!
        user_data = get_user(telegram_id)
        client_email = None
        
        for sub in user_data.get('subscriptions', []):
            if sub.get('uuid') == client_uuid:
                client_email = sub.get('email')
                break
        
        if not client_email:
            client_email = str(telegram_id)
        
        # Получаем список HWID устройств
        hwids = get_client_hwids(main_server, client_email)
        hwid_count = len(hwids)
        
        is_blocked = False
        for sub in user_data.get('subscriptions', []):
            if sub.get('uuid') == client_uuid:
                is_blocked = sub.get('blocked', False)
                break
        
        # Если устройств больше MAX_DEVICES — блокируем
        if hwid_count > MAX_DEVICES:
            if not is_blocked:
                update_client_status(main_server, client_uuid, False)
                for sub in user_data.get('subscriptions', []):
                    if sub.get('uuid') == client_uuid:
                        sub['blocked'] = True
                        sub['blocked_reason'] = f'Превышение устройств ({hwid_count}/{MAX_DEVICES})'
                        sub['blocked_date'] = datetime.now().isoformat()
                        save_user(telegram_id, user_data)
                        break
            
            return {
                'success': False,
                'blocked': True,
                'online': hwid_count,
                'max_devices': MAX_DEVICES,
                'hwids': hwids
            }
        
        # Если было заблокировано и всё в норме — разблокируем
        if is_blocked and hwid_count <= MAX_DEVICES:
            update_client_status(main_server, client_uuid, True)
            for sub in user_data.get('subscriptions', []):
                if sub.get('uuid') == client_uuid:
                    sub['blocked'] = False
                    sub['blocked_reason'] = None
                    sub['blocked_date'] = None
                    save_user(telegram_id, user_data)
                    break
            
            return {
                'success': True,
                'blocked': False,
                'online': hwid_count,
                'max_devices': MAX_DEVICES,
                'unblocked': True,
                'hwids': hwids
            }
        
        return {
            'success': True,
            'blocked': is_blocked,
            'online': hwid_count,
            'max_devices': MAX_DEVICES,
            'hwids': hwids
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'online': 0,
            'max_devices': MAX_DEVICES,
            'blocked': False
        }

# ========== СОЗДАНИЕ ПОДПИСКИ ==========

def create_subscription(server, telegram_id, days, client_name=None):
    try:
        all_servers = get_servers()
        if not all_servers:
            return {'success': False, 'error': 'Нет доступных серверов'}
        
        client_number = get_next_client_number()
        client_uuid = str(uuid.uuid4())
        sub_id = generate_sub_id(16)
        
        all_inbound_ids = []
        for s in all_servers:
            inbound_ids = s.get('inbound_ids', [])
            all_inbound_ids.extend(inbound_ids)
        
        if not all_inbound_ids:
            return {'success': False, 'error': 'Нет инбаундов на серверах'}
        
        main_server = all_servers[0]
        base_url = main_server['url'].rstrip('/')
        api_token = main_server['api_token']
        
        email = str(telegram_id)
        expiry_time = int((datetime.now() + timedelta(days=days)).timestamp() * 1000)

        client_data = {
            "id": client_uuid,
            "email": email,
            "subId": sub_id,
            "flow": "xtls-rprx-vision",
            "fingerprint": "chrome",
            "security": "auto",
            "totalGB": 0,
            "expiryTime": expiry_time,
            "enable": True,
            "limitIp": MAX_DEVICES,
            "comment": f"User_{client_number}"
        }

        payload = {
            "client": client_data,
            "inboundIds": all_inbound_ids
        }

        headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        response = requests.post(
            f"{base_url}/panel/api/clients/add",
            headers=headers,
            json=payload,
            verify=False,
            timeout=30
        )

        if response.status_code != 200:
            return {'success': False, 'error': f'HTTP {response.status_code}: {response.text[:200]}'}

        result = response.json()
        if not result.get('success'):
            error_msg = result.get('msg', 'Unknown error')
            if 'email' in str(error_msg).lower() or 'already' in str(error_msg).lower():
                email = f"{telegram_id}_{random.randint(1, 999)}"
                client_data["email"] = email
                payload = {
                    "client": client_data,
                    "inboundIds": all_inbound_ids
                }
                response = requests.post(
                    f"{base_url}/panel/api/clients/add",
                    headers=headers,
                    json=payload,
                    verify=False,
                    timeout=30
                )
                if response.status_code == 200:
                    result = response.json()
                    if result.get('success'):
                        return process_success_result(result, client_data, all_servers, sub_id, client_number, email, expiry_time, telegram_id, days)
            return {'success': False, 'error': f'Ошибка панели: {error_msg}'}

        return process_success_result(result, client_data, all_servers, sub_id, client_number, email, expiry_time, telegram_id, days)
    except Exception as e:
        return {'success': False, 'error': str(e)}

def process_success_result(result, client_data, all_servers, sub_id, client_number, email, expiry_time, telegram_id, days):
    main_server = all_servers[0]
    link_base = main_server.get('link_url', main_server['url'])
    sub_link = f"{link_base}/{SUBSCRIPTION_PATH}/{sub_id}"
    
    servers_list = []
    for s in all_servers:
        servers_list.append(s.get('name', f"Сервер {s.get('id', '')}"))
    
    user_data = get_user(telegram_id)
    if 'subscriptions' not in user_data:
        user_data['subscriptions'] = []
    
    user_data['subscriptions'].append({
        'purchase_date': datetime.now().isoformat(),
        'expiry_date': datetime.fromtimestamp(expiry_time / 1000).isoformat(),
        'days': days,
        'sub_link': sub_link,
        'sub_id': sub_id,
        'uuid': client_data["id"],
        'client_id': client_number,
        'client_number': client_number,
        'email': email,
        'servers': servers_list,
        'servers_count': len(all_servers),
        'blocked': False,
        'blocked_reason': None,
        'blocked_date': None
    })
    save_user(telegram_id, user_data)
    
    return {
        'success': True,
        'sub_link': sub_link,
        'expiry_date': expiry_time,
        'client_id': client_number,
        'client_number': client_number,
        'sub_id': sub_id,
        'uuid': client_data["id"],
        'email': email,
        'servers': servers_list,
        'servers_count': len(all_servers)
    }

def test_server_connection(server):
    try:
        base_url = server['url'].rstrip('/')
        api_token = server['api_token']
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json"
        }
        response = requests.get(
            f"{base_url}/panel/api/inbounds/list",
            headers=headers,
            verify=False,
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                return {'success': True, 'msg': 'Подключение успешно'}
        return {'success': False, 'msg': f'Ошибка: {response.status_code}'}
    except Exception as e:
        return {'success': False, 'msg': str(e)}
