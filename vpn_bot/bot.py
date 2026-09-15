# -*- coding: utf-8 -*-
# test_bot.py
import logging
import os
import re
import html as html_module
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, LabeledPrice, Invoice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, PreCheckoutQueryHandler
from telegram.request import HTTPXRequest

from config import BOT_TOKEN, MAIN_ADMIN_ID, PRICES, FREE_PERIOD_DAYS, PANEL_URL, SUBSCRIPTION_PATH, CHANNEL_ID, CHANNEL_LINK, PROXY_URL, MAX_DEVICES
from database import *
from panel_api import create_subscription, test_server_connection, get_client_usage
import qrcode
from io import BytesIO

logging.basicConfig(level=logging.INFO)

TG_CAPTION_LIMIT = 1024
TG_TEXT_LIMIT = 4096


# ============================================================
#                    HTML ХЕЛПЕРЫ
# ============================================================
def html_escape(text):
    if text is None:
        return ''
    return html_module.escape(str(text), quote=False)


def html_code(text):
    """Тап в Telegram = копирование."""
    return f"<code>{html_escape(text)}</code>"


# ============================================================
#                    PAYMENT METHODS
# ============================================================
def get_payment_methods():
    return load_data("payment_methods.json", {
        'sbp_cards': {'name': '💳 СБП/Карты', 'details': 'Оплата через Lava'},
        'card': {'name': '💳 Банковская карта', 'details': 'Карта: 2200 1234 5678 9012\nПолучатель: Иванов Иван'},
        'sbp': {'name': '📱 СБП', 'details': 'Номер: +7 999 123-45-67\nПолучатель: Иванов Иван'},
        'usdt': {'name': '🪙 USDT (TRC20)', 'details': 'Адрес: TXxxx...'}
    })


def save_payment_methods(methods):
    save_data("payment_methods.json", methods)


def extract_domain_from_url(url):
    url = url.strip()
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url
    parsed = re.match(r'(https?://[^:/]+)', url)
    if parsed:
        return parsed.group(1)
    return None


def extract_full_url_with_port(url):
    url = url.strip()
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url
    return url


MENU_BUTTON = ReplyKeyboardMarkup(
    [[KeyboardButton("👤 Личный кабинет")]],
    resize_keyboard=True,
    one_time_keyboard=False
)


# ============================================================
#                    ХЕЛПЕРЫ ОТПРАВКИ
# ============================================================
async def generate_qr_code(data):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_byte_arr = BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return img_byte_arr


async def safe_edit_message(query, text, keyboard=None, parse_mode=None):
    if len(text) > TG_TEXT_LIMIT:
        text = text[:TG_TEXT_LIMIT - 30] + "\n\n... (обрезано)"

    try:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=parse_mode)
        return
    except Exception as e:
        err = str(e).lower()
        if "not modified" in err:
            return
        if "parse" in err or "entities" in err or "tag" in err:
            try:
                await query.edit_message_text(text, reply_markup=keyboard, parse_mode=None)
                return
            except Exception as e2:
                if "not modified" in str(e2).lower():
                    return
                logging.warning(f"safe_edit retry (no parse) failed: {e2}")

    try:
        await query.delete_message()
    except Exception:
        pass
    try:
        await query.message.reply_text(text, reply_markup=keyboard, parse_mode=None)
    except Exception as e3:
        logging.error(f"safe_edit final fallback error: {e3}")


async def send_with_banner(update_or_query, banner_name, text, keyboard=None, parse_mode='HTML'):
    banner_path = f"banner/{banner_name}"
    is_callback = hasattr(update_or_query, 'edit_message_text')
    has_banner = os.path.exists(banner_path)
    caption_too_long = len(text) > (TG_CAPTION_LIMIT - 50)
    text_too_long = len(text) > (TG_TEXT_LIMIT - 100)

    if has_banner and not caption_too_long and not text_too_long:
        for pm in [parse_mode, None]:
            try:
                if is_callback:
                    try:
                        await update_or_query.delete_message()
                    except Exception:
                        pass
                with open(banner_path, 'rb') as f:
                    await update_or_query.message.reply_photo(
                        photo=f, caption=text, reply_markup=keyboard, parse_mode=pm
                    )
                return
            except Exception as e:
                err = str(e).lower()
                if "parse" in err or "entities" in err or "tag" in err:
                    continue
                logging.error(f"send_with_banner photo error (pm={pm}): {e}")
                break

    if has_banner and (caption_too_long or text_too_long):
        try:
            if is_callback:
                try:
                    await update_or_query.delete_message()
                except Exception:
                    pass
            with open(banner_path, 'rb') as f:
                await update_or_query.message.reply_photo(photo=f)

            chunks = [text] if not text_too_long else [text[i:i + TG_TEXT_LIMIT - 100] for i in range(0, len(text), TG_TEXT_LIMIT - 100)]
            for idx, chunk in enumerate(chunks):
                kb = keyboard if idx == len(chunks) - 1 else None
                sent = False
                for pm in [parse_mode, None]:
                    try:
                        await update_or_query.message.reply_text(chunk, reply_markup=kb, parse_mode=pm)
                        sent = True
                        break
                    except Exception as e:
                        err = str(e).lower()
                        if "parse" in err or "entities" in err or "tag" in err:
                            continue
                        logging.error(f"long text send error: {e}")
                        break
                if not sent:
                    try:
                        await update_or_query.message.reply_text(chunk, reply_markup=kb, parse_mode=None)
                    except Exception as e:
                        logging.error(f"long text final error: {e}")
            return
        except Exception as e:
            logging.error(f"send_with_banner (long) outer error: {e}")

    chunks = [text] if not text_too_long else [text[i:i + TG_TEXT_LIMIT - 100] for i in range(0, len(text), TG_TEXT_LIMIT - 100)]

    if is_callback and len(chunks) == 1:
        await safe_edit_message(update_or_query, chunks[0], keyboard, parse_mode)
        return

    if is_callback:
        try:
            await update_or_query.delete_message()
        except Exception:
            pass

    for idx, chunk in enumerate(chunks):
        kb = keyboard if idx == len(chunks) - 1 else None
        sent = False
        for pm in [parse_mode, None]:
            try:
                await update_or_query.message.reply_text(chunk, reply_markup=kb, parse_mode=pm)
                sent = True
                break
            except Exception as e:
                err = str(e).lower()
                if "parse" in err or "entities" in err or "tag" in err:
                    continue
                logging.error(f"text send error: {e}")
                break
        if not sent:
            try:
                await update_or_query.message.reply_text(chunk, reply_markup=kb, parse_mode=None)
            except Exception as e:
                logging.error(f"text final error: {e}")


