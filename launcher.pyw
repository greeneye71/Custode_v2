"""
MedInventory - Windows Launcher con System Tray (TNA)
Estensione .pyw: nessuna finestra console su Windows.

Uso: doppio-click su launcher.pyw (o associa a pythonw.exe)
Avvio automatico Windows: shortcut in %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
"""

import os
import sys
import json
import socket
import webbrowser
import threading
import subprocess
import time
import urllib.request

# --- Cerca la directory dell'app ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')


def _load_config():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


class MedInventoryLauncher:
    def __init__(self):
        config = _load_config()
        self._port = config.get('port', 5000)
        self._app_name = config.get('app_name', 'MedInventory')
        self._server_url = f"http://localhost:{self._port}"
        self._health_url = f"{self._server_url}/api/health"
        self._server_process = None
        self._icon = None
        self._running = False

    # ------------------------------------------------------------------ #
    # Server lifecycle                                                     #
    # ------------------------------------------------------------------ #

    def _is_server_running(self):
        try:
            with urllib.request.urlopen(self._health_url, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _is_port_in_use(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('localhost', self._port)) == 0

    def _start_server(self):
        if self._is_port_in_use():
            # Server già attivo (es. avviato manualmente) — adotta senza avviare processo
            return

        run_production = os.path.join(BASE_DIR, 'run_production.py')
        if not os.path.exists(run_production):
            run_production = os.path.join(BASE_DIR, 'app.py')

        CREATE_NO_WINDOW = 0x08000000
        self._server_process = subprocess.Popen(
            [sys.executable, run_production],
            cwd=BASE_DIR,
            creationflags=CREATE_NO_WINDOW
        )

        # Attendi health check (max 15 secondi)
        for _ in range(15):
            time.sleep(1)
            if self._is_server_running():
                break

    def _stop_server(self):
        if not self._server_process:
            return
        try:
            self._server_process.terminate()
            self._server_process.wait(timeout=5)
        except Exception:
            try:
                self._server_process.kill()
            except Exception:
                pass
        self._server_process = None

    # ------------------------------------------------------------------ #
    # Icona tray                                                           #
    # ------------------------------------------------------------------ #

    def _generate_icon(self, running=True):
        """Genera icona 64x64 con Pillow: verde se running, rossa altrimenti."""
        try:
            from PIL import Image, ImageDraw
            img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            color = '#22c55e' if running else '#ef4444'
            draw.ellipse([4, 4, 60, 60], fill=color)
            # Croce bianca al centro
            draw.rectangle([30, 14, 34, 50], fill='white')
            draw.rectangle([14, 30, 50, 34], fill='white')
            return img
        except ImportError:
            return None

    def _get_logs_dir(self):
        logs = os.path.join(BASE_DIR, 'logs')
        if not os.path.exists(logs):
            os.makedirs(logs, exist_ok=True)
        return logs

    # ------------------------------------------------------------------ #
    # Monitor thread                                                       #
    # ------------------------------------------------------------------ #

    def _monitor_loop(self):
        while self._running:
            time.sleep(10)
            if not self._icon:
                continue
            try:
                is_up = self._is_server_running()
                self._icon.icon = self._generate_icon(is_up)
                status = 'Online' if is_up else 'Non raggiungibile'
                self._icon.title = f"{self._app_name} — {status}"
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Menu actions                                                         #
    # ------------------------------------------------------------------ #

    def _action_apri(self, icon, item):
        webbrowser.open(self._server_url)

    def _action_riavvia(self, icon, item):
        def _do_restart():
            self._stop_server()
            time.sleep(1)
            self._start_server()
        threading.Thread(target=_do_restart, daemon=True).start()

    def _action_log(self, icon, item):
        os.startfile(self._get_logs_dir())

    def _action_esci(self, icon, item):
        self._running = False
        icon.stop()
        self._stop_server()

    # ------------------------------------------------------------------ #
    # Entry point                                                          #
    # ------------------------------------------------------------------ #

    def run(self):
        try:
            import pystray
        except ImportError:
            # Fallback senza tray: avvia solo il server e apri il browser
            self._start_server()
            webbrowser.open(self._server_url)
            # Blocca il processo finché il server gira
            while self._is_server_running():
                time.sleep(5)
            return

        self._running = True

        # Avvia server in thread separato
        server_thread = threading.Thread(target=self._start_server, daemon=True)
        server_thread.start()
        server_thread.join()  # Attendi che il server sia pronto prima di mostrare l'icona

        # Avvia monitor thread
        monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        monitor_thread.start()

        # Crea icona iniziale
        icon_img = self._generate_icon(self._is_server_running())
        is_up = self._is_server_running()

        menu = pystray.Menu(
            pystray.MenuItem(
                f"Apri {self._app_name}",
                self._action_apri,
                default=True
            ),
            pystray.MenuItem("Riavvia server", self._action_riavvia),
            pystray.MenuItem("Visualizza log", self._action_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Esci", self._action_esci),
        )

        status = 'Online' if is_up else 'Avvio...'
        self._icon = pystray.Icon(
            self._app_name,
            icon=icon_img,
            title=f"{self._app_name} — {status}",
            menu=menu
        )
        self._icon.run()


if __name__ == '__main__':
    launcher = MedInventoryLauncher()
    launcher.run()
