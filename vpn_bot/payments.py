# -*- coding: utf-8 -*-
"""Lava Business API — интеграция для DubikVPN."""
import os
import json
import hmac
import hashlib
import logging
import requests
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LAVA_CONFIG_FILE = os.path.join(BASE_DIR, "lava_config.json")
PENDING_FILE = os.path.join(BASE_DIR, "pending_payments.json")
LAVA_API_BASE = "https://api.lava.ru/business"

DEFAULT_LAVA_CONFIG = {
    "shop_id": "YOUR_SHOP_ID_UUID",
    "secret_key": "YOUR_SECRET_KEY",
    "additional_key": "YOUR_WEBHOOK_ADDITIONAL_KEY",
    "webhook_url": "https://pay.heompvpn.pro:8443/api/lava/webhook",
    "success_url": "https://t.me/DubikVPNBot",
    "fail_url": "https://t.me/DubikVPNBot",
    "expire_minutes": 300,
}


def get_config():
    if os.path.exists(LAVA_CONFIG_FILE):
        try:
            with open(LAVA_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"lava_config read: {e}")
    with open(LAVA_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(DEFAULT_LAVA_CONFIG, f, ensure_ascii=False, indent=2)
    return dict(DEFAULT_LAVA_CONFIG)


def make_signature(body_str, secret_key):
    return hmac.new(secret_key.encode('utf-8'), body_str.encode('utf-8'), hashlib.sha256).hexdigest()


def _post(method, params, use_secret=True):
    cfg = get_config()
    key = cfg['secret_key'] if use_secret else cfg['additional_key']
    body_str = json.dumps(params, ensure_ascii=False)
    signature = make_signature(body_str, key)

    url = f"{LAVA_API_BASE}/{method}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Signature": signature,
    }

    logging.info(f"[LAVA] -> POST {url} body={body_str[:200]}")

    try:
        r = requests.post(url, data=body_str.encode('utf-8'), headers=headers, timeout=30)
        try:
            data = r.json()
        except Exception:
            data = {"error": f"Invalid JSON: {r.text[:200]}"}
        logging.info(f"[LAVA] <- {r.status_code} {str(data)[:300]}")
        return data
    except requests.Timeout:
        return {"error": "Timeout", "data": None}
    except Exception as e:
        logging.error(f"[LAVA] error: {e}")
        return {"error": str(e), "data": None}


def create_invoice(user_id, amount, days, order_id=None):
    cfg = get_config()
    if not order_id:
        order_id = f"dubik_{user_id}_{days}_{int(datetime.now().timestamp())}"

    params = {
        "sum": round(float(amount), 2),
        "orderId": order_id,
        "shopId": cfg["shop_id"],
        "hookUrl": cfg["webhook_url"],
        "successUrl": cfg["success_url"],
        "failUrl": cfg["fail_url"],
        "expire": int(cfg.get("expire_minutes", 300)),
        "comment": f"Подписка DubikVPN на {days} дней",
        "customFields": json.dumps({"user_id": int(user_id), "days": int(days)}, ensure_ascii=False),
    }

    resp = _post("invoice/create", params)

    if resp.get("status_check") and resp.get("data"):
        d = resp["data"]
        inv_id = d.get("id")
        save_pending(inv_id, user_id, amount, days, order_id)
        return {
            "success": True,
            "invoice_id": inv_id,
            "payment_url": d.get("url"),
            "order_id": order_id,
            "expired": d.get("expired"),
        }
    return {"success": False, "error": resp.get("error") or "Unknown"}


def check_invoice_status(invoice_id=None, order_id=None):
    cfg = get_config()
    params = {"shopId": cfg["shop_id"]}
    if invoice_id:
        params["invoiceId"] = invoice_id
    if order_id:
        params["orderId"] = order_id

    resp = _post("invoice/status", params)
    if resp.get("status_check") and resp.get("data"):
        d = resp["data"]
        return {
            "success": True,
            "status": d.get("status"),
            "amount": d.get("amount"),
            "invoice_id": d.get("id"),
            "order_id": d.get("order_id"),
        }
    return {"success": False, "error": resp.get("error") or "Unknown"}


def get_available_tariffs():
    cfg = get_config()
    resp = _post("invoice/get-available-tariffs", {"shopId": cfg["shop_id"]})
    if resp.get("status_check") and isinstance(resp.get("data"), list):
        return {"success": True, "tariffs": resp["data"]}
    return {"success": False, "error": resp.get("error") or "Unknown"}


def verify_webhook_signature(body_str, signature):
    cfg = get_config()
    expected = hmac.new(cfg["additional_key"].encode('utf-8'), body_str.encode('utf-8'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, (signature or "").strip())


def _load_pending():
    if os.path.exists(PENDING_FILE):
        try:
            with open(PENDING_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_pending(data):
    with open(PENDING_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_pending(invoice_id, user_id, amount, days, order_id):
    data = _load_pending()
    data[invoice_id] = {
        "invoice_id": invoice_id,
        "user_id": int(user_id),
        "amount": float(amount),
        "days": int(days),
        "order_id": order_id,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    }
    _save_pending(data)


def get_pending(invoice_id):
    return _load_pending().get(invoice_id)


def get_all_pending():
    return _load_pending()


def mark_paid(invoice_id):
    data = _load_pending()
    if invoice_id in data:
        data[invoice_id]["status"] = "paid"
        data[invoice_id]["paid_at"] = datetime.now().isoformat()
        _save_pending(data)
        return data[invoice_id]
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(get_available_tariffs(), indent=2, ensure_ascii=False))
