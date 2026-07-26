# -*- coding: utf-8 -*-
# bot.py
import logging
import asyncio
import os
import secrets
import string
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from config import BOT_TOKEN, MAIN_ADMIN_ID, PRICES, FREE_PERIOD_DAYS, PANEL_URL, SUBSCRIPTION_PATH
from database import *
from panel_api import create_subscription, test_server_connection
import qrcode
from io import BytesIO

logging.basicConfig(level=logging.INFO)

# Загружаем способы оплаты из базы
def get_payment_methods():
    return load_data("payment_methods.json", {
        'card': {'name': '💳 Банковская карта', 'details': 'Карта: 2200 1234 5678 9012\nПолучатель: Иванов Иван'},
        'sbp': {'name': '📱 СБП', 'details': 'Номер: +7 999 123-45-67\nПолучатель: Иванов Иван'},
        'usdt': {'name': '🪙 USDT (TRC20)', 'details': 'Адрес: TXxxx...'}
    })

def save_payment_methods(methods):
    save_data("payment_methods.json", methods)

# ========== ИЗВЛЕЧЕНИЕ ДОМЕНА ИЗ ССЫЛКИ ==========
def extract_domain_from_url(url):
    """Извлекает домен из URL и добавляет https://"""
    url = url.strip()
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url
    parsed = re.match(r'(https?://[^:/]+)', url)
    if parsed:
        return parsed.group(1)
    return None

def extract_full_url_with_port(url):
    """Извлекает полный URL с портом"""
    url = url.strip()
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url
    return url

# ========== СТАТУС СИСТЕМЫ ==========
async def get_system_status_text():
    servers = get_servers()
    total_clients = 0
    total_free = 0
    lines = []
    for s in servers:
        used = get_server_used_slots(s['id'])
        total_clients += used
        max_slots = s.get('max_slots')
        if max_slots and max_slots > 0:
            free = max_slots - used
            total_free += free
            lines.append(f"🌍 {s['name']} ({used}/{max_slots})")
        else:
            lines.append(f"🌍 {s['name']} ({used}/∞)")
    return f"📊 СТАТУС СИСТЕМЫ\n\n🆓 Свободных мест: {total_free}\n🟢 Клиентов подключено: {total_clients}\n\n📡 Доступные сервера:\n" + "\n".join(lines)

# ========== ПРОВЕРКА ИСТЕКАЮЩИХ ПОДПИСОК ==========
async def check_expiring_subs(context):
    users = get_users()
    now = datetime.now()
    for uid_str, u_data in users.items():
        uid = int(uid_str)
        for sub in u_data.get('subscriptions', []):
            expiry = datetime.fromisoformat(sub['expiry_date'])
            left = (expiry - now).total_seconds() / 3600
            if 23 <= left <= 25 and not sub.get('warning_sent'):
                sub['warning_sent'] = True
                save_user(uid, u_data)
                try:
                    await context.bot.send_message(uid, f"⚠️ ВНИМАНИЕ!\n\nВаша подписка на сервере {sub['server_name']} истекает через 24 часа.\n\n📅 Дата окончания: {expiry.strftime('%d.%m.%Y %H:%M')}")
                except:
                    pass

# ========== АВТОПРОДЛЕНИЕ ==========
async def auto_renew_check(context):
    renewed = auto_renew_subscriptions()
    if renewed:
        for user_id in renewed:
            try:
                user_data = get_user(user_id)
                await context.bot.send_message(
                    user_id,
                    "🔄 ПОДПИСКА АВТОМАТИЧЕСКИ ПРОДЛЕНА!\n\n"
                    "Ваша подписка была автоматически продлена благодаря достаточному балансу.\n"
                    f"💰 Новый баланс: {user_data.get('balance', 0)}₽"
                )
            except:
                pass

# ========== ПУШ УВЕДОМЛЕНИЯ ==========
async def check_renewal_reminders(context):
    users = get_users()
    now = datetime.now()
    for uid_str, user_data in users.items():
        uid = int(uid_str)
        for sub in user_data.get('subscriptions', []):
            expiry = datetime.fromisoformat(sub['expiry_date'])
            days_left = (expiry - now).days
            reminder_sent = sub.get('reminder_sent', {})
            reminders = {7: 'неделя', 3: '3 дня', 1: 'завтра'}
            for days, text in reminders.items():
                if days_left == days and not reminder_sent.get(str(days), False):
                    try:
                        price = PRICES.get(sub.get('days', 30), 150)
                        balance = user_data.get('balance', 0)
                        msg = f"⏰ НАПОМИНАНИЕ!\n\n"
                        msg += f"Ваша подписка на сервере {sub['server_name']} истекает через {text}!\n"
                        msg += f"📅 Дата окончания: {expiry.strftime('%d.%m.%Y %H:%M')}\n\n"
                        if balance >= price:
                            msg += f"💰 На вашем балансе достаточно средств ({balance}₽)\n"
                            msg += "✅ Подписка будет автоматически продлена!"
                        else:
                            msg += f"💰 На балансе: {balance}₽ (нужно {price}₽)\n"
                            msg += "💳 Пополните баланс для автопродления!"
                        await context.bot.send_message(uid, msg)
                        if 'reminder_sent' not in sub:
                            sub['reminder_sent'] = {}
                        sub['reminder_sent'][str(days)] = True
                        save_user(uid, user_data)
                    except Exception as e:
                        print(f"Ошибка отправки напоминания {uid}: {e}")

# ========== QR-КОД ==========
async def generate_qr_code(data):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_byte_arr = BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return img_byte_arr

# ========== ВЫВОД СРЕДСТВ ==========
async def show_withdraw_menu(query, context):
    user_id = query.from_user.id
    user_data = get_user(user_id)
    main_balance = user_data.get('balance', 0)
    text = f"💸 ВЫВОД СРЕДСТВ\n\n💰 Основной баланс: {main_balance}₽\n\nВыберите действие:"
    keyboard = [
        [InlineKeyboardButton("💳 Вывод на карту", callback_data="withdraw_card")],
        [InlineKeyboardButton("🔙 Назад", callback_data="profile")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def withdraw_card(query, context):
    user_id = query.from_user.id
    user_data = get_user(user_id)
    main_balance = user_data.get('balance', 0)
    text = f"💳 ВЫВОД НА КАРТУ\n\n💰 Доступно для вывода: {main_balance}₽\n\n"
    text += "Для вывода средств на карту, пожалуйста, укажите:\n"
    text += "1. Сумму вывода\n2. Номер карты\n3. ФИО получателя\n\n"
    text += "Введите данные в формате:\n<сумма> | <номер карты> | <ФИО>\n\n"
    text += "Пример: 500 | 1234 5678 9012 3456 | Иванов Иван Иванович"
    context.user_data['withdraw_step'] = 'waiting_details'
    keyboard = [[InlineKeyboardButton("🔙 Отмена", callback_data="withdraw_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def process_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    if context.user_data.get('withdraw_step') == 'waiting_details':
        try:
            parts = text.split('|')
            if len(parts) != 3:
                await update.message.reply_text(
                    "❌ Неверный формат!\n\nИспользуйте формат:\nсумма | номер карты | ФИО\n\nПример: 500 | 1234 5678 9012 3456 | Иванов Иван Иванович",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="withdraw_menu")]])
                )
                return
            amount = float(parts[0].strip())
            card_number = parts[1].strip()
            full_name = parts[2].strip()
            if amount <= 0:
                await update.message.reply_text("❌ Сумма должна быть больше 0!")
                return
            user_data = get_user(user_id)
            main_balance = user_data.get('balance', 0)
            if amount > main_balance:
                await update.message.reply_text(
                    f"❌ Недостаточно средств!\n\n💰 Доступно: {main_balance}₽\n💳 Запрошено: {amount:.2f}₽",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="withdraw_menu")]])
                )
                return
            withdraw_data = {
                'user_id': user_id,
                'amount': amount,
                'card_number': card_number,
                'full_name': full_name,
                'date': datetime.now().isoformat(),
                'status': 'pending'
            }
            pending_withdrawals = load_data("pending_withdrawals.json", [])
            pending_withdrawals.append(withdraw_data)
            save_data("pending_withdrawals.json", pending_withdrawals)
            await update.message.reply_text(
                f"✅ Заявка на вывод отправлена!\n\n💰 Сумма: {amount:.2f}₽\n💳 Карта: {card_number}\n👤 Получатель: {full_name}\n\n⏳ Ожидайте подтверждения администратора.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="profile")]])
            )
            for admin_id in get_admins():
                try:
                    keyboard = [
                        [InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_withdraw_{user_id}_{int(datetime.now().timestamp())}")],
                        [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_withdraw_{user_id}_{int(datetime.now().timestamp())}")]
                    ]
                    await context.bot.send_message(
                        admin_id,
                        f"💳 НОВАЯ ЗАЯВКА НА ВЫВОД!\n\n👤 Пользователь: {user_id}\n💰 Сумма: {amount:.2f}₽\n💳 Карта: {card_number}\n👤 Получатель: {full_name}\n📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                except:
                    pass
            context.user_data['withdraw_step'] = None
        except ValueError:
            await update.message.reply_text(
                "❌ Ошибка! Сумма должна быть числом.\n\nИспользуйте формат:\nсумма | номер карты | ФИО",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="withdraw_menu")]])
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")