async def send_sub_photo(bot, chat_id, sub_link, days, client_id, extra_text='', keyboard=None):
    """Отправляет QR + caption с <code>ссылкой</code>. Тап = копирование."""
    qr_img = await generate_qr_code(sub_link)
    caption = (
        f"✅ <b>ПОДПИСКА АКТИВИРОВАНА!</b>\n\n"
        f"📅 Срок: {days} дней\n"
        f"📛 ID: {html_escape(client_id)}\n"
    )
    if extra_text:
        caption += f"{extra_text}\n"
    caption += (
        f"\n🔗 {html_code(sub_link)}\n\n"
        f"⚠️ Максимум {MAX_DEVICES} устройств\n\n"
        f"👆 Тапни по ссылке — она скопируется"
    )

    try:
        await bot.send_photo(
            chat_id=chat_id,
            photo=qr_img,
            caption=caption,
            parse_mode='HTML',
            reply_markup=keyboard,
        )
        return
    except Exception as e:
        logging.error(f"send_sub_photo error: {e}")

    try:
        await bot.send_message(chat_id=chat_id, text=caption, parse_mode='HTML', reply_markup=keyboard)
    except Exception as e:
        logging.error(f"send_sub_photo final fallback: {e}")
        try:
            await bot.send_message(chat_id=chat_id, text=caption)
        except Exception as e2:
            logging.error(f"send_sub_photo plain fallback: {e2}")


async def check_channel_subscription(context, user_id):
    try:
        chat_member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.error(f"Ошибка проверки подписки для {user_id}: {e}")
        return False


# ============================================================
#                    ПОДПИСКА НА КАНАЛ
# ============================================================
async def verify_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    is_subscribed = await check_channel_subscription(context, user_id)

    if is_subscribed:
        set_user_verified(user_id)
        await query.edit_message_text(
            "✅ Отлично! Подписка подтверждена!\n\n"
            "Теперь у вас есть доступ к боту. Нажмите /start, чтобы продолжить.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 В бот", callback_data="go_to_bot")]
            ])
        )
    else:
        await query.answer("❌ Вы ещё не подписались на канал! Подпишитесь и попробуйте снова.", show_alert=True)


async def go_to_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await check_subscription_and_show_menu(query, context, query.from_user.id, is_callback=True)


async def check_subscription_and_show_menu(update_or_query, context, user_id, is_callback=False):
    is_subscribed = await check_channel_subscription(context, user_id)

    if not is_subscribed:
        text = (
            "🔒 <b>Подписка на канал обязательна!</b>\n\n"
            "Вы отписались от нашего новостного канала.\n"
            "Чтобы продолжить пользоваться ботом, подпишитесь снова:\n"
            f"📢 <b>{html_escape(CHANNEL_LINK)}</b>\n\n"
            "После подписки нажмите <b>«Я подписался»</b>."
        )
        keyboard = [
            [InlineKeyboardButton("📢 Перейти в канал", url=CHANNEL_LINK)],
            [InlineKeyboardButton("✅ Я подписался", callback_data="verify_subscription")]
        ]
        if is_callback:
            await safe_edit_message(update_or_query, text, InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        else:
            await update_or_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return False

    await show_main_menu(update_or_query, context, user_id)
    return True


# ============================================================
#                    ПРОФИЛЬ / ТАРИФЫ
# ============================================================
def get_user_tariff_info(user_data):
    subs = user_data.get('subscriptions', [])
    now = datetime.now()
    active_subs = [s for s in subs if datetime.fromisoformat(s['expiry_date']) > now]

    if not active_subs:
        return "Нет активной подписки", 0, None

    best_sub = max(active_subs, key=lambda x: (datetime.fromisoformat(x['expiry_date']) - now).days)
    days_left = (datetime.fromisoformat(best_sub['expiry_date']) - now).days

    if best_sub.get('is_free'):
        tariff = "Пробный (3 дня)"
    else:
        d = best_sub.get('days', 0)
        if d == 30:
            tariff = "Безлимит 1 месяц"
        elif d == 90:
            tariff = "Безлимит 3 месяца"
        elif d == 180:
            tariff = "Безлимит 6 месяцев"
        else:
            tariff = f"Безлимит {d} дней"

    return tariff, days_left, best_sub


def clean_expired_subs(user_id):
    user_data = get_user(user_id)
    now = datetime.now()
    if 'subscriptions' in user_data:
        user_data['subscriptions'] = [
            s for s in user_data['subscriptions']
            if datetime.fromisoformat(s['expiry_date']) > now
        ]
        save_user(user_id, user_data)
    return user_data


# ============================================================
#                    ФОНОВЫЕ ЗАДАЧИ
# ============================================================
async def auto_backup(context):
    try:
        backup_file, timestamp = create_backup()
        admins = get_admins()
        for admin_id in admins:
            try:
                with open(backup_file, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=admin_id,
                        document=f,
                        caption=f"📦 АВТОМАТИЧЕСКИЙ БЕКАП\n\n✅ Бекап создан!\n📅 Дата: {timestamp}"
                    )
            except Exception:
                pass
        backup_dir = "backups"
        if os.path.exists(backup_dir):
            backups = sorted([os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('.zip')], key=os.path.getmtime, reverse=True)
            for old_backup in backups[10:]:
                try:
                    os.remove(old_backup)
                except Exception:
                    pass
    except Exception:
        pass


async def check_expiring_subs(context):
    users = get_users()
    now = datetime.now()
    for uid_str, u_data in users.items():
        try:
            uid = int(uid_str)
        except Exception:
            continue
        try:
            u_data = clean_expired_subs(uid)
            for sub in u_data.get('subscriptions', []):
                expiry = datetime.fromisoformat(sub['expiry_date'])
                left = (expiry - now).total_seconds() / 3600
                if 23 <= left <= 25 and not sub.get('warning_sent'):
                    sub['warning_sent'] = True
                    save_user(uid, u_data)
                    try:
                        await context.bot.send_message(uid, f"⚠️ ВНИМАНИЕ!\n\nВаша подписка истекает через 24 часа.\n\n📅 Дата окончания: {expiry.strftime('%d.%m.%Y %H:%M')}")
                    except Exception:
                        pass
        except Exception:
            pass


async def auto_renew_check(context):
    renewed = auto_renew_subscriptions()
    if renewed:
        for user_id in renewed:
            try:
                await context.bot.send_message(user_id, "🔄 ПОДПИСКА АВТОМАТИЧЕСКИ ПРОДЛЕНА!")
            except Exception:
                pass


async def check_renewal_reminders(context):
    users = get_users()
    now = datetime.now()
    for uid_str, user_data in users.items():
        try:
            uid = int(uid_str)
        except Exception:
            continue
        try:
            user_data = clean_expired_subs(uid)
            for sub in user_data.get('subscriptions', []):
                expiry = datetime.fromisoformat(sub['expiry_date'])
                days_left = (expiry - now).days
                reminder_sent = sub.get('reminder_sent', {})
                reminders = {7: 'неделю', 3: '3 дня', 1: 'завтра'}
                for days, text in reminders.items():
                    if days_left == days and not reminder_sent.get(str(days), False):
                        try:
                            price = PRICES.get(sub.get('days', 30), 150)
                            balance = user_data.get('balance', 0)
                            msg = f"⏰ НАПОМИНАНИЕ!\n\nВаша подписка истекает через {text}!\n📅 Дата окончания: {expiry.strftime('%d.%m.%Y %H:%M')}\n\n"
                            if balance >= price:
                                msg += f"💰 На вашем балансе достаточно средств ({balance}₽)\n✅ Подписка будет автоматически продлена!"
                            else:
                                msg += f"💰 На балансе: {balance}₽ (нужно {price}₽)\n💳 Пополните баланс!"
                            await context.bot.send_message(uid, msg)
                            if 'reminder_sent' not in sub:
                                sub['reminder_sent'] = {}
                            sub['reminder_sent'][str(days)] = True
                            save_user(uid, user_data)
                        except Exception:
                            pass
        except Exception:
            pass


# ============================================================
#                    START
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    log_user_activity(user_id, 'start')
    await check_subscription_and_show_menu(update, context, user_id, is_callback=False)


# ============================================================
#                    ГЛАВНОЕ МЕНЮ
# ============================================================
async def show_main_menu(update_or_query, context, user_id=None):
    if user_id is None:
        if hasattr(update_or_query, 'from_user'):
            user_id = update_or_query.from_user.id
        else:
            user_id = update_or_query.effective_user.id

    user_data = clean_expired_subs(user_id)

    if hasattr(update_or_query, 'effective_user'):
        user = update_or_query.effective_user
    elif hasattr(update_or_query, 'from_user'):
        user = update_or_query.from_user
    else:
        user = None

    if user:
        user_data['username'] = user.username or ''
        user_data['first_name'] = user.first_name or ''
        save_user(user_id, user_data)

    balance = user_data.get('balance', 0)
    tariff, days_left, active_sub = get_user_tariff_info(user_data)
    first_name = user_data.get('first_name') or 'Пользователь'

    text = f"👤 <b>Профиль:</b>\n"
    text += f"📝 Имя: {html_escape(first_name)}\n"
    text += f"🆔 <code>{user_id}</code>\n"
    text += f"💳 Баланс: {balance} ₽\n\n"

    if active_sub:
        sub_link = active_sub.get('sub_link', 'Нет ссылки')
        text += f"🔑 <b>Ваша подписка:</b>\n{html_code(sub_link)}\n\n"
        text += f"💎 Тариф: {tariff}\n"

        client_uuid = active_sub.get('uuid')
        if client_uuid:
            try:
                servers = get_servers()
                if servers:
                    main_server = servers[0]
                    usage = get_client_usage(main_server, client_uuid)
                    if usage:
                        online = usage.get('online', 0)
                        text += f"\n📱 Подключено устройств: {online}/{MAX_DEVICES}"
                        if online >= MAX_DEVICES:
                            text += f" ⚠️ ЛИМИТ ДОСТИГНУТ!"
                    else:
                        text += f"\n📱 Устройства: Не удалось проверить"
            except Exception as e:
                logging.error(f"Ошибка проверки устройств: {e}")
                text += f"\n📱 Устройства: Не удалось проверить"

        expiry_date = datetime.fromisoformat(active_sub['expiry_date'])
        if expiry_date > datetime.now():
            text += f"\n📅 Статус: ✅ Активна\n⏳ Действует до: {expiry_date.strftime('%d.%m.%Y %H:%M')}\n📆 Осталось дней: {days_left}"
        else:
            text += f"\n📅 Статус: ❌ Истекла\n\n⚠️ Подписка истекла. Продлите её."
    else:
        text += f"🔑 Ваша подписка:\n❌ Нет активной подписки\n\n⚠️ Подписка истекла. Продлите её."

    keyboard = [
        [InlineKeyboardButton("🛒 Купить подписку", callback_data="buy")],
        [InlineKeyboardButton("🔑 Мои подписки", callback_data="my_subs")],
    ]

    if not user_data.get('got_free', False):
        keyboard.append([InlineKeyboardButton("🎁 Бесплатно 3 дня", callback_data="free_sub")])

    keyboard.append([InlineKeyboardButton("📖 Инструкция", callback_data="instructions")])
    keyboard.append([InlineKeyboardButton("ℹ️ О сервисе", callback_data="about")])

    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("🛡️ Админ панель", callback_data="admin_panel")])

    await send_with_banner(update_or_query, "cabinet.png", text, InlineKeyboardMarkup(keyboard), parse_mode='HTML')


