# panel_api.py
import requests
import json
import uuid
import secrets
import string
import random
from datetime import datetime, timedelta
from urllib3.exceptions import InsecureRequestWarning
from config import PANEL_URL, SUBSCRIPTION_PATH
from database import get_next_client_number, get_servers

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

def generate_sub_id(length=8):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

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
        
        # EMAIL = ТЕЛЕГРАМ ID (просто число)
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
                # Если email уже существует, добавляем суффикс
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
    
    # Получаем список серверов для сохранения
    servers_list = []
    for s in all_servers:
        servers_list.append(s.get('name', f"Server {s.get('id', '')}"))
    
    from database import save_user, get_user
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
        'client_number': client_number,
        'email': email,
        'servers': servers_list,
        'servers_count': len(all_servers)
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
        response = requests.get(f"{base_url}/panel/api/inbounds/list", headers=headers, verify=False, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                return {'success': True, 'msg': 'Подключение успешно'}
        return {'success': False, 'msg': f'Ошибка: {response.status_code}'}
    except Exception as e:
        return {'success': False, 'msg': str(e)}
