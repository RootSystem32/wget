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
from database import get_next_client_number

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

def generate_sub_id(length=16):
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def create_subscription(server, email, days, client_name=None):
    try:
        base_url = server['url'].rstrip('/')
        api_token = server['api_token']
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        expiry_time = int((datetime.now() + timedelta(days=days)).timestamp() * 1000)
        client_uuid = str(uuid.uuid4())
        
        sub_id = generate_sub_id()
        
        # ====== ГЕНЕРИРУЕМ НОВЫЙ НОМЕР ======
        client_number = get_next_client_number()
        client_email = str(client_number)
        client_comment = f"User {client_number}"

        client_data = {
            "id": client_uuid,
            "email": client_email,
            "subId": sub_id,
            "flow": "xtls-rprx-vision",
            "fingerprint": "chrome",
            "security": "auto",
            "totalGB": 0,
            "expiryTime": expiry_time,
            "enable": True,
            "comment": client_comment
        }

        payload = {
            "client": client_data,
            "inboundIds": [server['inbound_id']]
        }

        response = requests.post(
            f"{base_url}/panel/api/clients/add",
            headers=headers,
            json=payload,
            verify=False,
            timeout=30
        )

        if response.status_code != 200:
            return {'success': False, 'error': f'HTTP {response.status_code}'}

        result = response.json()
        if not result.get('success'):
            return {'success': False, 'error': result.get('msg', 'Unknown error')}

        link_base = server.get('link_url', server.get('url', PANEL_URL))
        sub_link = f"{link_base}/{SUBSCRIPTION_PATH}/{sub_id}"

        return {
            'success': True,
            'sub_link': sub_link,
            'expiry_date': expiry_time,
            'client_id': client_number,
            'client_number': client_number,
            'client_email': client_email,
            'sub_id': sub_id,
            'uuid': client_uuid
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

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