# ============================================================
#                    СРОКИ / ПОКУПКА
# ============================================================
async def show_durations(query, context):
    keyboard = [
        [InlineKeyboardButton("📅 30 дней (150₽)", callback_data="duration_30")],
        [InlineKeyboardButton("📅 90 дней (350₽)", callback_data="duration_90")],
        [InlineKeyboardButton("📅 180 дней (700₽)", callback_data="duration_180")],
        [InlineKeyboardButton("💳 Пополнить баланс", callback_data="topup_balance")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
    ]
    await send_with_banner(query, "key.png", "💎 Выберите срок подписки:", InlineKeyboardMarkup(keyboard), parse_mode=None)


async def process_duration(query, context):
    """Выбор тарифа → только кнопка СБП/Карты (Lava)."""
    days = int(query.data.split("_")[1])
    price = PRICES.get(days)
    context.user_data['pending_days'] = days
    context.user_data['pending_price'] = price

    keyboard = [
        [InlineKeyboardButton("💳 СБП / Карты", callback_data=f"lava_buy_{days}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="buy")]
    ]
    text = f"💎 <b>ОПЛАТА ПОДПИСКИ</b>\n\n📅 Срок: {days} дней\n💰 Стоимость: {price}₽\n\n👇 Нажмите для оплаты:"
    await send_with_banner(query, "key.png", text, InlineKeyboardMarkup(keyboard), parse_mode='HTML')


# ============================================================
#                    ПОПОЛНЕНИЕ БАЛАНСА
# ============================================================
async def topup_balance(query, context):
    text = "💳 ПОПОЛНЕНИЕ БАЛАНСА\n\nВыберите способ оплаты:"
    keyboard = [
        [InlineKeyboardButton("💳 СБП / Карты", callback_data="topup_sbp_cards")],
        [InlineKeyboardButton("⭐ Telegram Stars (мгновенно)", callback_data="topup_stars")],
        [InlineKeyboardButton("🔙 Назад", callback_data="buy")]
    ]
    await send_with_banner(query, "key.png", text, InlineKeyboardMarkup(keyboard), parse_mode=None)


async def topup_sbp_cards(query, context):
    text = (
        "💳 ВЫБЕРИТЕ ТАРИФ ДЛЯ ПОПОЛНЕНИЯ\n\n"
        "Оплата картой, СБП или LAVA через Lava.\n"
        "Средства зачисляются автоматически за 1–2 минуты."
    )
    keyboard = [
        [InlineKeyboardButton("📅 30 дней — 150₽", callback_data="lava_buy_30")],
        [InlineKeyboardButton("📅 90 дней — 350₽", callback_data="lava_buy_90")],
        [InlineKeyboardButton("📅 180 дней — 700₽", callback_data="lava_buy_180")],
        [InlineKeyboardButton("🔙 Назад", callback_data="topup_balance")],
    ]
    await send_with_banner(query, "key.png", text, InlineKeyboardMarkup(keyboard), parse_mode=None)


async def topup_stars(query, context):
    text = "⭐ ПОПОЛНЕНИЕ ЧЕРЕЗ STARS\n\nВыберите пакет:\n"
    keyboard = [
        [InlineKeyboardButton("⭐ 100 Stars (30 дней, 150₽)", callback_data="stars_topup_30_100")],
        [InlineKeyboardButton("⭐ 250 Stars (90 дней, 350₽)", callback_data="stars_topup_90_250")],
        [InlineKeyboardButton("⭐ 500 Stars (180 дней, 700₽)", callback_data="stars_topup_180_500")],
        [InlineKeyboardButton("🔙 Назад", callback_data="topup_balance")]
    ]
    await send_with_banner(query, "key.png", text, InlineKeyboardMarkup(keyboard), parse_mode=None)


# ============================================================
#                    LAVA — ОПЛАТА
# ============================================================
async def process_lava_buy(query, context):
    from payments import create_invoice

    days = int(query.data.replace("lava_buy_", ""))
    price = PRICES.get(days, 150)
    user_id = query.from_user.id

    await safe_edit_message(query, "⏳ Создаю счёт...")

    result = create_invoice(user_id, price, days)

    if not result.get('success'):
        await safe_edit_message(
            query,
            f"❌ Ошибка создания счёта:\n{html_escape(result.get('error', 'unknown'))}",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data=f"duration_{days}")]
            ]),
            parse_mode='HTML',
        )
        return

    payment_url = result.get('payment_url')
    invoice_id = result.get('invoice_id')

    text = (
        f"💳 <b>СЧЁТ СОЗДАН</b>\n\n"
        f"📅 Срок: {days} дней\n"
        f"💰 Сумма: {price}₽\n"
        f"🆔 Счёт: {html_code(invoice_id)}\n\n"
        f"👇 Нажмите кнопку ниже для оплаты:"
    )
    keyboard = [
        [InlineKeyboardButton("💳 Перейти к оплате", url=payment_url)],
        [InlineKeyboardButton("✅ Я оплатил", callback_data=f"lava_check_{invoice_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"duration_{days}")],
    ]
    await safe_edit_message(query, text, InlineKeyboardMarkup(keyboard), parse_mode='HTML')


async def check_lava_payment(query, context):
    from payments import check_invoice_status, get_pending, mark_paid

    invoice_id = query.data.replace("lava_check_", "")
    result = check_invoice_status(invoice_id=invoice_id)

    if not result.get('success'):
        await query.answer("❌ Ошибка проверки, попробуйте позже", show_alert=True)
        return

    status = result.get('status')

    if status == 'success':
        p = get_pending(invoice_id)
        if p and p.get('status') != 'paid':
            user_id = p['user_id']
            days = p['days']
            sub = create_subscription(None, user_id, days, f"Lava {invoice_id}")
            if sub.get('success'):
                ud = get_user(user_id)
                if 'subscriptions' not in ud:
                    ud['subscriptions'] = []
                ud['subscriptions'].append({
                    'purchase_date': datetime.now().isoformat(),
                    'expiry_date': datetime.fromtimestamp(sub['expiry_date'] / 1000).isoformat(),
                    'days': days,
                    'sub_link': sub['sub_link'],
                    'client_id': sub['client_id'],
                    'client_number': sub.get('client_number'),
                    'email': sub.get('email'),
                    'servers': sub.get('servers', []),
                    'servers_count': sub.get('servers_count', 1),
                    'warning_sent': False,
                    'is_free': False,
                    'source': 'purchase',
                    'created_by': None,
                    'created_at': datetime.now().isoformat(),
                    'totalGB': 0,
                    'usedGB': 0,
                    'uuid': sub.get('uuid'),
                    'blocked': False,
                    'blocked_reason': None,
                    'blocked_date': None,
                })
                save_user(user_id, ud)
                mark_paid(invoice_id)

        await query.answer("✅ Оплата подтверждена!", show_alert=True)
        await safe_edit_message(
            query,
            "✅ <b>Оплата получена!</b>\n\nПодписка активируется в течение минуты. Проверьте «🔑 Мои подписки».",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔑 Мои подписки", callback_data="my_subs")],
                [InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu")],
            ]),
            parse_mode='HTML',
        )
    elif status == 'created':
        await query.answer("⏳ Счёт ещё не оплачен", show_alert=True)
    elif status in ('fail', 'expired'):
        await query.answer(f"❌ Счёт {status}. Создайте новый.", show_alert=True)
    elif status == 'refund':
        await query.answer("↩️ По счёту сделан возврат", show_alert=True)
    else:
        await query.answer(f"Статус: {status}", show_alert=True)


# ============================================================
#                    STARS
# ============================================================
async def process_stars_topup(query, context):
    parts = query.data.split("_")
    days = int(parts[2])
    stars_amount = int(parts[3])
    context.user_data['pending_days'] = days
    context.user_data['pending_price'] = stars_amount

    await query.edit_message_text(
        f"⭐ Вы выбрали пополнение на {stars_amount} Stars\n📅 Срок: {days} дней\n\nНажмите кнопку ниже для оплаты:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Оплатить Stars", callback_data=f"send_stars_invoice_{days}_{stars_amount}")]
        ])
    )


