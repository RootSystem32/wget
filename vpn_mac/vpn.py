import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import subprocess
import threading
import time
import os
from datetime import datetime

class SSHVPNClient:
    def __init__(self, root):
        self.root = root
        self.root.title("🛡️ SSH VPN")
        self.root.geometry("700x600")
        self.root.resizable(False, False)
        self.root.configure(bg='#1a1a2e')
        
        self.ssh_user = "root"
        self.ssh_host = "77.239.103.158"
        self.ssh_password = "5V2ej5rKzoh4"
        self.proxy_port = 9443
        
        self.is_connected = False
        self.ssh_process = None
        self.connection_time = 0
        self.log_lines = []
        
        self.setup_ui()
        
    def setup_ui(self):
        main_frame = tk.Frame(self.root, bg='#1a1a2e')
        main_frame.pack(fill='both', expand=True, padx=20, pady=15)
        
        title_frame = tk.Frame(main_frame, bg='#1a1a2e')
        title_frame.pack(fill='x', pady=(0, 15))
        
        tk.Label(title_frame, text="🛡️ SSH VPN", 
                font=("Arial", 22, "bold"), fg='#4fc3f7', bg='#1a1a2e').pack(side='left')
        
        self.status_indicator = tk.Label(title_frame, text="●", font=("Arial", 24), 
                                   fg='#ef5350', bg='#1a1a2e')
        self.status_indicator.pack(side='right', padx=(0, 5))
        
        self.status_text = tk.Label(title_frame, text="DISCONNECTED", 
                                   font=("Arial", 10, "bold"), fg='#ef5350', bg='#1a1a2e')
        self.status_text.pack(side='right')
        
        info_frame = tk.Frame(main_frame, bg='#16213e', relief='flat', bd=2)
        info_frame.pack(fill='x', pady=(0, 15))
        
        tk.Label(info_frame, text=f"📌 Server: {self.ssh_host}:22  |  Proxy: SOCKS5 :{self.proxy_port}", 
                font=("Arial", 10), fg='#b0bec5', bg='#16213e').pack(pady=10)
        
        control_frame = tk.Frame(main_frame, bg='#16213e', relief='flat', bd=2)
        control_frame.pack(fill='x', pady=(0, 15))
        
        control_btn_frame = tk.Frame(control_frame, bg='#16213e')
        control_btn_frame.pack(pady=15)
        
        self.connect_btn = tk.Button(control_btn_frame, text="🔌 CONNECT", 
                                    command=self.toggle_connection,
                                    bg='#1a237e', fg='white', font=("Arial", 14, "bold"),
                                    relief='flat', cursor='hand2', 
                                    width=25, height=2)
        self.connect_btn.pack()
        
        stats_frame = tk.Frame(control_frame, bg='#16213e')
        stats_frame.pack(fill='x', padx=20, pady=(0, 15))
        
        self.time_label = tk.Label(stats_frame, text="⏱️ Time: 00:00:00", 
                                  font=("Arial", 11), fg='#b0bec5', bg='#16213e')
        self.time_label.pack()
        
        log_frame = tk.Frame(main_frame, bg='#16213e', relief='flat', bd=2)
        log_frame.pack(fill='both', expand=True)
        
        log_header = tk.Frame(log_frame, bg='#16213e')
        log_header.pack(fill='x', padx=15, pady=(10, 5))
        
        tk.Label(log_header, text="📋 LOGS", font=("Arial", 11, "bold"), 
                fg='#e0e0e0', bg='#16213e').pack(side='left')
        
        copy_btn = tk.Button(log_header, text="📋 Copy", command=self.copy_logs,
                            bg='#424242', fg='white', font=("Arial", 8),
                            relief='flat', cursor='hand2', padx=10)
        copy_btn.pack(side='right')
        
        log_container = tk.Frame(log_frame, bg='#0d1117')
        log_container.pack(fill='both', expand=True, padx=15, pady=(0, 15))
        
        self.log_text = scrolledtext.ScrolledText(log_container, height=12, 
                                                bg='#0d1117', fg='#e0e0e0',
                                                font=("Consolas", 9),
                                                insertbackground='#4fc3f7',
                                                relief='flat', bd=2)
        self.log_text.pack(fill='both', expand=True)
        self.log_text.config(state='disabled')
    
    def log_message(self, message, level="INFO"):
        self.log_text.config(state='normal')
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        log_line = f"[{timestamp}] {message}"
        self.log_lines.append(log_line)
        if len(self.log_lines) > 1000:
            self.log_lines = self.log_lines[-1000:]
        
        colors = {
            "ERROR": "#ef5350",
            "SUCCESS": "#4caf50",
            "INFO": "#4fc3f7",
            "SSH": "#ff9800"
        }
        
        color = colors.get(level, "#e0e0e0")
        self.log_text.insert(tk.END, f"[{timestamp}] ", "#4fc3f7")
        self.log_text.insert(tk.END, f"{message}\n", color)
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')
    
    def copy_logs(self):
        if not self.log_lines:
            return
        log_text = "\n".join(self.log_lines)
        self.root.clipboard_clear()
        self.root.clipboard_append(log_text)
        self.log_message("📋 Logs copied", "SUCCESS")
    
    def toggle_connection(self):
        if not self.is_connected:
            self.connect_vpn()
        else:
            self.disconnect_vpn()
    
    def connect_vpn(self):
        self.log_message("🚀 Starting...", "INFO")
        self.connect_btn.config(state="disabled", text="⏳ CONNECTING...")
        threading.Thread(target=self._connect_thread, daemon=True).start()
    
    def _connect_thread(self):
        try:
            # Убиваем старые процессы
            subprocess.run("pkill -f 'ssh -N -D 9443'", shell=True, stderr=subprocess.DEVNULL)
            time.sleep(1)
            
            # Используем AppleScript для открытия терминала и ввода пароля
            # Изменено: delay 4 вместо delay 2 (увеличение времени отправки пароля на 2 секунды)
            script = f'''
            tell application "Terminal"
                activate
                do script "ssh -N -D {self.proxy_port} -o StrictHostKeyChecking=no {self.ssh_user}@{self.ssh_host}"
                delay 4
                do script "{self.ssh_password}" in front window
            end tell
            '''
            
            self.log_message("📌 Opening Terminal with SSH...", "SSH")
            subprocess.run(["osascript", "-e", script], capture_output=True)
            
            # Ждем, пока порт откроется
            self.log_message("⏳ Waiting for SSH tunnel...", "INFO")
            port_open = False
            for i in range(15):
                time.sleep(1)
                result = subprocess.run(f"lsof -i :{self.proxy_port} | grep LISTEN", shell=True, capture_output=True)
                if result.stdout:
                    port_open = True
                    self.log_message(f"✅ Port {self.proxy_port} is listening", "SUCCESS")
                    break
                self.log_message(f"⏳ Waiting... ({i+1}/15)", "INFO")
            
            if port_open:
                self.is_connected = True
                self.connection_time = time.time()
                
                # Настраиваем прокси
                self.log_message("📌 Configuring system proxy...", "SSH")
                subprocess.run(f"networksetup -setsocksfirewallproxy wi-fi 127.0.0.1 {self.proxy_port}", shell=True)
                subprocess.run("networksetup -setsocksfirewallproxystate wi-fi on", shell=True)
                
                self.root.after(0, lambda: self.connect_btn.config(text="🔌 DISCONNECT", state="normal"))
                self.root.after(0, lambda: self.status_text.config(text="CONNECTED", fg='#4caf50'))
                self.root.after(0, lambda: self.status_indicator.config(fg='#4caf50'))
                self.root.after(0, lambda: self.log_message("✅ VPN connected!", "SUCCESS"))
                self.root.after(0, lambda: self.update_time())
                
                # Мониторинг
                threading.Thread(target=self.monitor_ssh, daemon=True).start()
            else:
                self.log_message("❌ SSH tunnel failed to start", "ERROR")
                self.root.after(0, lambda: self.connect_btn.config(state="normal", text="🔌 CONNECT"))
                
        except Exception as e:
            self.log_message(f"❌ Error: {str(e)}", "ERROR")
            self.root.after(0, lambda: self.connect_btn.config(state="normal", text="🔌 CONNECT"))
    
    def monitor_ssh(self):
        while self.is_connected:
            time.sleep(3)
            result = subprocess.run(f"lsof -i :{self.proxy_port} | grep LISTEN", shell=True, capture_output=True)
            if not result.stdout:
                self.root.after(0, self.disconnect_vpn)
                self.root.after(0, lambda: self.log_message("⚠️ Tunnel closed", "WARNING"))
                break
    
    def update_time(self):
        if not self.is_connected:
            return
        elapsed = int(time.time() - self.connection_time)
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60
        self.time_label.config(text=f"⏱️ Time: {h:02d}:{m:02d}:{s:02d}")
        self.root.after(1000, self.update_time)
    
    def disconnect_vpn(self):
        self.log_message("🔄 Disconnecting...", "INFO")
        
        # Отключаем прокси
        subprocess.run("networksetup -setsocksfirewallproxystate wi-fi off", shell=True)
        
        # Закрываем терминал с SSH
        subprocess.run("osascript -e 'tell application \"Terminal\" to close every window whose name contains \"ssh\"'", shell=True)
        subprocess.run("pkill -f 'ssh -N -D 9443'", shell=True, stderr=subprocess.DEVNULL)
        
        self.is_connected = False
        self.status_text.config(text="DISCONNECTED", fg='#ef5350')
        self.status_indicator.config(fg='#ef5350')
        self.connect_btn.config(text="🔌 CONNECT", state="normal")
        self.time_label.config(text="⏱️ Time: 00:00:00")
        self.log_message("✅ Disconnected", "SUCCESS")

def main():
    root = tk.Tk()
    app = SSHVPNClient(root)
    root.mainloop()

if __name__ == "__main__":
    main()
