#!/bin/bash
# ============================================
# MedInventory - Linux Service Installer
# Usa systemd (equivalente di NSSM su Windows)
# by Studio Bergamaschi
# ============================================

SERVICE_NAME="medinventory"
SERVICE_DISPLAY="MedInventory - Gestione Apparecchi Elettromedicali"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_EXE="$APP_DIR/venv/bin/python"
APP_SCRIPT="$APP_DIR/run_production.py"
LOG_DIR="$APP_DIR/logs"

# ---------------------------------------------------------------------------
# Controlla root
# ---------------------------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
    echo ""
    echo "ERRORE: Eseguire come root o con sudo!"
    echo "  sudo bash $0"
    echo ""
    exit 1
fi

# ---------------------------------------------------------------------------
# Controlla virtual environment
# ---------------------------------------------------------------------------
if [ ! -f "$PYTHON_EXE" ]; then
    echo ""
    echo "ERRORE: Virtual environment non trovato!"
    echo ""
    echo "Eseguire prima il setup:"
    echo "  bash $APP_DIR/setup.sh"
    echo ""
    exit 1
fi

# ---------------------------------------------------------------------------
# Controlla waitress
# ---------------------------------------------------------------------------
"$PYTHON_EXE" -c "import waitress" &>/dev/null
if [ $? -ne 0 ]; then
    echo ""
    echo "ERRORE: Il pacchetto 'waitress' non è installato nel virtual environment!"
    echo ""
    echo "Eseguire:"
    echo "  $APP_DIR/venv/bin/pip install -r $APP_DIR/requirements.txt"
    echo ""
    exit 1
fi

# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------
echo ""
echo "============================================"
echo " MedInventory - Installazione Servizio"
echo "============================================"
echo ""
echo " Cartella applicazione: $APP_DIR"
echo " Python:                $PYTHON_EXE"
echo ""
echo " Cosa vuoi fare?"
echo ""
echo "  1. Installa il servizio"
echo "  2. Rimuovi il servizio"
echo "  3. Avvia il servizio"
echo "  4. Ferma il servizio"
echo "  5. Riavvia il servizio"
echo "  6. Stato del servizio"
echo "  7. Mostra log errori"
echo "  8. Esci"
echo ""
read -rp "Scelta (1-8): " choice

case "$choice" in
    1) action="install" ;;
    2) action="remove"  ;;
    3) action="start"   ;;
    4) action="stop"    ;;
    5) action="restart" ;;
    6) action="status"  ;;
    7) action="showlog" ;;
    8) action="end"     ;;
    *)
        echo "Scelta non valida."
        exit 0
        ;;
esac

# ---------------------------------------------------------------------------
# INSTALL
# ---------------------------------------------------------------------------
if [ "$action" = "install" ]; then
    echo ""

    # Controlla se il servizio è già installato
    if systemctl list-unit-files "${SERVICE_NAME}.service" &>/dev/null \
       && systemctl list-unit-files "${SERVICE_NAME}.service" | grep -q "$SERVICE_NAME"; then
        echo "ATTENZIONE: Il servizio $SERVICE_NAME è già installato."
        echo "Per reinstallarlo rimuoverlo prima con l'opzione 2."
        echo ""
        exit 0
    fi

    echo "Installazione del servizio $SERVICE_NAME..."

    # --- Scelta utente di esecuzione ---
    echo ""
    echo "Con quale utente deve girare il servizio?"
    echo ""
    echo "  1. Utente corrente: $SUDO_USER  (consigliato se i file appartengono a lui)"
    echo "  2. root            (sconsigliato per sicurezza)"
    echo "  3. Utente specifico"
    echo ""
    read -rp "Scelta (1/2/3): " userChoice

    case "$userChoice" in
        1)
            if [ -z "$SUDO_USER" ]; then
                RUN_USER="root"
                echo "ATTENZIONE: impossibile determinare l'utente originale, uso root."
            else
                RUN_USER="$SUDO_USER"
            fi
            ;;
        2)
            RUN_USER="root"
            ;;
        3)
            read -rp "Nome utente: " RUN_USER
            if ! id "$RUN_USER" &>/dev/null; then
                echo "ERRORE: utente '$RUN_USER' non trovato."
                exit 1
            fi
            ;;
        *)
            RUN_USER="${SUDO_USER:-root}"
            ;;
    esac

    echo "Il servizio girerà come utente: $RUN_USER"

    # Crea cartella log con i permessi corretti
    mkdir -p "$LOG_DIR"
    chown "$RUN_USER" "$LOG_DIR"

    # --- Crea il file unit systemd ---
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=$SERVICE_DISPLAY
After=network.target
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON_EXE $APP_SCRIPT
Restart=on-failure
RestartSec=5
StandardOutput=append:$LOG_DIR/service_stdout.log
StandardError=append:$LOG_DIR/service_stderr.log