async def send_stars_invoice(update, context):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    days = int(parts[3])
    stars_amount = int(parts[4])
    context.user_data['pending_days'] = days
    context.user_data['pending_price'] = stars_amount

    invoice = Invoice(
        title="⭐ Пополнение баланса",
        description=f"Пополнение на {stars_amount} Stars через Telegram Stars\n1 Star = 1 ₽\nСумма: {stars_amount} Stars\nСрок: {days} дней",
        currency="XTR",
        prices=[LabeledPrice(label=f"{stars_amount} Stars", amount=stars_amount)],
        start_parameter=f"topup_{query.from_user.id}_{stars_amount}"
    )

    await query.message.reply_invoice(
        invoice=invoice,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Оплатить", callback_data=f"pay_stars_{days}_{stars_amount}")]
        ])
    )


async def pay_stars_callback(update, context):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    days = int(parts[2])
    stars_amount = int(parts[3])

    invoice = Invoice(
        title="⭐ Пополнение баланса",
        description=f"Пополнение на {stars_amount} Stars через Telegram Stars\n1 Star = 1 ₽\nСумма: {stars_amount} Stars\nСрок: {days} дней",
        currency="XTR",
        prices=[LabeledPrice(label=f"{stars_amount} Stars", amount=stars_amount)],
        start_parameter=f"topup_{query.from_user.id}_{stars_amount}"
    )

    await query.message.reply_invoice(
        invoice=invoice,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Оплатить", callback_data=f"pay_stars_{days}_{stars_amount}")]
        ])
    )