# ========== ГЛАВНОЕ МЕНЮ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    log_user_activity(user_id, 'start')
    user_data = get_user(user_id)
    if update.effective_user:
        user_data['username'] = update.effective_user.username or ''
        user_data['first_name'] = update.effective_user.first_name or ''
        save_user(user_id, user_data)
    total_clients = sum(get_server_used_slots(s['id']) for s in get_servers())
    got_free = user_data.get('got_free', False)
    free_status = "✅" if got_free else "❌"
    keyboard = [
        [InlineKeyboardButton("🛒 Купить подписку", callback_data="buy")],
        [InlineKeyboardButton("👤 Личный кабинет", callback_data="profile")],
        [InlineKeyboardButton("🔑 Мои подписки", callback_data="my_subs")],
        [InlineKeyboardButton("🎁 Бесплатно 3 дня", callback_data="free_sub")],
        [InlineKeyboardButton("📖 Инструкция", callback_data="instructions")],
        [InlineKeyboardButton("📊 Статус системы", callback_data="system_status")],
        [InlineKeyboardButton("ℹ️ О сервисе", callback_data="about")]
    ]
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("🛡️ Админ панель", callback_data="admin_panel")])
        keyboard.append([InlineKeyboardButton("📊 Просмотр статистики", callback_data="view_stats")])
    ad_text = (
        f"\n\n📢 <b>Наши каналы и сайт:</b>\n"
        f"📌 Наш канал: @Dubiikk\n"
        f"📌 Канал с информацией о VPN: @dubikvpn_channel\n"
        f"🌐 Наш сайт: https://www.heompvpn.pro\n\n"
        f"💎 <b>Мы предлагаем:</b>\n"
        f"🔹 Безлимитный трафик\n🔹 Высокую скорость\n🔹 Защиту ваших данных\n🔹 До 3 устройств одновременно\n\n"
        f"<b>Дубик VPN — это ваш ключ к открытому интернету!</b> 🚀"
    )
    await update.message.reply_text(
        f"🔒 DubikVPN\n\n🟢 Клиентов подключено: {total_clients}\n🎁 Бесплатный ключ: {free_status}\n\nВыберите действие:{ad_text}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

# ========== ВЫБОР СЕРВЕРА ==========
async def show_servers(query, context):
    servers = get_servers()
    if not servers:
        await query.edit_message_text("❌ Серверы не найдены", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))
        return
    keyboard = []
    for s in servers:
        available = get_available_slots(s['id'])
        if available is None:
            status = "♾️"
        elif available > 0:
            status = f"✅ {available}/{s.get('max_slots', 0)}"
        else:
            status = "❌ мест нет"
        keyboard.append([InlineKeyboardButton(f"🌍 {s['name']} [{status}]", callback_data=f"select_server_{s['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
    await query.edit_message_text("🌐 Выберите сервер:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_durations(query, context):
    keyboard = [
        [InlineKeyboardButton("📅 1 месяц (150₽)", callback_data="duration_30")],
        [InlineKeyboardButton("📅 3 месяца (350₽)", callback_data="duration_90")],
        [InlineKeyboardButton("📅 6 месяцев (700₽)", callback_data="duration_180")],
        [InlineKeyboardButton("🔙 Назад", callback_data="buy")]
    ]
    await query.edit_message_text("💎 Выберите срок:", reply_markup=InlineKeyboardMarkup(keyboard))

# ========== ЛИЧНЫЙ КАБИНЕТ ==========
async def show_profile(query, context):
    user_id = query.from_user.id
    user_data = get_user(user_id)
    balance = user_data.get('balance', 0)
    subs = user_data.get('subscriptions', [])
    log_user_activity(user_id, 'profile')
    text = f"👤 ЛИЧНЫЙ КАБИНЕТ\n\n💰 Баланс: {balance}₽\n🔑 Активных подписок: {len([s for s in subs if datetime.fromisoformat(s['expiry_date']) > datetime.now()])}\n🎁 Бесплатный ключ: {'✅ Получен' if user_data.get('got_free') else '❌ Не получен'}\n"
    if subs:
        text += "\n📋 Активные подписки:\n"
        for i, s in enumerate(subs[:3], 1):
            expiry = datetime.fromisoformat(s['expiry_date']).strftime('%d.%m.%Y')
            days_left = (datetime.fromisoformat(s['expiry_date']) - datetime.now()).days
            text += f"{i}. {s['server_name']} до {expiry} (осталось {days_left} дн.)\n"
        if len(subs) > 3:
            text += f"... и еще {len(subs)-3} подписок"
    keyboard = [
        [InlineKeyboardButton("💳 Пополнить баланс", callback_data="topup_balance")],
        [InlineKeyboardButton("💸 Перевести баланс", callback_data="transfer_balance")],
        [InlineKeyboardButton("💸 Вывод средств", callback_data="withdraw_menu")],
        [InlineKeyboardButton("📜 История операций", callback_data="transaction_history")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ========== ПОПОЛНЕНИЕ БАЛАНСА ==========
async def topup_balance(query, context):
    user_id = query.from_user.id
    text = "💳 ПОПОЛНЕНИЕ БАЛАНСА\n\nВыберите сумму:\n"
    keyboard = [
        [InlineKeyboardButton("100₽", callback_data="topup_100")],
        [InlineKeyboardButton("200₽", callback_data="topup_200")],
        [InlineKeyboardButton("500₽", callback_data="topup_500")],
        [InlineKeyboardButton("1000₽", callback_data="topup_1000")],
        [InlineKeyboardButton("🔙 Назад", callback_data="profile")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def process_topup(query, context):
    user_id = query.from_user.id
    amount = int(query.data.split("_")[1])
    context.user_data['topup_amount'] = amount
    payment_methods = get_payment_methods()
    payment_text = "🏦 СПОСОБЫ ОПЛАТЫ:\n\n"
    for key, method in payment_methods.items():
        payment_text += f"{method['name']}:\n{method['details']}\n\n"
    text = f"💳 ПОПОЛНЕНИЕ БАЛАНСА\n\n💰 Сумма: {amount}₽\n\n{payment_text}\n⚠️ В комментарии укажите: {user_id}\n\n✅ После перевода нажмите кнопку ниже"
    keyboard = [
        [InlineKeyboardButton("✅ Я перевел(а)", callback_data="topup_done")],
        [InlineKeyboardButton("🔙 Назад", callback_data="profile")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    add_pending_topup(user_id, amount)

async def topup_done(query, context):
    user_id = query.from_user.id
    pending = get_pending_topup(user_id)
    if not pending:
        await query.edit_message_text("❌ Заявка на пополнение не найдена", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="profile")]]))
        return
    amount = pending.get('amount', 0)
    await query.edit_message_text("📸 Заявка на пополнение отправлена!\n\nОжидайте подтверждения.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="profile")]]))
    for admin_id in get_admins():
        try:
            keyboard = [
                [InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_topup_{user_id}")],
                [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_topup_{user_id}")]
            ]
            await context.bot.send_message(
                admin_id,
                f"💰 НОВАЯ ЗАЯВКА НА ПОПОЛНЕНИЕ!\n\n👤 Пользователь: {user_id}\n💰 Сумма: {amount}₽",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except:
            pass

# ========== ПЕРЕВОД БАЛАНСА ==========
async def transfer_balance_start(query, context):
    context.user_data['transfer_step'] = 'waiting_user_id'
    await query.edit_message_text(
        "💸 ПЕРЕВОД БАЛАНСА\n\nВведите Telegram ID пользователя, которому хотите перевести средства:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="profile")]])
    )

async def process_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    step = context.user_data.get('transfer_step')
    if step == 'waiting_user_id':
        try:
            target_id = int(text)
            if target_id == user_id:
                await update.message.reply_text("❌ Нельзя перевести самому себе!")
                return
            target_data = get_user(target_id)
            if not target_data:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            context.user_data['transfer_target'] = target_id
            context.user_data['transfer_step'] = 'waiting_amount'
            await update.message.reply_text(
                f"👤 Получатель: {target_id}\n\nВведите сумму перевода (в рублях):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="profile")]])
            )
        except ValueError:
            await update.message.reply_text("❌ Введите корректный Telegram ID (только цифры)")
    elif step == 'waiting_amount':
        try:
            amount = int(text)
            if amount <= 0:
                await update.message.reply_text("❌ Сумма должна быть больше 0!")
                return
            user_data = get_user(user_id)
            balance = user_data.get('balance', 0)
            if amount > balance:
                await update.message.reply_text(f"❌ Недостаточно средств!\n💰 Ваш баланс: {balance}₽")
                return
            target_id = context.user_data.get('transfer_target')
            user_data['balance'] = balance - amount
            save_user(user_id, user_data)
            target_data = get_user(target_id)
            target_data['balance'] = target_data.get('balance', 0) + amount
            save_user(target_id, target_data)
            add_transaction(user_id, -amount, 'transfer_out', f'Перевод пользователю {target_id}')
            add_transaction(target_id, amount, 'transfer_in', f'Перевод от пользователя {user_id}')
            try:
                await context.bot.send_message(
                    target_id,
                    f"💰 ПОСТУПЛЕНИЕ СРЕДСТВ!\n\nВы получили перевод от пользователя {user_id}\nСумма: {amount}₽\n\nВаш баланс: {target_data['balance']}₽"
                )
            except:
                pass
            await update.message.reply_text(
                f"✅ Перевод выполнен успешно!\n\n💰 Сумма: {amount}₽\n👤 Получатель: {target_id}\n\nВаш баланс: {user_data['balance']}₽",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 На главную", callback_data="back_to_menu")]])
            )
            context.user_data['transfer_step'] = None
            context.user_data.pop('transfer_target', None)
        except ValueError:
            await update.message.reply_text("❌ Введите корректную сумму (только цифры)")

# ========== ИСТОРИЯ ТРАНЗАКЦИЙ ==========
async def show_transactions(query, context):
    user_id = query.from_user.id
    transactions = get_user_transactions(user_id)
    if not transactions:
        await query.edit_message_text("📜 ИСТОРИЯ ОПЕРАЦИЙ\n\nУ вас пока нет операций.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="profile")]]))
        return
    text = "📜 ИСТОРИЯ ОПЕРАЦИЙ (последние 10):\n\n"
    for t in transactions[-10:]:
        date = datetime.fromisoformat(t['date']).strftime('%d.%m.%Y %H:%M')
        if t['type'] == 'topup':
            text += f"✅ Пополнение: +{t['amount']}₽ ({date})\n"
        elif t['type'] == 'transfer_in':
            text += f"📥 Перевод: +{t['amount']}₽ ({date})\n"
        elif t['type'] == 'transfer_out':
            text += f"📤 Перевод: -{abs(t['amount'])}₽ ({date})\n"
        elif t['type'] == 'subscription':
            text += f"🔑 Оплата подписки: -{abs(t['amount'])}₽ ({date})\n"
        elif t['type'] == 'withdraw':
            text += f"💸 Вывод средств: -{abs(t['amount'])}₽ ({date})\n"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="profile")]]))

# ========== БЕКАП ==========
async def admin_backup(query, context):
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Нет доступа")
        return
    await query.edit_message_text("⏳ Создание бекапа...")
    try:
        backup_file, timestamp = create_backup()
        with open(backup_file, 'rb') as f:
            await context.bot.send_document(
                chat_id=query.from_user.id,
                document=f,
                caption=f"✅ Бекап создан!\n📅 Дата: {timestamp}\n📁 Файл: {os.path.basename(backup_file)}"
            )
        backups = get_backup_list()
        if backups:
            text = "📂 СПИСОК БЕКАПОВ:\n\n"
            for i, b in enumerate(backups[:5], 1):
                date = datetime.fromtimestamp(b['modified']).strftime('%d.%m.%Y %H:%M')
                size_kb = b['size'] / 1024
                text += f"{i}. {b['name']}\n   📅 {date}\n   📦 {size_kb:.1f} KB\n\n"
            keyboard = [
                [InlineKeyboardButton("🔄 Восстановить бекап", callback_data="admin_restore_backup")],
                [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
            ]
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.message.reply_text("✅ Бекап создан!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))
    except Exception as e:
        await query.message.reply_text(f"❌ Ошибка при создании бекапа: {str(e)}")

async def admin_restore_backup(query, context):
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Нет доступа")
        return
    backups = get_backup_list()
    if not backups:
        await query.edit_message_text("❌ Нет доступных бекапов", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))
        return
    keyboard = []
    for b in backups[:5]:
        date = datetime.fromtimestamp(b['modified']).strftime('%d.%m.%Y %H:%M')
        keyboard.append([InlineKeyboardButton(f"📁 {b['name']} ({date})", callback_data=f"restore_backup_{b['name']}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")])
    await query.edit_message_text("🔄 ВЫБЕРИТЕ БЕКАП ДЛЯ ВОССТАНОВЛЕНИЯ:\n\n⚠️ ВНИМАНИЕ! Текущие данные будут заменены!", reply_markup=InlineKeyboardMarkup(keyboard))

async def process_restore_backup(query, context):
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Нет доступа")
        return
    backup_name = query.data.split("_")[2]
    keyboard = [
        [InlineKeyboardButton("✅ Да, восстановить", callback_data=f"confirm_restore_{backup_name}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin_panel")]
    ]
    await query.edit_message_text(f"⚠️ ВНИМАНИЕ!\n\nВы уверены, что хотите восстановить бекап {backup_name}?\n\nВсе текущие данные будут заменены!", reply_markup=InlineKeyboardMarkup(keyboard))

async def confirm_restore_backup(query, context):
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Нет доступа")
        return
    backup_name = query.data.split("_")[2]
    await query.edit_message_text("⏳ Восстановление бекапа...")
    success, message = restore_backup(backup_name)
    if success:
        await query.message.reply_text(f"✅ {message}\n\nРекомендуется перезапустить бота для полного применения изменений.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))
    else:
        await query.message.reply_text(f"❌ {message}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))

# ========== ОБРАБОТЧИК ВЫВОДА ДЛЯ АДМИНОВ ==========
async def approve_withdraw(query, context):
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Нет прав")
        return
    data_parts = query.data.split("_")
    user_id = int(data_parts[2])
    pending_withdrawals = load_data("pending_withdrawals.json", [])
    found = False
    withdraw_data = None
    for w in pending_withdrawals:
        if w['user_id'] == user_id and w['status'] == 'pending':
            w['status'] = 'approved'
            withdraw_data = w
            found = True
            break
    if not found:
        await query.edit_message_text("❌ Заявка не найдена или уже обработана")
        return
    save_data("pending_withdrawals.json", pending_withdrawals)
    user_data = get_user(user_id)
    amount = withdraw_data['amount']
    user_data['balance'] = user_data.get('balance', 0) - amount
    save_user(user_id, user_data)
    add_transaction(user_id, -amount, 'withdraw', f'Вывод на карту {withdraw_data["card_number"]}')
    try:
        await context.bot.send_message(
            user_id,
            f"✅ ВЫВОД СРЕДСТВ ПОДТВЕРЖДЕН!\n\n💰 Сумма: {amount:.2f}₽\n💳 Карта: {withdraw_data['card_number']}\n👤 Получатель: {withdraw_data['full_name']}\n\nСредства будут отправлены в ближайшее время."
        )
    except:
        pass
    await query.edit_message_text(f"✅ Вывод для пользователя {user_id} подтвержден")

async def reject_withdraw(query, context):
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Нет прав")
        return
    data_parts = query.data.split("_")
    user_id = int(data_parts[2])
    pending_withdrawals = load_data("pending_withdrawals.json", [])
    found = False
    for w in pending_withdrawals:
        if w['user_id'] == user_id and w['status'] == 'pending':
            w['status'] = 'rejected'
            found = True
            break
    if not found:
        await query.edit_message_text("❌ Заявка не найдена или уже обработана")
        return
    save_data("pending_withdrawals.json", pending_withdrawals)
    try:
        await context.bot.send_message(
            user_id,
            f"❌ ВЫВОД СРЕДСТВ ОТКЛОНЕН!\n\nК сожалению, ваша заявка на вывод была отклонена администратором.\nПожалуйста, свяжитесь с поддержкой для уточнения причин."
        )
    except:
        pass
    await query.edit_message_text(f"❌ Вывод для пользователя {user_id} отклонен")

# ========== ОСНОВНОЙ ОБРАБОТЧИК КНОПОК ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "back_to_menu":
        total_clients = sum(get_server_used_slots(s['id']) for s in get_servers())
        users = get_users()
        user_data = users.get(str(user_id), {})
        got_free = user_data.get('got_free', False)
        free_status = "✅" if got_free else "❌"
        keyboard = [
            [InlineKeyboardButton("🛒 Купить подписку", callback_data="buy")],
            [InlineKeyboardButton("👤 Личный кабинет", callback_data="profile")],
            [InlineKeyboardButton("🔑 Мои подписки", callback_data="my_subs")],
            [InlineKeyboardButton("🎁 Бесплатно 3 дня", callback_data="free_sub")],
            [InlineKeyboardButton("📖 Инструкция", callback_data="instructions")],
            [InlineKeyboardButton("📊 Статус системы", callback_data="system_status")],
            [InlineKeyboardButton("ℹ️ О сервисе", callback_data="about")]
        ]
        if is_admin(user_id):
            keyboard.append([InlineKeyboardButton("🛡️ Админ панель", callback_data="admin_panel")])
            keyboard.append([InlineKeyboardButton("📊 Просмотр статистики", callback_data="view_stats")])
        ad_text = (
            f"\n\n📢 <b>Наши каналы и сайт:</b>\n"
            f"📌 Наш канал: @Dubiikk\n"
            f"📌 Канал с информацией о VPN: @dubikvpn_channel\n"
            f"🌐 Наш сайт: https://www.heompvpn.pro\n\n"
            f"💎 <b>Мы предлагаем:</b>\n"
            f"🔹 Безлимитный трафик\n🔹 Высокую скорость\n🔹 Защиту ваших данных\n🔹 До 3 устройств одновременно\n\n"
            f"<b>Дубик VPN — это ваш ключ к открытому интернету!</b> 🚀"
        )
        await query.edit_message_text(
            f"🔒 DubikVPN\n\n🟢 Клиентов подключено: {total_clients}\n🎁 Бесплатный ключ: {free_status}\n\nВыберите действие:{ad_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
        return

    elif data == "view_stats":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ Нет доступа")
            return
        stats_url = f"http://144.31.193.141:8444"
        await query.edit_message_text(
            f"📊 СТАТИСТИКА\n\nПерейдите по ссылке для просмотра статистики:\n🌐 {stats_url}\n\n🔑 IP для входа: 176.59.132.127",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 Открыть статистику", url=stats_url)],
                [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
            ])
        )
        return

    if data == "buy":
        await show_servers(query, context)
    elif data.startswith("select_server_"):
        server_id = int(data.split("_")[2])
        context.user_data['selected_server_id'] = server_id
        await show_durations(query, context)
    elif data == "profile":
        await show_profile(query, context)
    elif data == "withdraw_menu":
        await show_withdraw_menu(query, context)
    elif data == "withdraw_card":
        await withdraw_card(query, context)
    elif data == "topup_balance":
        await topup_balance(query, context)
    elif data.startswith("topup_"):
        if data == "topup_done":
            await topup_done(query, context)
        else:
            amount = data.split("_")[1]
            if amount.isdigit():
                await process_topup(query, context)
    elif data == "transfer_balance":
        await transfer_balance_start(query, context)
    elif data == "transaction_history":
        await show_transactions(query, context)
    elif data == "my_subs":
        user_id = query.from_user.id
        subs = get_user(user_id).get('subscriptions', [])
        if not subs:
            await query.edit_message_text("У вас пока нет подписок", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))
            return
        text = "🔑 Ваши подписки:\n\n"
        for i, s in enumerate(subs, 1):
            expiry = datetime.fromisoformat(s['expiry_date']).strftime('%d.%m.%Y')
            days_left = (datetime.fromisoformat(s['expiry_date']) - datetime.now()).days
            client_id = s.get('client_id', '???')
            if s.get('is_free'):
                period = "3 дня (бесплатный)"
            else:
                days_val = s.get('days', 0)
                if days_val == 30:
                    period = "1 месяц"
                elif days_val == 90:
                    period = "3 месяца"
                elif days_val == 180:
                    period = "6 месяцев"
                else:
                    period = f"{days_val} дней"
            sub_link = s.get('sub_link', 'нет ссылки')
            text += f"{i}. {s['server_name']}\n   ID: {client_id}\n   До: {expiry} (осталось {days_left} дн.)\n   {period}\n   🔗 {sub_link}\n\n"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))

    elif data == "free_sub":
        user_id = query.from_user.id
        users = get_users()
        user_data = users.get(str(user_id), {})
        if user_data.get('got_free', False):
            await query.edit_message_text("❌ Вы уже получали бесплатный ключ!\n\nБесплатный период предоставляется только один раз.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))
            return
        servers = get_servers()
        if not servers:
            await query.edit_message_text("❌ Нет серверов", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))
            return
        server = None
        for s in servers:
            if is_slot_available(s['id']):
                server = s
                break
        if not server:
            await query.edit_message_text("❌ На всех серверах закончились места", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))
            return
        client_name = "DubikVPN (free)"
        email = f"free_{user_id}_{int(datetime.now().timestamp())}"
        result = create_subscription(server, email, FREE_PERIOD_DAYS, client_name)
        if result['success']:
            user_data = get_user(user_id)
            if 'subscriptions' not in user_data:
                user_data['subscriptions'] = []
            user_data['subscriptions'].append({
                'server_id': server['id'],
                'server_name': server['name'],
                'purchase_date': datetime.now().isoformat(),
                'expiry_date': datetime.fromtimestamp(result['expiry_date']/1000).isoformat(),
                'days': FREE_PERIOD_DAYS,
                'sub_link': result['sub_link'],
                'client_number': result.get('client_number'),
                'email': result.get('client_email'),
                'client_id': result['client_id'],
                'is_free': True,
                'warning_sent': False
            })
            user_data['got_free'] = True
            save_user(user_id, user_data)
            update_server_used_slots(server['id'])
            qr_img = await generate_qr_code(result['sub_link'])
            await query.edit_message_text(
                f"🎁 БЕСПЛАТНАЯ ПОДПИСКА!\n\n📅 {FREE_PERIOD_DAYS} дня\n\n🔗 {result['sub_link']}\n\n⚠️ Только один раз!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 На главную", callback_data="back_to_menu")]])
            )
            await context.bot.send_photo(chat_id=user_id, photo=qr_img, caption="📱 Ваш QR-код для подключения")
        else:
            await query.edit_message_text(f"❌ Ошибка: {result['error']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))

    elif data == "instructions":
        await query.edit_message_text(
            "📖 Как подключиться:\n\n1️⃣ Скачайте Nekobox или v2rayNG\n2️⃣ Скопируйте ссылку подписки\n3️⃣ Добавить подписку → Вставить ссылку\n4️⃣ Обновить и подключиться",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]])
        )
    elif data == "about":
        await query.edit_message_text(
            "🔒 DubikVPN\n\n🔸 Youtube без рекламы\n🔸 ChatGPT, Grok, нейросети работают\n🔸 Низкий пинг\n\n📋 Условия:\n• Не более 3 устройств\n• Торренты запрещены",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]])
        )
    elif data == "system_status":
        text = await get_system_status_text()
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))
    elif data.startswith("duration_"):
        days = int(data.split("_")[1])
        price = PRICES.get(days)
        context.user_data['pending_days'] = days
        context.user_data['pending_price'] = price
        server_id = context.user_data.get('selected_server_id')
        server = get_server_by_id(server_id)
        if not server:
            await query.edit_message_text("❌ Сервер не найден")
            return
        context.user_data['pending_server_id'] = server_id
        keyboard = [
            [InlineKeyboardButton("💰 Оплатить с баланса", callback_data="pay_from_balance")],
            [InlineKeyboardButton("💳 Оплатить переводом", callback_data="pay_by_transfer")],
            [InlineKeyboardButton("🔙 Назад", callback_data="buy")]
        ]
        await query.edit_message_text(
            f"💎 ОПЛАТА ПОДПИСКИ\n\n📅 Срок: {days//30} мес.\n💰 Стоимость: {price}₽\n🌍 Сервер: {server['name']}\n\nВыберите способ оплаты:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "pay_from_balance":
        days = context.user_data.get('pending_days')
        price = context.user_data.get('pending_price')
        server_id = context.user_data.get('pending_server_id')
        if not all([days, price, server_id]):
            await query.edit_message_text("❌ Ошибка: данные потеряны")
            return
        user_data = get_user(user_id)
        balance = user_data.get('balance', 0)
        if balance < price:
            await query.edit_message_text(
                f"❌ Недостаточно средств на балансе!\n\n💰 Ваш баланс: {balance}₽\n💳 Нужно: {price}₽\n\nПополните баланс в личном кабинете.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👤 Личный кабинет", callback_data="profile")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="buy")]
                ])
            )
            return
        server = get_server_by_id(server_id)
        if not server:
            await query.edit_message_text("❌ Сервер не найден")
            return
        if not is_slot_available(server['id']):
            await query.edit_message_text("❌ На сервере нет свободных мест")
            return
        user_data['balance'] = balance - price
        save_user(user_id, user_data)
        next_id = get_next_client_id()
        client_name = f"DubikVPN ({next_id})"
        email = f"user_{user_id}_{next_id}_{int(datetime.now().timestamp())}"
        result = create_subscription(server, email, days, client_name)
        if result['success']:
            if 'subscriptions' not in user_data:
                user_data['subscriptions'] = []
            user_data['subscriptions'].append({
                'server_id': server['id'],
                'server_name': server['name'],
                'purchase_date': datetime.now().isoformat(),
                'expiry_date': datetime.fromtimestamp(result['expiry_date']/1000).isoformat(),
                'days': days,
                'sub_link': result['sub_link'],
                'client_id': result['client_id'],
                'client_number': result.get('client_number'),
                'email': result.get('client_email'),
                'warning_sent': False,
                'is_free': False
            })
            save_user(user_id, user_data)
            update_server_used_slots(server['id'])
            add_transaction(user_id, -price, 'subscription', f'Оплата подписки на {days} дней')
            qr_img = await generate_qr_code(result['sub_link'])
            await query.edit_message_text(
                f"✅ ПОДПИСКА АКТИВИРОВАНА!\n\n📅 Срок: {days//30} мес.\n📛 ID: {result['client_id']}\n💰 С баланса списано: {price}₽\n\n🔗 {result['sub_link']}\n\n⚠️ Не более 3 устройств",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 На главную", callback_data="back_to_menu")]])
            )
            await context.bot.send_photo(chat_id=user_id, photo=qr_img, caption="📱 Ваш QR-код для подключения")
        else:
            user_data['balance'] = user_data.get('balance', 0) + price
            save_user(user_id, user_data)
            await query.edit_message_text(f"❌ Ошибка: {result['error']}")

    elif data == "pay_by_transfer":
        days = context.user_data.get('pending_days')
        price = context.user_data.get('pending_price')
        server_id = context.user_data.get('pending_server_id')
        if not all([days, price, server_id]):
            await query.edit_message_text("❌ Ошибка: данные потеряны")
            return
        server = get_server_by_id(server_id)
        if not server:
            await query.edit_message_text("❌ Сервер не найден")
            return
        payment_methods = get_payment_methods()
        payment_text = "🏦 СПОСОБЫ ОПЛАТЫ:\n\n"
        for key, method in payment_methods.items():
            payment_text += f"{method['name']}:\n{method['details']}\n\n"
        text = f"💳 ОПЛАТА ПЕРЕВОДОМ\n\n📅 Срок: {days//30} мес.\n💰 Сумма: {price}₽\n🌍 Сервер: {server['name']}\n\n{payment_text}\n⚠️ В комментарии укажите: {user_id}\n\n✅ После перевода нажмите кнопку ниже"
        keyboard = [
            [InlineKeyboardButton("✅ Я перевел(а)", callback_data="payment_done")],
            [InlineKeyboardButton("🔙 Назад", callback_data="buy")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        add_pending(user_id, {'server_id': server_id, 'days': days, 'price': price})

    elif data == "payment_done":
        pending = get_pending_order(user_id)
        if not pending:
            await query.edit_message_text("❌ Заказ не найден", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))
            return
        await query.edit_message_text("📸 Чек получен!\n\nОжидайте подтверждения.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))
        for admin_id in get_admins():
            try:
                keyboard = [
                    [InlineKeyboardButton("✅ Выдать", callback_data=f"approve_{user_id}")],
                    [InlineKeyboardButton("❌ Отказать", callback_data=f"reject_{user_id}")]
                ]
                await context.bot.send_message(admin_id, f"💰 НОВЫЙ ПЛАТЕЖ!\n👤 Пользователь: {user_id}\n💰 Сумма: {pending['price']}₽", reply_markup=InlineKeyboardMarkup(keyboard))
            except:
                pass

    elif data.startswith("approve_topup_"):
        if not is_admin(query.from_user.id):
            await query.edit_message_text("⛔ Нет прав")
            return
        target_user_id = int(data.split("_")[2])
        pending = get_pending_topup(target_user_id)
        if not pending:
            await query.edit_message_text("❌ Заявка не найдена")
            return
        amount = pending.get('amount', 0)
        user_data = get_user(target_user_id)
        user_data['balance'] = user_data.get('balance', 0) + amount
        save_user(target_user_id, user_data)
        remove_pending_topup(target_user_id)
        add_transaction(target_user_id, amount, 'topup', f'Пополнение баланса на {amount}₽')
        await context.bot.send_message(
            target_user_id,
            f"✅ БАЛАНС ПОПОЛНЕН!\n\n💰 Сумма: {amount}₽\n💰 Новый баланс: {user_data['balance']}₽"
        )
        await query.edit_message_text(f"✅ Баланс пользователя {target_user_id} пополнен на {amount}₽")
        return

    elif data.startswith("reject_topup_"):
        if not is_admin(query.from_user.id):
            await query.edit_message_text("⛔ Нет прав")
            return
        target_user_id = int(data.split("_")[2])
        remove_pending_topup(target_user_id)
        await context.bot.send_message(target_user_id, "❌ Заявка на пополнение отклонена")
        await query.edit_message_text(f"❌ Заявка на пополнение отклонена")
        return

    elif data.startswith("approve_withdraw_"):
        await approve_withdraw(query, context)
        return

    elif data.startswith("reject_withdraw_"):
        await reject_withdraw(query, context)
        return

    elif data.startswith("approve_"):
        if not is_admin(query.from_user.id):
            await query.edit_message_text("⛔ Нет прав")
            return
        target_user_id = int(data.split("_")[1])
        pending = get_pending_order(target_user_id)
        if not pending:
            await query.edit_message_text("❌ Заказ не найден")
            return
        server = get_server_by_id(pending.get('server_id'))
        if not server:
            await query.edit_message_text("❌ Сервер не найден")
            return
        if not is_slot_available(server['id']):
            await query.edit_message_text("❌ На сервере нет свободных мест")
            return
        next_id = get_next_client_id()
        client_name = f"DubikVPN ({next_id})"
        email = f"user_{target_user_id}_{next_id}_{int(datetime.now().timestamp())}"
        result = create_subscription(server, email, pending['days'], client_name)
        if result['success']:
            user_data = get_user(target_user_id)
            if 'subscriptions' not in user_data:
                user_data['subscriptions'] = []
            user_data['subscriptions'].append({
                'server_id': server['id'],
                'server_name': server['name'],
                'purchase_date': datetime.now().isoformat(),
                'expiry_date': datetime.fromtimestamp(result['expiry_date']/1000).isoformat(),
                'days': pending['days'],
                'sub_link': result['sub_link'],
                'client_id': result['client_id'],
                'warning_sent': False,
                'is_free': False
            })
            save_user(target_user_id, user_data)
            remove_pending(target_user_id)
            update_server_used_slots(server['id'])
            add_transaction(target_user_id, -pending['price'], 'subscription', f'Оплата подписки на {pending["days"]} дней')
            qr_img = await generate_qr_code(result['sub_link'])
            await context.bot.send_message(
                target_user_id,
                f"✅ ПОДПИСКА АКТИВИРОВАНА!\n\n📅 Срок: {pending['days']//30} мес.\n📛 ID: {result['client_id']}\n\n🔗 {result['sub_link']}\n\n⚠️ Не более 3 устройств",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 На главную", callback_data="back_to_menu")]])
            )
            await context.bot.send_photo(chat_id=target_user_id, photo=qr_img, caption="📱 Ваш QR-код для подключения")
            await query.edit_message_text(f"✅ Подписка выдана пользователю {target_user_id}")
        else:
            await query.edit_message_text(f"❌ Ошибка: {result['error']}")
        return

    elif data.startswith("reject_"):
        if not is_admin(query.from_user.id):
            await query.edit_message_text("⛔ Нет прав")
            return
        target_user_id = int(data.split("_")[1])
        remove_pending(target_user_id)
        await context.bot.send_message(target_user_id, "❌ Платеж не подтвержден")
        await query.edit_message_text(f"❌ Платеж отклонен")
        return

    elif data == "admin_panel":
        if not is_admin(query.from_user.id):
            await query.edit_message_text("⛔ Нет доступа")
            return
        servers = get_servers()
        keyboard = [
            [InlineKeyboardButton("🔑 Выдать подписку", callback_data="admin_issue")],
            [InlineKeyboardButton("💰 Выдать баланс", callback_data="admin_give_balance")],
            [InlineKeyboardButton("💳 Редактировать способы оплаты", callback_data="admin_edit_payments")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("💾 Бекап", callback_data="admin_backup")],
            [InlineKeyboardButton("💳 Заявки на вывод", callback_data="admin_withdrawals")],
            [InlineKeyboardButton("📊 Просмотр статистики", callback_data="view_stats")],
            [InlineKeyboardButton("🔍 Поиск по ID", callback_data="admin_search")],
            [InlineKeyboardButton("➕ Добавить сервер", callback_data="admin_add_server")],
            [InlineKeyboardButton("👑 Выдать админку", callback_data="admin_grant")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]
        for s in servers:
            used = get_server_used_slots(s['id'])
            max_slots = s.get('max_slots', '∞')
            slots_text = f"{used}/{max_slots}" if max_slots != '∞' and max_slots else "∞"
            keyboard.append([InlineKeyboardButton(f"❌ Удалить {s['name']} [{slots_text}]", callback_data=f"admin_del_server_{s['id']}")])
        await query.edit_message_text("🛡️ Админ панель", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_withdrawals":
        if not is_admin(query.from_user.id):
            await query.edit_message_text("⛔ Нет доступа")
            return
        pending_withdrawals = load_data("pending_withdrawals.json", [])
        pending = [w for w in pending_withdrawals if w['status'] == 'pending']
        if not pending:
            await query.edit_message_text("❌ Нет активных заявок на вывод", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))
            return
        text = f"💳 ЗАЯВКИ НА ВЫВОД ({len(pending)}):\n\n"
        for i, w in enumerate(pending, 1):
            text += f"{i}. 👤 Пользователь: {w['user_id']}\n   💰 Сумма: {w['amount']:.2f}₽\n   💳 Карта: {w['card_number']}\n   👤 Получатель: {w['full_name']}\n   📅 Дата: {datetime.fromisoformat(w['date']).strftime('%d.%m.%Y %H:%M')}\n   ---\n\n"
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_backup":
        await admin_backup(query, context)
    elif data == "admin_restore_backup":
        await admin_restore_backup(query, context)
    elif data.startswith("restore_backup_"):
        await process_restore_backup(query, context)
    elif data.startswith("confirm_restore_"):
        await confirm_restore_backup(query, context)

    elif data == "admin_issue":
        context.user_data['admin_action'] = 'issue_days'
        await query.edit_message_text("📅 Введите количество дней:")

    elif data == "admin_give_balance":
        context.user_data['admin_action'] = 'give_balance_user'
        await query.edit_message_text("💰 Введите Telegram ID пользователя:")

    elif data == "admin_edit_payments":
        if not is_admin(query.from_user.id):
            await query.edit_message_text("⛔ Нет прав")
            return
        keyboard = [
            [InlineKeyboardButton("➕ Добавить способ оплаты", callback_data="admin_add_payment")],
            [InlineKeyboardButton("✏️ Редактировать способ", callback_data="admin_edit_payment")],
            [InlineKeyboardButton("🗑️ Удалить способ", callback_data="admin_delete_payment")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]
        payment_methods = get_payment_methods()
        text = "💳 СПОСОБЫ ОПЛАТЫ:\n\n"
        for key, method in payment_methods.items():
            text += f"{key}: {method['name']}\n{method['details']}\n\n"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "admin_add_payment":
        context.user_data['admin_action'] = 'add_payment_key'
        await query.edit_message_text("Введите ключ для нового способа оплаты (например: card, sbp, usdt):")

    elif data == "admin_edit_payment":
        context.user_data['admin_action'] = 'edit_payment_key'
        payment_methods = get_payment_methods()
        keyboard = []
        for key in payment_methods.keys():
            keyboard.append([InlineKeyboardButton(f"✏️ {key}", callback_data=f"edit_payment_{key}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_edit_payments")])
        await query.edit_message_text("Выберите способ оплаты для редактирования:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("edit_payment_"):
        key = data.split("_")[2]
        context.user_data['edit_payment_key'] = key
        context.user_data['admin_action'] = 'edit_payment_name'
        await query.edit_message_text(f"Введите новое название для '{key}':")

    elif data == "admin_delete_payment":
        context.user_data['admin_action'] = 'delete_payment_key'
        payment_methods = get_payment_methods()
        keyboard = []
        for key in payment_methods.keys():
            keyboard.append([InlineKeyboardButton(f"🗑️ {key}", callback_data=f"delete_payment_{key}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_edit_payments")])
        await query.edit_message_text("Выберите способ оплаты для удаления:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("delete_payment_"):
        key = data.split("_")[2]
        payment_methods = get_payment_methods()
        if key in payment_methods:
            del payment_methods[key]
            save_payment_methods(payment_methods)
            await query.edit_message_text(f"✅ Способ оплаты '{key}' удален")
        else:
            await query.edit_message_text("❌ Способ оплаты не найден")
        await button_handler(update, context)

    elif data == "admin_broadcast":
        context.user_data['admin_action'] = 'broadcast_message'
        await query.edit_message_text("📢 Введите сообщение для рассылки всем пользователям:")

    elif data == "admin_search":
        if not is_admin(query.from_user.id):
            await query.edit_message_text("⛔ Нет прав")
            return
        context.user_data['admin_action'] = 'search_user'
        await query.edit_message_text("🔍 Поиск пользователя\n\nВведите Telegram ID или ID ключа:")

    # ========== ДОБАВЛЕНИЕ НОВОГО СЕРВЕРА С ПОДДЕРЖКОЙ link_url ==========
    elif data == "admin_add_server":
        context.user_data['admin_action'] = 'add_server_url'
        await query.edit_message_text(
            "➕ Добавление нового сервера\n\n"
            "Введите URL панели сервера для API.\n"
            "Формат: https://vpn.heompvpn.pro:порт\n"
            "Или просто: vpn.heompvpn.pro\n\n"
            "⚠️ Если ссылки для подписок должны быть на другом домене — укажите это позже."
        )

    elif data == "admin_grant":
        context.user_data['admin_action'] = 'grant_admin'
        await query.edit_message_text("👑 Введите Telegram ID:")

    elif data.startswith("admin_del_server_"):
        server_id = int(data.split("_")[3])
        delete_server(server_id)
        await query.edit_message_text("✅ Сервер удалён")
        await button_handler(update, context)

# ========== ОБРАБОТКА ТЕКСТА ==========
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    log_user_activity(user_id, f'text_input: {text[:50]}')

    if context.user_data.get('withdraw_step'):
        await process_withdraw(update, context)
        return

    if context.user_data.get('transfer_step'):
        await process_transfer(update, context)
        return

    if not is_admin(user_id):
        return

    action = context.user_data.get('admin_action')

    if action == 'search_user':
        found_id, udata = search_user_by_id(text.strip())
        if not found_id:
            await update.message.reply_text("❌ Пользователь не найден")
            context.user_data['admin_action'] = None
            return
        try:
            chat = await context.bot.get_chat(int(found_id))
            name = chat.first_name or "No name"
            username = f"@{chat.username}" if chat.username else "нет username"
        except:
            name = "Неизвестно"
            username = "нет"
        subs = udata.get('subscriptions', [])
        subs_info = ""
        for s in subs:
            expiry = datetime.fromisoformat(s['expiry_date']).strftime('%d.%m.%Y')
            days_left = (datetime.fromisoformat(s['expiry_date']) - datetime.now()).days
            client_id = s.get('client_id', '???')
            subs_info += f"• {s['server_name']} (ID {client_id}) до {expiry} (осталось {days_left} дн.)\n"
        total = len(subs)
        free_status = "✅" if udata.get('got_free') else "❌"
        balance = udata.get('balance', 0)
        await update.message.reply_text(
            f"👤 Информация о пользователе\n\n"
            f"🆔 ID: {found_id}\n👤 Имя: {name}\n📱 Username: {username}\n"
            f"💰 Баланс: {balance}₽\n🔑 Всего подписок: {total}\n🎁 Бесплатная: {free_status}\n\n"
            f"📋 Подписки:\n{subs_info if subs_info else 'Нет активных подписок'}"
        )
        context.user_data['admin_action'] = None
        return

    if action == 'broadcast_message':
        users = get_all_users()
        success = 0
        fail = 0
        for uid in users:
            try:
                await context.bot.send_message(uid, f"📢 РАССЫЛКА\n\n{text}")
                success += 1
            except:
                fail += 1
        await update.message.reply_text(f"✅ Рассылка завершена!\n📤 Отправлено: {success}\n❌ Ошибок: {fail}")
        context.user_data['admin_action'] = None
        return

    if action == 'give_balance_user':
        try:
            target_id = int(text.strip())
            context.user_data['give_balance_target'] = target_id
            context.user_data['admin_action'] = 'give_balance_amount'
            await update.message.reply_text("💰 Введите сумму для начисления:")
        except:
            await update.message.reply_text("❌ Введите корректный ID")
        return

    if action == 'give_balance_amount':
        try:
            amount = int(text.strip())
            target_id = context.user_data.get('give_balance_target')
            if not target_id:
                await update.message.reply_text("❌ Ошибка: пользователь не найден")
                context.user_data['admin_action'] = None
                return
            user_data = get_user(target_id)
            user_data['balance'] = user_data.get('balance', 0) + amount
            save_user(target_id, user_data)
            add_transaction(target_id, amount, 'topup', f'Администратор начислил {amount}₽')
            await update.message.reply_text(f"✅ Баланс пользователя {target_id} пополнен на {amount}₽\n💰 Новый баланс: {user_data['balance']}₽")
            try:
                await context.bot.send_message(
                    target_id,
                    f"💰 БАЛАНС ПОПОЛНЕН!\n\nАдминистратор начислил вам {amount}₽\n💰 Новый баланс: {user_data['balance']}₽"
                )
            except:
                pass
            context.user_data['admin_action'] = None
            context.user_data.pop('give_balance_target', None)
        except:
            await update.message.reply_text("❌ Введите корректную сумму")

    if action == 'add_payment_key':
        context.user_data['payment_key'] = text.strip()
        context.user_data['admin_action'] = 'add_payment_name'
        await update.message.reply_text("Введите название способа оплаты (например: 💳 Банковская карта):")

    elif action == 'add_payment_name':
        key = context.user_data.get('payment_key')
        name = text.strip()
        context.user_data['admin_action'] = 'add_payment_details'
        context.user_data['payment_name'] = name
        await update.message.reply_text("Введите реквизиты для оплаты:")

    elif action == 'add_payment_details':
        key = context.user_data.get('payment_key')
        name = context.user_data.get('payment_name')
        details = text.strip()
        payment_methods = get_payment_methods()
        payment_methods[key] = {'name': name, 'details': details}
        save_payment_methods(payment_methods)
        await update.message.reply_text(f"✅ Способ оплаты '{key}' добавлен!")
        context.user_data['admin_action'] = None
        context.user_data.pop('payment_key', None)
        context.user_data.pop('payment_name', None)

    elif action == 'edit_payment_name':
        key = context.user_data.get('edit_payment_key')
        name = text.strip()
        context.user_data['admin_action'] = 'edit_payment_details'
        context.user_data['payment_name'] = name
        await update.message.reply_text(f"Введите новые реквизиты для '{key}':")

    elif action == 'edit_payment_details':
        key = context.user_data.get('edit_payment_key')
        name = context.user_data.get('payment_name')
        details = text.strip()
        payment_methods = get_payment_methods()
        if key in payment_methods:
            payment_methods[key] = {'name': name, 'details': details}
            save_payment_methods(payment_methods)
            await update.message.reply_text(f"✅ Способ оплаты '{key}' обновлен!")
        else:
            await update.message.reply_text("❌ Способ оплаты не найден")
        context.user_data['admin_action'] = None
        context.user_data.pop('edit_payment_key', None)
        context.user_data.pop('payment_name', None)

    elif action == 'delete_payment_key':
        pass

    # ========== НОВАЯ ОБРАБОТКА ДЛЯ ДОБАВЛЕНИЯ СЕРВЕРА С link_url ==========
    elif action == 'add_server_url':
        user_input = text.strip()
        full_url = extract_full_url_with_port(user_input)
        domain = extract_domain_from_url(user_input)

        if not domain:
            await update.message.reply_text(
                "❌ Не удалось извлечь домен из ссылки.\n\n"
                "Пожалуйста, введите корректный URL.\nПример: https://vpn.heompvpn.pro:31840"
            )
            context.user_data['admin_action'] = None
            return

        context.user_data['new_server_url'] = full_url
        context.user_data['new_server_domain'] = domain
        context.user_data['admin_action'] = 'add_server_name'

        await update.message.reply_text(
            f"✅ API URL определён: {full_url}\n\n"
            f"Теперь введите имя сервера (например: VPN Europe):"
        )

    elif action == 'add_server_name':
        context.user_data['new_server_name'] = text.strip()
        context.user_data['admin_action'] = 'add_server_link_url'
        await update.message.reply_text(
            f"🔗 Введите URL для ссылок подписок (или '-' чтобы использовать API URL):\n\n"
            f"Обычно это тот же домен, что и API URL, но может отличаться.\n"
            f"Пример: https://vpn.heompvpn.pro:2096\n\n"
            f"Если оставить как API URL, введите '-'"
        )

    elif action == 'add_server_link_url':
        user_input = text.strip()
        if user_input == '-':
            context.user_data['new_server_link_url'] = context.user_data.get('new_server_url')
        else:
            link_url = extract_full_url_with_port(user_input)
            if not extract_domain_from_url(user_input):
                await update.message.reply_text(
                    "❌ Не удалось извлечь домен из ссылки.\n\n"
                    "Пожалуйста, введите корректный URL.\nПример: https://vpn.heompvpn.pro:2096"
                )
                return
            context.user_data['new_server_link_url'] = link_url

        context.user_data['admin_action'] = 'add_server_token'
        await update.message.reply_text(
            f"🔑 Введите API Token для сервера:\n\n"
            f"Token можно найти в панели 3x-UI в настройках."
        )

    elif action == 'add_server_token':
        context.user_data['new_server_token'] = text.strip()
        context.user_data['admin_action'] = 'add_server_inbound'
        await update.message.reply_text(
            f"📋 Введите Inbound ID (цифру) для сервера:\n\n"
            f"Inbound ID можно найти в разделе 'Инбаунды' панели."
        )

    elif action == 'add_server_inbound':
        try:
            inbound_id = int(text.strip())
            context.user_data['temp_server'] = {
                'name': context.user_data.get('new_server_name'),
                'url': context.user_data.get('new_server_url'),
                'link_url': context.user_data.get('new_server_link_url'),
                'api_token': context.user_data.get('new_server_token'),
                'inbound_id': inbound_id
            }
            context.user_data['admin_action'] = 'add_server_limit'
            await update.message.reply_text(
                "📊 Лимит мест на сервере\n\n"
                "Введите максимальное количество подписок (0 - безлимит):"
            )
        except:
            await update.message.reply_text("❌ Inbound ID должен быть числом")
            context.user_data['admin_action'] = None

    elif action == 'add_server_limit':
        try:
            max_slots = int(text.strip())
            server = context.user_data.get('temp_server')

            if not server:
                await update.message.reply_text("❌ Ошибка: данные сервера потеряны")
                context.user_data['admin_action'] = None
                return

            server['max_slots'] = max_slots if max_slots > 0 else None
            server['used_slots'] = 0

            result = test_server_connection(server)
            if result['success']:
                add_server(server)
                limit_text = f"Лимит: {max_slots} мест" if max_slots > 0 else "Безлимит"
                await update.message.reply_text(
                    f"✅ Сервер '{server['name']}' добавлен!\n"
                    f"🌐 API URL: {server['url']}\n"
                    f"🔗 Ссылки для подписок: {server.get('link_url', server['url'])}\n"
                    f"{limit_text}\n\n"
                    f"Теперь пользователи смогут покупать подписки на этом сервере."
                )
            else:
                await update.message.reply_text(
                    f"❌ Ошибка подключения к серверу:\n{result['msg']}\n\n"
                    f"Проверьте URL, Token и Inbound ID."
                )
        except:
            await update.message.reply_text("❌ Введите число")

        context.user_data['admin_action'] = None
        context.user_data.pop('temp_server', None)
        for key in ['new_server_name', 'new_server_url', 'new_server_link_url', 'new_server_token', 'new_server_domain']:
            context.user_data.pop(key, None)

    elif action == 'grant_admin':
        try:
            target_id = int(text.strip())
            add_admin(target_id)
            await update.message.reply_text(f"✅ Админ {target_id} добавлен")
        except:
            await update.message.reply_text("❌ Ошибка")
        context.user_data['admin_action'] = None

    elif action == 'issue_days':
        try:
            days = int(text.strip())
            servers = get_servers()
            if not servers:
                await update.message.reply_text("❌ Нет серверов")
                context.user_data['admin_action'] = None
                return
            server = servers[0]
            next_id = get_next_client_id()
            client_name = f"DubikVPN ({next_id})"
            email = f"admin_{next_id}_{int(datetime.now().timestamp())}"
            result = create_subscription(server, email, days, client_name)
            if result['success']:
                await update.message.reply_text(
                    f"✅ Подписка на {days} дней:\n\n📛 ID: {result['client_id']}\n\n🔗 {result['sub_link']}"
                )
                update_server_used_slots(server['id'])
            else:
                await update.message.reply_text(f"❌ Ошибка: {result['error']}")
        except:
            await update.message.reply_text("❌ Введите число")
        context.user_data['admin_action'] = None

    elif action == 'grant_admin':
        try:
            target_id = int(text.strip())
            add_admin(target_id)
            await update.message.reply_text(f"✅ Админ {target_id} добавлен")
        except:
            await update.message.reply_text("❌ Ошибка")
        context.user_data['admin_action'] = None

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    if app.job_queue:
        app.job_queue.run_repeating(check_expiring_subs, interval=3600, first=10)
        app.job_queue.run_repeating(auto_renew_check, interval=3600, first=30)
        app.job_queue.run_repeating(check_renewal_reminders, interval=3600, first=60)

    print("🤖 Бот DubikVPN запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
