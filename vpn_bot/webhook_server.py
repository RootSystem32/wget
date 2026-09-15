# -*- coding: utf-8 -*-
"""Lava Webhook Server — принимает вебхуки Lava, активирует подписки."""
import os
import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from payments import verify_webhook_signature, get_pending, get_all_pending, mark_paid

try:
    from panel_api import create_subscription
    from database import get_user, save_user
except Exception as e:
    logging.error(f"import panel_api/database failed: {e}")
    def create_subscription(*a, **k): return {'success': False, 'error': 'panel_api not loaded'}
    def get_user(uid): return {}
    def save_user(uid, d): pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
app = Flask(__name__)

WEBHOOK_HOST = "127.0.0.1"
WEBHOOK_PORT = 4442


def get_client_ip():
    xff = request.headers.get('X-Forwarded-For')
    if xff:
        return xff.split(',')[0].strip()
    return request.headers.get('X-Real-IP') or request.remote_addr or ""


@app.route('/health')
def health():
    return jsonify({'ok': True, 'service': 'lava-webhook', 'time': datetime.now().isoformat()})


@app.route('/api/lava/webhook', methods=['POST'])
def lava_webhook():
    body_str = request.get_data(as_text=True)
    signature = request.headers.get('Signature', '')
    client_ip = get_client_ip()

    logging.info(f"[LAVA] <- {client_ip} sig={signature[:16]}... len={len(body_str)}")

    try:
        valid = verify_webhook_signature(body_str, signature)
    except Exception as e:
        logging.error(f"[LAVA] sig check error: {e}")
        return jsonify({'error': 'internal error'}), 500

    if not valid:
        logging.warning(f"[LAVA] INVALID SIGNATURE from {client_ip}")
        return jsonify({'error': 'invalid signature'}), 403

    try:
        data = json.loads(body_str)
    except Exception as e:
        logging.error(f"[LAVA] json error: {e}")
        return jsonify({'error': 'invalid json'}), 400

    invoice_id = data.get('id') or data.get('invoice_id')
    status = data.get('status')
    order_id = data.get('order_id')
    amount = data.get('amount')
    custom_fields = data.get('custom_fields')

    logging.info(f"[LAVA] invoice={invoice_id} status={status} amount={amount}")

    if status == 'success':
        _process_success(invoice_id, order_id, custom_fields)

    return jsonify({'ok': True})


def _process_success(invoice_id, order_id, custom_fields):
    p = get_pending(invoice_id) if invoice_id else None

    if not p and order_id:
        for inv, pp in get_all_pending().items():
            if pp.get('order_id') == order_id:
                p = pp
                invoice_id = inv
                break

    if not p and custom_fields:
        try:
            cf = json.loads(custom_fields) if isinstance(custom_fields, str) else custom_fields
            p = {'user_id': int(cf.get('user_id')), 'days': int(cf.get('days'))}
        except Exception as e:
            logging.error(f"[LAVA] custom parse: {e}")

    if not p:
        logging.warning(f"[LAVA] pending not found: invoice={invoice_id} order={order_id}")
        return

    if p.get('status') == 'paid':
        logging.info(f"[LAVA] already paid: {invoice_id}")
        return

    user_id = p['user_id']
    days = p['days']

    logging.info(f"[LAVA] activating user={user_id} days={days}")

    try:
        result = create_subscription(None, user_id, days, f"Lava {invoice_id}")
        if not result.get('success'):
            logging.error(f"[LAVA] create_subscription: {result.get('error')}")
            return

        ud = get_user(user_id)
        if not isinstance(ud, dict):
            ud = {}
        ud.setdefault('subscriptions', []).append({
            'purchase_date': datetime.now().isoformat(),
            'expiry_date': datetime.fromtimestamp(result['expiry_date'] / 1000).isoformat(),
            'days': days,
            'sub_link': result['sub_link'],
            'client_id': result['client_id'],
            'client_number': result.get('client_number'),
            'email': result.get('email'),
            'servers': result.get('servers', []),
            'servers_count': result.get('servers_count', 1),
            'warning_sent': False,
            'is_free': False,
            'source': 'purchase',
            'created_by': None,
            'created_at': datetime.now().isoformat(),
            'totalGB': 0,
            'usedGB': 0,
            'uuid': result.get('uuid'),
            'blocked': False,
            'blocked_reason': None,
            'blocked_date': None,
        })
        save_user(user_id, ud)
        mark_paid(invoice_id)
        logging.info(f"[LAVA] OK activated: user={user_id} days={days}")
    except Exception as e:
        logging.error(f"[LAVA] activation error: {e}")


if __name__ == '__main__':
    logging.info(f"Lava Webhook Server -> {WEBHOOK_HOST}:{WEBHOOK_PORT}")
    app.run(host=WEBHOOK_HOST, port=WEBHOOK_PORT, debug=False)