async def pre_checkout_query_handler(update, context):
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment_handler(update, context):
    user_id = update.effective_user.id
    payment = update.message.successful_payment
    amount_stars = payment.total_amount

    days = context.user_data.get('pending_days', 30)
    stars_amount = context.user_data.get('pending_price', amount_stars)

    if not days or not stars_amount:
        try:
            start_param = payment.invoice_payload or ""
            parts = start_param.split('_')
            if len(parts) >= 3:
                stars_amount = int(parts[2])
                if stars_amount >= 500:
                    days = 180
                elif stars_amount >= 250:
                    days = 90
                else:
                    days = 30
        except Exception:
            days = 30
            stars_amount = amount_stars

    user_data = get_user(user_id)
    result = create_subscription(None, user_id, days, f"User {user_id} (Stars)")

    if result['success']:
        if 'subscriptions' not in user_data:
            user_data['subscriptions'] = []
        user_data['subscriptions'].append({
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
            'blocked_date': None
        })
        save_user(user_id, user_data)

        for server in get_servers():
            update_server_used_slots(server['id'])

        add_transaction(user_id, -stars_amount, 'subscription', f'Оплата подписки на {days} дней через Stars')

        await send_sub_photo(
            context.bot, user_id,
            result['sub_link'], days, result['client_id'],
            extra_text=f"⭐ Оплачено: {amount_stars} Stars\n🌍 Серверов: {result.get('servers_count', 1)}",
            keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]]),
        )

        await update.message.reply_text(
            f"✅ Ваш заказ успешно выполнен!\n\n"
            f"Опубликуйте пожалуйста отзыв в группе https://t.me/DubikReviews\n"
            f"Спасибо за покупку!❤️"
        )
    else:
        await update.message.reply_text(f"❌ Ошибка активации подписки:\n{result.get('error', 'Неизвестная ошибка')}")


# ============================================================
#                    ПОКУПКА С БАЛАНСА
# ============================================================
async def pay_from_balance(query, context):
    user_id = query.from_user.id
    days = context.user_data.get('pending_days')
    price = context.user_data.get('pending_price')

    if not all([days, price]):
        await safe_edit_message(query, "❌ Ошибка: данные потеряны")
        return

    user_data = get_user(user_id)
    balance = user_data.get('balance', 0)

    if balance < price:
        text = f"❌ Недостаточно средств на балансе!\n\n💰 Ваш баланс: {balance}₽\n💳 Нужно: {price}₽"
        keyboard = [[InlineKeyboardButton("💳 Пополнить баланс", callback_data="topup_balance")], [InlineKeyboardButton("🔙 Назад", callback_data="buy")]]
        await send_with_banner(query, "key.png", text, InlineKeyboardMarkup(keyboard), parse_mode=None)
        return

    user_data['balance'] = balance - price
    save_user(user_id, user_data)

    result = create_subscription(None, user_id, days, f"User {user_id}")

    if result['success']:
        if 'subscriptions' not in user_data:
            user_data['subscriptions'] = []
        user_data['subscriptions'].append({
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
            'blocked_date': None
        })
        save_user(user_id, user_data)

        for server in get_servers():
            update_server_used_slots(server['id'])

        add_transaction(user_id, -price, 'subscription', f'Оплата подписки на {days} дней')

        await send_sub_photo(
            context.bot, user_id,
            result['sub_link'], days, result['client_id'],
            extra_text=f"💰 Списано: {price}₽\n🌍 Серверов: {result.get('servers_count', 1)}",
            keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]]),
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ Ваш заказ выполнен!\n\nОпубликуйте отзыв в группе https://t.me/DubikReviews\nСпасибо!❤️"
        )

        try:
            await query.delete_message()
        except Exception:
            pass
    else:
        user_data['balance'] = user_data.get('balance', 0) + price
        save_user(user_id, user_data)
        await safe_edit_message(query, f"❌ Ошибка: {result['error']}")


# ============================================================
#                    FREE / MY SUBS / INFO
# ============================================================
async def free_sub(query, context):
    user_id = query.from_user.id
    user_data = get_user(user_id)

    if user_data.get('got_free', False):
        await safe_edit_message(query, "❌ Вы уже получали бесплатный ключ!", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))
        return

    servers = get_servers()
    if not servers:
        await safe_edit_message(query, "❌ Нет серверов", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))
        return

    has_available = False
    for s in servers:
        if is_slot_available(s['id']):
            has_available = True
            break

    if not has_available:
        await safe_edit_message(query, "❌ На всех серверах закончились места", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))
        return

    result = create_subscription(None, user_id, FREE_PERIOD_DAYS, f"User {user_id} (free)")

    if result['success']:
        user_data = get_user(user_id)
        if 'subscriptions' not in user_data:
            user_data['subscriptions'] = []
        user_data['subscriptions'].append({
            'purchase_date': datetime.now().isoformat(),
            'expiry_date': datetime.fromtimestamp(result['expiry_date'] / 1000).isoformat(),
            'days': FREE_PERIOD_DAYS,
            'sub_link': result['sub_link'],
            'client_id': result['client_id'],
            'client_number': result.get('client_number'),
            'email': result.get('email'),
            'servers': result.get('servers', []),
            'servers_count': result.get('servers_count', 1),
            'is_free': True,
            'source': 'free',
            'created_by': None,
            'created_at': datetime.now().isoformat(),
            'warning_sent': False,
            'totalGB': 0,
            'usedGB': 0,
            'uuid': result.get('uuid'),
            'blocked': False,
            'blocked_reason': None,
            'blocked_date': None
        })
        user_data['got_free'] = True
        save_user(user_id, user_data)

        for server in get_servers():
            update_server_used_slots(server['id'])

        await send_sub_photo(
            context.bot, user_id,
            result['sub_link'], FREE_PERIOD_DAYS, result['client_id'],
            extra_text=f"🎁 Бесплатно (только 1 раз)\n🌍 Серверов: {result.get('servers_count', 1)}",
            keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]]),
        )

        try:
            await query.delete_message()
        except Exception:
            pass
    else:
        await safe_edit_message(query, f"❌ Ошибка: {result['error']}", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]))


async def my_subs(query, context):
    """Мои подписки — ссылки в <code>, тап = копирование."""
    user_id = query.from_user.id
    user_data = clean_expired_subs(user_id)
    subs = user_data.get('subscriptions', [])

    if not subs:
        text = "❌ У вас нет активных подписок"
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]
        if os.path.exists("banner/key1.png"):
            await send_with_banner(query, "key1.png", text, InlineKeyboardMarkup(keyboard), parse_mode=None)
        else:
            await safe_edit_message(query, text, InlineKeyboardMarkup(keyboard))
        return

    text = f"🔑 <b>Ваши активные подписки ({len(subs)} шт.):</b>\n\n"
    for i, s in enumerate(subs, 1):
        expiry = datetime.fromisoformat(s['expiry_date'])
        days_left = (expiry - datetime.now()).days

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

        client_id = s.get('client_id', '???')
        sub_link = s.get('sub_link', 'нет ссылки')
        text += f"{i}. 🔑 ID: {html_escape(client_id)}\n"
        text += f"   📅 До: {expiry.strftime('%d.%m.%Y')} ({days_left} дн.)\n"
        text += f"   📦 {period}\n"
        text += f"   🔗 {html_code(sub_link)}\n\n"

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]

    if len(text) <= (TG_CAPTION_LIMIT - 50) and os.path.exists("banner/key1.png"):
        await send_with_banner(query, "key1.png", text, InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else:
        try:
            await query.delete_message()
        except Exception:
            pass

        if os.path.exists("banner/key1.png"):
            try:
                with open("banner/key1.png", "rb") as f:
                    await query.message.reply_photo(photo=f, caption=f"🔑 Ваши активные подписки ({len(subs)} шт.)")
            except Exception as e:
                logging.error(f"banner error: {e}")

        if len(text) <= 4000:
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        else:
            chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)]
            for idx, chunk in enumerate(chunks):
                kb = keyboard if idx == len(chunks) - 1 else None
                try:
                    await query.message.reply_text(chunk, reply_markup=InlineKeyboardMarkup(kb) if kb else None, parse_mode='HTML')
                except Exception as e:
                    logging.error(f"chunk send error: {e}")
                    await query.message.reply_text(chunk, reply_markup=InlineKeyboardMarkup(kb) if kb else None)


