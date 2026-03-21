#!/bin/bash
# ============================================
# MedInventory - Setup Iniziale (Linux)
# by Studio Bergamaschi
# ============================================

APP_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "============================================"
echo " MedInventory - Setup Iniziale"
echo " by Studio Bergamaschi"
echo "============================================"
echo ""

# ---------------------------------------------------------------------------
# [0] Controlla Python 3.10+
# ---------------------------------------------------------------------------
if ! command -v python3 &>/dev/null; then
    echo "ERRORE: Python 3 non trovato!"
    echo ""
    echo "Installare Python 3.10+ con:"
    echo "  sudo apt install python3 python3-venv python3-pip   # Debian/Ubuntu"
    echo "  sudo dnf install python3                            # Fedora/RHEL"
    echo ""
    exit 1
fi

PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
PY_VER="$PY_MAJOR.$PY_MINOR"

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "ERRORE: Python 3.10+ richiesto (trovato $PY_VER)."
    exit 1
fi

echo "Python trovato: $PY_VER"

# ---------------------------------------------------------------------------
# [1/4] Virtual environment
# ---------------------------------------------------------------------------
echo ""
echo "[1/4] Creazione virtual environment..."

if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
    if [ $? -ne 0 ]; then
        echo ""
        echo "ERRORE: impossibile creare il virtual environment."
        echo "Verificare che python3-venv sia installato:"
        echo "  sudo apt install python3-venv"
        exit 1
    fi
    echo "      Virtual environment creato."
else
    echo "      Virtual environment già esistente."
fi

# ---------------------------------------------------------------------------
# [2/4] Dipendenze
# ---------------------------------------------------------------------------
echo ""
echo "[2/4] Installazione dipendenze..."

VENV_PYTHON="$APP_DIR/venv/bin/python"

# Se pip non è presente nel venv (Python senza ensurepip), lo installiamo
if ! "$VENV_PYTHON" -m pip --version &>/dev/null; then
    echo "      pip non trovato nel venv, bootstrap in corso..."
    # Prova ensurepip (Python standard)
    "$VENV_PYTHON" -m ensurepip --upgrade 2>/dev/null
    # Se ancora non funziona, prova con pip di sistema
    if ! "$VENV_PYTHON" -m pip --version &>/dev/null; then
        python3 -m pip install --target "$APP_DIR/venv/lib/python$PY_VER/site-packages" pip 2>/dev/null || true
    fi
    if ! "$VENV_PYTHON" -m pip --version &>/dev/null; then
        echo ""
        echo "ERRORE: impossibile trovare o installare pip."
        echo "Installare i pacchetti necessari:"
        echo "  sudo apt install python3-pip python3-venv   # Debian/Ubuntu"
        echo "  sudo dnf install python3-pip                # Fedora/RHEL"
        exit 1
    fi
fi

"$VENV_PYTHON" -m pip install --upgrade pip --quiet
"$VENV_PYTHON" -m pip install -r "$APP_DIR/requirements.txt"

if [ $? -ne 0 ]; then
    echo ""
    echo "ERRORE: installazione dipendenze fallita."
    echo "Controllare requirements.txt e la connessione internet."
    exit 1
fi

# ---------------------------------------------------------------------------
# [3/4] Database
# ---------------------------------------------------------------------------
echo ""
echo "[3/4] Inizializzazione database..."

"$APP_DIR/venv/bin/python" "$APP_DIR/seed.py"

if [ $? -ne 0 ]; then
    echo ""
    echo "ERRORE: inizializzazione database fallita."
    exit 1
fi

# ---------------------------------------------------------------------------
# [4/4] Configurazione
# ---------------------------------------------------------------------------
echo ""
echo "[4/4] Verifica configurazione..."

if [ ! -f "$APP_DIR/config.json" ]; then
    echo "      Il file config.json verrà creato al primo avvio."
    echo "      Modificare config.example.json per personalizzare le impostazioni."
else
    echo "      config.json trovato."
fi

# Rendi eseguibile lo script di installazione del servizio
chmod +x "$APP_DIR/install_service.sh" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Riepilogo
# ---------------------------------------------------------------------------
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo "============================================"
echo " Setup completato!"
echo "============================================"
echo ""
echo "Per avviare in modalità sviluppo:"
echo "  venv/bin/python app.py"
echo ""
echo "Per avviare in produzione:"
echo "  venv/bin/python run_production.py"
echo ""
echo "Per installare come servizio systemd:"
echo "  sudo bash install_service.sh"
echo ""
echo "Credenziali admin predefinite:"
echo "  Email:    admin@medinventory.local"
echo "  Password: admin123"
echo "  (verrà richiesto di cambiarla al primo accesso)"
echo ""
echo "L'applicazione sarà accessibile su:"
echo "  http://localhost:5000"
if [ -n "$LOCAL_IP" ]; then
    echo "  http://$LOCAL_IP:5000"
fi
echo ""
