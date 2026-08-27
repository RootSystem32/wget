# Telegram Bot Token
BOT_TOKEN = ""

# Главный администратор
MAIN_ADMIN_ID = 

# Файлы для хранения данных
USERS_FILE = "users.json"
SERVERS_FILE = "servers.json"
ADMINS_FILE = "admins.json"
PENDING_FILE = "pending.json"
PAYMENT_METHODS_FILE = "payment_methods.json"

# Цены на подписки (безлимит)
PRICES = {
    30: 150,
    90: 350,
    180: 700
}

# Бесплатный период (дней)
FREE_PERIOD_DAYS = 3

# URL панели (используется как fallback)
PANEL_URL = "https://nider.heompvpn.pro"

# Путь для подписок
SUBSCRIPTION_PATH = "connect"

# ========== ПРОВЕРКА ПОДПИСКИ НА КАНАЛ ==========
# ID канала @DubikVPN (получить через @userinfobot)
CHANNEL_ID = -1004  # ЗАМЕНИТЬ НА РЕАЛЬНЫЙ ID
CHANNEL_LINK = "https://t.me/"

# ========== НАСТРОЙКИ ПРОКСИ ==========
# Раскомментируй если нужен прокси
# PROXY_URL = "socks5://user:pass@ip:port"
PROXY_URL = None