async def instructions(query, context):
    text = (
        "📖 Как подключиться:\n\n"
        "1️⃣ Скачайте Nekobox или v2rayNG\n"
        "2️⃣ Скопируйте ссылку подписки\n"
        "3️⃣ Добавить подписку → Вставить ссылку\n"
        "4️⃣ Обновить и подключиться"
    )
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]
    await send_with_banner(query, "info.png", text, InlineKeyboardMarkup(keyboard), parse_mode=None)


async def about(query, context):
    text = (
        "🔒 Дубик ВПН | Dubik VPN\n\n"
        "🚀 Высокоскоростные серверы\n"
        "Мы используем высокоскоростные серверы в различных локациях для обеспечения стабильного и быстрого соединения.\n\n"
        "🛡 Безопасность данных\n"
        "Для защиты ваших данных мы применяем новейшие протоколы шифрования, которые гарантируют вашу конфиденциальность.\n\n"
        "⚠️ Ваш ключ — ваша безопасность!\n"
        "Не передавайте своё шифрование сторонним лицам, чтобы избежать рисков.\n\n"
        f"📱 Максимум устройств: {MAX_DEVICES}\n\n"
        "📱 Поддержка: @dubikvpn_support\n"
        "📢 Канал: @DubikVPN\n"
        "🌐 Наш сайт: https://www.heompvpn.pro\n\n"
        "🔵 Публичная оферта: https://telegra.ph/DubikVPN-09-03-2"
    )
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]
    await send_with_banner(query, "service.png", text, InlineKeyboardMarkup(keyboard), parse_mode=None)