[Install]
WantedBy=multi-user.target
EOF

    if [ $? -ne 0 ]; then
        echo "ERRORE: impossibile creare il file $SERVICE_FILE"
        exit 1
    fi

    # Ricarica la configurazione systemd
    systemctl daemon-reload

    # Abilita l'avvio automatico
    systemctl enable "$SERVICE_NAME"
    if [ $? -ne 0 ]; then
        echo "ERRORE: impossibile abilitare il servizio."
        exit 1
    fi

    echo ""
    echo "Servizio installato con successo."
    echo "Il servizio si avvierà automaticamente all'avvio del sistema."
    echo ""
    read -rp "Vuoi avviare il servizio ora? (s/n): " startNow
    if [[ "$startNow" =~ ^[Ss]$ ]]; then
        action="do_start"
    else
        exit 0
    fi
fi

# ---------------------------------------------------------------------------
# DO_START (usato da install e da start)
# ---------------------------------------------------------------------------
do_start() {
    echo ""
    echo "Avvio del servizio..."
    systemctl start "$SERVICE_NAME"
    if [ $? -ne 0 ]; then
        echo ""
        echo "ERRORE: comando di avvio fallito."
    fi

    # Attendi qualche secondo per lasciare tempo all'app di inizializzarsi
    sleep 4

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "Servizio avviato correttamente."
    else
        echo ""
        echo "ATTENZIONE: il servizio non risulta in esecuzione."
        echo ""
        echo "Controllare i log errori:"
        echo "  journalctl -u $SERVICE_NAME -n 50 --no-pager"
        echo "  cat $LOG_DIR/service_stderr.log"
        echo ""
        echo "Possibili cause:"
        echo "  - Dipendenze mancanti nel venv"
        echo "  - Permessi insufficienti per l'utente '$RUN_USER'"
        echo "  - Porta 5000 già in uso"
        echo "  - config.json o config.example.json mancanti"
    fi
}

if [ "$action" = "do_start" ]; then
    do_start
    exit 0
fi

# ---------------------------------------------------------------------------
# REMOVE
# ---------------------------------------------------------------------------
if [ "$action" = "remove" ]; then
    echo ""
    echo "Rimozione del servizio $SERVICE_NAME..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    echo "Servizio rimosso."
    exit 0
fi

# ---------------------------------------------------------------------------
# START
# ---------------------------------------------------------------------------
if [ "$action" = "start" ]; then
    RUN_USER=$(systemctl show "$SERVICE_NAME" -p User --value 2>/dev/null || echo "")
    do_start
    exit 0
fi

# ---------------------------------------------------------------------------
# STOP
# ---------------------------------------------------------------------------
if [ "$action" = "stop" ]; then
    echo ""
    echo "Arresto del servizio $SERVICE_NAME..."
    systemctl stop "$SERVICE_NAME"
    if [ $? -eq 0 ]; then
        echo "Servizio arrestato."
    else
        echo "ERRORE nell'arresto del servizio."
    fi
    exit 0
fi

# ---------------------------------------------------------------------------
# RESTART
# ---------------------------------------------------------------------------
if [ "$action" = "restart" ]; then
    echo ""
    echo "Riavvio del servizio $SERVICE_NAME..."
    systemctl restart "$SERVICE_NAME"
    sleep 4
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "Servizio riavviato correttamente."
    else
        echo "ERRORE: il servizio non risulta in esecuzione dopo il riavvio."
        echo "Controllare: journalctl -u $SERVICE_NAME -n 50 --no-pager"
    fi
    exit 0
fi

# ---------------------------------------------------------------------------
# STATUS
# ---------------------------------------------------------------------------
if [ "$action" = "status" ]; then
    echo ""
    systemctl status "$SERVICE_NAME"
    exit 0
fi

# ---------------------------------------------------------------------------
# SHOWLOG
# ---------------------------------------------------------------------------
if [ "$action" = "showlog" ]; then
    echo ""
    echo "=== Log systemd (ultime 50 righe) ==="
    echo ""
    journalctl -u "$SERVICE_NAME" -n 50 --no-pager 2>/dev/null \
        || echo "(journalctl non disponibile)"

    if [ -f "$LOG_DIR/service_stderr.log" ]; then
        echo ""
        echo "=== $LOG_DIR/service_stderr.log ==="
        echo ""
        tail -n 50 "$LOG_DIR/service_stderr.log"
    else
        echo ""
        echo "(Nessun log errori in $LOG_DIR/service_stderr.log)"
    fi
    exit 0
fi

# END
exit 0