# ============================================================
#                    АДМИН-ПАНЕЛЬ
# ============================================================
async def admin_panel(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return

    servers = get_servers()
    keyboard = [
        [InlineKeyboardButton("🔑 Выдать подписку", callback_data="admin_issue")],
        [InlineKeyboardButton("💰 Выдать баланс", callback_data="admin_give_balance")],
        [InlineKeyboardButton("💳 Редактировать способы оплаты", callback_data="admin_edit_payments")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("💾 Бекап", callback_data="admin_backup")],
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

    await send_with_banner(query, "cabinet.png", "🛡️ Админ панель", InlineKeyboardMarkup(keyboard), parse_mode=None)


async def admin_issue(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    context.user_data['admin_action'] = 'issue_days'
    await safe_edit_message(query, "📅 Введите количество дней:")


async def admin_give_balance(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    context.user_data['admin_action'] = 'give_balance_user'
    await safe_edit_message(query, "💰 Введите Telegram ID:")


async def admin_edit_payments(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
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
    await safe_edit_message(query, text, InlineKeyboardMarkup(keyboard))


async def admin_add_payment(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    context.user_data['admin_action'] = 'add_payment_key'
    await safe_edit_message(query, "Введите ключ для нового способа оплаты (например: card, sbp, usdt):")


async def admin_edit_payment(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    payment_methods = get_payment_methods()
    keyboard = []
    for key in payment_methods.keys():
        keyboard.append([InlineKeyboardButton(f"✏️ {key}", callback_data=f"edit_payment_{key}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_edit_payments")])
    await safe_edit_message(query, "Выберите способ оплаты для редактирования:", InlineKeyboardMarkup(keyboard))


async def admin_edit_payment_details(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    payment_key = query.data.split("_")[2]
    context.user_data['edit_payment_key'] = payment_key
    context.user_data['admin_action'] = 'edit_payment_name'
    await safe_edit_message(query, f"✏️ Редактирование: {html_escape(payment_key)}\n\nВведите новое название:", parse_mode='HTML')


async def admin_delete_payment(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    payment_methods = get_payment_methods()
    keyboard = []
    for key in payment_methods.keys():
        keyboard.append([InlineKeyboardButton(f"🗑️ {key}", callback_data=f"delete_payment_{key}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_edit_payments")])
    await safe_edit_message(query, "Выберите способ оплаты для удаления:", InlineKeyboardMarkup(keyboard))


async def admin_delete_payment_details(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    payment_key = query.data.split("_")[2]
    payment_methods = get_payment_methods()
    if payment_key in payment_methods:
        del payment_methods[payment_key]
        save_payment_methods(payment_methods)
        await safe_edit_message(query, f"✅ Способ оплаты '{payment_key}' удален!")
    else:
        await safe_edit_message(query, "❌ Способ оплаты не найден")


async def admin_broadcast(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    context.user_data['admin_action'] = 'broadcast_message'
    await safe_edit_message(query, "📢 Введите сообщение для рассылки:")


async def admin_backup(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    await safe_edit_message(query, "⏳ Создание бекапа...")
    try:
        backup_file, timestamp = create_backup()
        with open(backup_file, 'rb') as f:
            await context.bot.send_document(chat_id=query.from_user.id, document=f, caption=f"✅ Бекап создан!\n📅 Дата: {timestamp}")
        await query.message.reply_text("✅ Бекап создан!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]]))
    except Exception as e:
        await query.message.reply_text(f"❌ Ошибка: {str(e)}")


async def admin_search(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    context.user_data['admin_action'] = 'search_user'
    await safe_edit_message(query, "🔍 Поиск пользователя\n\nВведите Telegram ID или ID ключа:")


async def admin_add_server(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    context.user_data['admin_action'] = 'add_server_url'
    await safe_edit_message(query, "➕ Введите URL сервера:")


async def admin_grant(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    context.user_data['admin_action'] = 'grant_admin'
    await safe_edit_message(query, "👑 Введите Telegram ID:")


async def admin_del_server(query, context):
    if not is_admin(query.from_user.id):
        await safe_edit_message(query, "⛔ Нет доступа")
        return
    server_id = int(query.data.split("_")[3])
    delete_server(server_id)
    await query.message.reply_text("✅ Сервер удален!")
    await query.message.delete()
    await admin_panel(query, context)


# ============================================================
#                    BUTTON HANDLER
# ============================================================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "back_to_menu":
        await check_subscription_and_show_menu(query, context, user_id, is_callback=True)
        return
    elif data == "verify_subscription":
        await verify_subscription(update, context)
        return
    elif data == "go_to_bot":
        await go_to_bot(update, context)
        return
    elif data == "buy":
        await show_durations(query, context)
        return
    elif data.startswith("duration_"):
        await process_duration(query, context)
        return
    elif data == "pay_from_balance":
        await pay_from_balance(query, context)
        return
    elif data == "topup_balance":
        await topup_balance(query, context)
        return
    elif data == "topup_sbp_cards":
        await topup_sbp_cards(query, context)
        return
    elif data.startswith("lava_buy_"):
        await process_lava_buy(query, context)
        return
    elif data.startswith("lava_check_"):
        await check_lava_payment(query, context)
        return
    elif data == "topup_stars":
        await topup_stars(query, context)
        return
    elif data.startswith("stars_topup_"):
        await process_stars_topup(query, context)
        return
    elif data.startswith("send_stars_invoice_"):
        await send_stars_invoice(update, context)
        return
    elif data.startswith("pay_stars_"):
        await pay_stars_callback(update, context)
        return
    elif data == "my_subs":
        await my_subs(query, context)
        return
    elif data == "free_sub":
        await free_sub(query, context)
        return
    elif data == "instructions":
        await instructions(query, context)
        return
    elif data == "about":
        await about(query, context)
        return
    elif data == "admin_panel":
        await admin_panel(query, context)
        return
    elif data == "admin_issue":
        await admin_issue(query, context)
        return
    elif data == "admin_give_balance":
        await admin_give_balance(query, context)
        return
    elif data == "admin_edit_payments":
        await admin_edit_payments(query, context)
        return
    elif data == "admin_add_payment":
        await admin_add_payment(query, context)
        return
    elif data == "admin_edit_payment":
        await admin_edit_payment(query, context)
        return
    elif data.startswith("edit_payment_"):
        await admin_edit_payment_details(query, context)
        return
    elif data == "admin_delete_payment":
        await admin_delete_payment(query, context)
        return
    elif data.startswith("delete_payment_"):
        await admin_delete_payment_details(query, context)
        return
    elif data == "admin_broadcast":
        await admin_broadcast(query, context)
        return
    elif data == "admin_backup":
        await admin_backup(query, context)
        return
    elif data == "admin_search":
        await admin_search(query, context)
        return
    elif data == "admin_add_server":
        await admin_add_server(query, context)
        return
    elif data == "admin_grant":
        await admin_grant(query, context)
        return
    elif data.startswith("admin_del_server_"):
        await admin_del_server(query, context)
        return
    elif data.startswith("approve_topup_"):
        if not is_admin(user_id):
            await safe_edit_message(query, "⛔ Нет доступа")
            return
        target_user_id = int(data.split("_")[2])
        pending = get_pending_topup(target_user_id)
        if not pending:
            await safe_edit_message(query, "❌ Заявка не найдена")
            return
        amount = pending.get('amount', 0)
        user_data = get_user(target_user_id)
        user_data['balance'] = user_data.get('balance', 0) + amount
        save_user(target_user_id, user_data)
        remove_pending_topup(target_user_id)
        add_transaction(target_user_id, amount, 'topup', f'Пополнение баланса на {amount}₽')
        await context.bot.send_message(target_user_id, f"✅ БАЛАНС ПОПОЛНЕН!\n\n💰 Сумма: {amount}₽\n💰 Новый баланс: {user_data['balance']}₽")
        await safe_edit_message(query, f"✅ Баланс пополнен на {amount}₽")
        return
    elif data.startswith("reject_topup_"):
        if not is_admin(user_id):
            await safe_edit_message(query, "⛔ Нет доступа")
            return
        target_user_id = int(data.split("_")[2])
        remove_pending_topup(target_user_id)
        await context.bot.send_message(target_user_id, "❌ Заявка на пополнение отклонена")
        await safe_edit_message(query, "❌ Заявка на пополнение отклонена")
        return
    elif data.startswith("approve_"):
        if not is_admin(user_id):
            await safe_edit_message(query, "⛔ Нет доступа")
            return
        target_user_id = int(data.split("_")[1])
        pending = get_pending_order(target_user_id)
        if not pending:
            await safe_edit_message(query, "❌ Заказ не найден")
            return
        result = create_subscription(None, target_user_id, pending['days'], f"User {target_user_id}")
        if result['success']:
            user_data = get_user(target_user_id)
            if 'subscriptions' not in user_data:
                user_data['subscriptions'] = []
            user_data['subscriptions'].append({
                'purchase_date': datetime.now().isoformat(),
                'expiry_date': datetime.fromtimestamp(result['expiry_date'] / 1000).isoformat(),
                'days': pending['days'],
                'sub_link': result['sub_link'],
                'client_id': result['client_id'],
                'client_number': result.get('client_number'),
                'email': result.get('email'),
                'servers': result.get('servers', []),
                'servers_count': result.get('servers_count', 1),
                'warning_sent': False,
                'is_free': False,
                'source': 'purchase',
                'created_by': user_id,
                'created_at': datetime.now().isoformat(),
                'totalGB': 0,
                'usedGB': 0,
                'uuid': result.get('uuid'),
                'blocked': False,
                'blocked_reason': None,
                'blocked_date': None
            })
            save_user(target_user_id, user_data)
            remove_pending(target_user_id)
            for server in get_servers():
                update_server_used_slots(server['id'])
            add_transaction(target_user_id, -pending['price'], 'subscription', f'Оплата подписки на {pending["days"]} дней')

            await send_sub_photo(
                context.bot, target_user_id,
                result['sub_link'], pending['days'], result['client_id'],
                extra_text=f"🌍 Серверов: {result.get('servers_count', 1)}",
                keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]]),
            )

            await context.bot.send_message(chat_id=target_user_id, text=f"✅ Ваш заказ выполнен!\n\nОпубликуйте отзыв в группе https://t.me/DubikReviews\nСпасибо!❤️")
            await safe_edit_message(query, f"✅ Подписка выдана пользователю {target_user_id}")
        else:
            await safe_edit_message(query, f"❌ Ошибка: {result['error']}")
        return
    elif data.startswith("reject_"):
        if not is_admin(user_id):
            await safe_edit_message(query, "⛔ Нет доступа")
            return
        target_user_id = int(data.split("_")[1])
        remove_pending(target_user_id)
        await context.bot.send_message(target_user_id, "❌ Платеж не подтвержден")
        await safe_edit_message(query, "❌ Платеж отклонен")
        return


# ============================================================
#                    TEXT HANDLER
# ============================================================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    log_user_activity(user_id, f'text_input: {text[:50]}')

    if text == "👤 Личный кабинет":
        await check_subscription_and_show_menu(update, context, user_id, is_callback=False)
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
            name = chat.first_name or "Без имени"
            username = f"@{chat.username}" if chat.username else "нет username"
        except Exception:
            name = "Неизвестно"
            username = "нет"
        subs = udata.get('subscriptions', [])
        subs_info = ""
        for s in subs:
            expiry = datetime.fromisoformat(s['expiry_date']).strftime('%d.%m.%Y')
            days_left = (datetime.fromisoformat(s['expiry_date']) - datetime.now()).days
            client_id = s.get('client_id', '???')
            subs_info += f"• ID {client_id} до {expiry} (осталось {days_left} дн.)\n"
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

    if not is_admin(user_id):
        return

    if action == 'add_payment_key':
        context.user_data['payment_key'] = text.strip()
        context.user_data['admin_action'] = 'add_payment_name'
        await update.message.reply_text("Введите название способа оплаты:")
        return
    elif action == 'add_payment_name':
        context.user_data['payment_name'] = text.strip()
        context.user_data['admin_action'] = 'add_payment_details'
        await update.message.reply_text("Введите реквизиты для оплаты:")
        return
    elif action == 'add_payment_details':
        key = context.user_data.get('payment_key')
        name = context.user_data.get('payment_name')
        details = text.strip()
        payment_methods = get_payment_methods()
        payment_methods[key] = {'name': name, 'details': details}
        save_payment_methods(payment_methods)
        await update.message.reply_text(f"✅ Способ оплаты '{key}' добавлен!")
        context.user_data['admin_action'] = None
        return
    elif action == 'edit_payment_name':
        key = context.user_data.get('edit_payment_key')
        if not key:
            await update.message.reply_text("❌ Ошибка: ключ не найден")
            context.user_data['admin_action'] = None
            return
        context.user_data['payment_name'] = text.strip()
        context.user_data['admin_action'] = 'edit_payment_details'
        await update.message.reply_text(f"Введите новые реквизиты для {key}:")
        return
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
        return
    elif action == 'broadcast_message':
        users = get_all_users()
        success = 0
        fail = 0
        for uid in users:
            try:
                await context.bot.send_message(uid, f"📢 РАССЫЛКА\n\n{text}")
                success += 1
            except Exception:
                fail += 1
        await update.message.reply_text(f"✅ Рассылка завершена!\n📤 Отправлено: {success}\n❌ Ошибок: {fail}")
        context.user_data['admin_action'] = None
        return
    elif action == 'give_balance_user':
        try:
            target_id = int(text.strip())
            context.user_data['give_balance_target'] = target_id
            context.user_data['admin_action'] = 'give_balance_amount'
            await update.message.reply_text("💰 Введите сумму:")
        except Exception:
            await update.message.reply_text("❌ Введите корректный ID")
        return
    elif action == 'give_balance_amount':
        try:
            amount = int(text.strip())
            target_id = context.user_data.get('give_balance_target')
            if not target_id:
                await update.message.reply_text("❌ Пользователь не найден")
                context.user_data['admin_action'] = None
                return
            user_data = get_user(target_id)
            user_data['balance'] = user_data.get('balance', 0) + amount
            save_user(target_id, user_data)
            add_transaction(target_id, amount, 'topup', f'Администратор начислил {amount}₽')
            await update.message.reply_text(f"✅ Баланс увеличен на {amount}₽")
            context.user_data['admin_action'] = None
        except Exception:
            await update.message.reply_text("❌ Введите корректную сумму")
        return
    elif action == 'issue_days':
        try:
            days = int(text.strip())
            servers = get_servers()
            if not servers:
                await update.message.reply_text("❌ Нет серверов")
                context.user_data['admin_action'] = None
                return
            result = create_subscription(None, user_id, days, f"Admin issued")
            if result['success']:
                user_data = get_user(user_id)
                if 'subscriptions' not in user_data:
                    user_data['subscriptions'] = []
                user_data['subscriptions'].append({
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
                    'source': 'admin',
                    'created_by': user_id,
                    'created_at': datetime.now().isoformat(),
                    'totalGB': 0,
                    'usedGB': 0,
                    'uuid': result.get('uuid'),
                    'blocked': False,
                    'blocked_reason': None,
                    'blocked_date': None
                })
                save_user(user_id, user_data)
                await update.message.reply_text(
                    f"✅ Подписка на {days} дней:\n\n📛 ID: {html_escape(result['client_id'])}\n\n🔗 {html_code(result['sub_link'])}",
                    parse_mode='HTML'
                )
                for server in get_servers():
                    update_server_used_slots(server['id'])
            else:
                await update.message.reply_text(f"❌ Ошибка: {result['error']}")
        except Exception:
            await update.message.reply_text("❌ Введите число")
        context.user_data['admin_action'] = None
        return
    elif action == 'add_server_url':
        user_input = text.strip()
        full_url = extract_full_url_with_port(user_input)
        domain = extract_domain_from_url(user_input)
        if not domain:
            await update.message.reply_text("❌ Неверный URL")
            context.user_data['admin_action'] = None
            return
        context.user_data['new_server_url'] = full_url
        context.user_data['admin_action'] = 'add_server_name'
        await update.message.reply_text(f"✅ API URL: {full_url}\n\nВведите имя сервера:")
        return
    elif action == 'add_server_name':
        context.user_data['new_server_name'] = text.strip()
        context.user_data['admin_action'] = 'add_server_token'
        await update.message.reply_text("🔑 Введите API Token:")
        return
    elif action == 'add_server_token':
        context.user_data['new_server_token'] = text.strip()
        context.user_data['admin_action'] = 'add_server_inbound'
        await update.message.reply_text("📋 Введите Inbound ID (число):")
        return
    elif action == 'add_server_inbound':
        try:
            context.user_data['new_inbound_id'] = int(text.strip())
            context.user_data['admin_action'] = 'add_server_limit'
            await update.message.reply_text("📊 Введите максимум слотов (0 - безлимит):")
        except Exception:
            await update.message.reply_text("❌ Введите число")
        return
    elif action == 'add_server_limit':
        try:
            max_slots = int(text.strip())
            server = {
                'name': context.user_data.get('new_server_name'),
                'url': context.user_data.get('new_server_url'),
                'api_token': context.user_data.get('new_server_token'),
                'inbound_ids': [context.user_data.get('new_inbound_id')],
                'max_slots': max_slots if max_slots > 0 else None,
                'used_slots': 0
            }
            result = test_server_connection(server)
            if result['success']:
                add_server(server)
                await update.message.reply_text(f"✅ Сервер '{server['name']}' добавлен!")
            else:
                await update.message.reply_text(f"❌ Ошибка подключения: {result['msg']}")
        except Exception:
            await update.message.reply_text("❌ Введите число")
        context.user_data['admin_action'] = None
        for key in ['new_server_name', 'new_server_url', 'new_server_token', 'new_inbound_id']:
            context.user_data.pop(key, None)
        return
    elif action == 'grant_admin':
        try:
            target_id = int(text.strip())
            add_admin(target_id)
            await update.message.reply_text(f"✅ Админ {target_id} добавлен")
        except Exception:
            await update.message.reply_text("❌ Ошибка")
        context.user_data['admin_action'] = None
        return


# ============================================================
#                    MAIN
# ============================================================
def main():
    if PROXY_URL:
        request = HTTPXRequest(proxy_url=PROXY_URL)
    else:
        request = HTTPXRequest()

    app = Application.builder().token(BOT_TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_query_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    try:
        if app.job_queue:
            app.job_queue.run_repeating(check_expiring_subs, interval=3600, first=10)
            app.job_queue.run_repeating(auto_renew_check, interval=3600, first=30)
            app.job_queue.run_repeating(check_renewal_reminders, interval=3600, first=60)
            app.job_queue.run_repeating(auto_backup, interval=172800, first=300)
    except Exception:
        pass

    print("🤖 Бот DubikVPN запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
