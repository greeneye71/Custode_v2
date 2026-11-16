#!/usr/bin/env bash
# Configurazione del deploy Podman di MedInventory (Custode).
#
#   sudo bash deploy/podman/configura.sh [comando]
#
# Senza comando apre il menu. Comandi:
#   installa     prepara l'host, costruisce l'immagine, installa le unità (non avvia)
#   nuovo        stato vuoto per una prima installazione
#   importa      copia lo stato di un'installazione esistente (sorgente: cartella)
#   token        token del tunnel Cloudflare, nel segreto di Podman
#   certificato  certificato HTTPS per la LAN
#   avvia        avvia il pod (unico punto di avvio)
#   stato        stato di unità, container, segreto, certificato, porta 443
#   aggiorna     ricostruisce l'immagine e riallinea le unità
#   tunnel-off   spegne il tunnel
#   disinstalla  rimuove le unità (i dati restano)
#
# Con --solo-funzioni come primo argomento definisce le funzioni e termina:
# serve ai test, che chiamano le funzioni pure senza Podman.
set -eu

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)

APP_USER=custode
IMAGE=localhost/custode:latest
POD_SERVICE=custode-pod.service
TUNNEL_SERVICE=custode-tunnel.service
LAN_SERVICE=custode-lan.service
TIMER=custode-diagnosi.timer
VOLUME_STATO=custode-stato
BACKUP_DIR=/var/backups/custode
SUBID=300000-365535
SYSCTL_FILE=/etc/sysctl.d/90-custode-porta-443.conf
VECCHIO_SERVIZIO=medinventory.service
CA_DIR=/etc/pki/minventory-ca
CA_CRT="$CA_DIR/ca.crt"
CA_KEY="$CA_DIR/ca.key"
QUADLETS="custode.pod custode-web.container custode-stato.volume"
USER_UNITS="custode-diagnosi.service custode-diagnosi.timer"

# --- helper ---------------------------------------------------------------------

ok() {
    printf 'OK: %s\n' "$*"
}

err() {
    printf 'ERRORE: %s\n' "$*" >&2
}

conferma() {
    local risposta
    printf '%s [s/N] ' "$1"
    read -r risposta || risposta=""
    case "$risposta" in
        s|S|si|SI|sì|Sì) return 0 ;;
        *) return 1 ;;
    esac
}

si_no() {
    if "$@" >/dev/null 2>&1; then
        printf 'sì'
    else
        printf 'no'
    fi
}

as_custode() {
    (cd / && runuser -u "$APP_USER" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$APP_USER")" "$@")
}

user_systemctl() {
    systemctl --user -M "$APP_USER@" "$@"
}

app_home() {
    getent passwd "$APP_USER" | cut -d: -f6
}

quadlet_dir() {
    printf '%s/.config/containers/systemd\n' "$(app_home)"
}

unit_dir() {
    printf '%s/.config/systemd/user\n' "$(app_home)"
}

lan_dir() {
    printf '%s/lan\n' "$(app_home)"
}

copia_come_custode() {
    # copia_come_custode <file_sorgente> <file_destinazione>
    install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$(dirname -- "$2")"
    install -o "$APP_USER" -g "$APP_USER" -m 0644 "$1" "$2"
}

crea_dir_utente() {
    # crea_dir_utente [cartella]: ogni livello sotto la home appartiene a custode
    # (install -d intestarebbe a root i livelli intermedi).
    local home dir rel corrente parte
    local parti=()
    home=$(app_home)
    install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$home/.config"
    install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$home/.config/containers"
    install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$home/.config/systemd"
    dir=${1:-}
    if [ -z "$dir" ]; then
        return 0
    fi
    rel=${dir#"$home"/}
    corrente=$home
    IFS=/ read -ra parti <<<"$rel"
    for parte in "${parti[@]}"; do
        corrente="$corrente/$parte"
        install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$corrente"
    done
}

utente_esiste() {
    id -u "$APP_USER" >/dev/null 2>&1
}

richiedi_utente() {
    if ! utente_esiste; then
        err "l'utente $APP_USER non esiste: esegui prima 'configura.sh installa'."
        return 1
    fi
}

installa_tunnel_quadlet() {
    # Il tunnel si installa solo quando esiste il segreto: Restart=always lo
    # farebbe altrimenti ripartire a vuoto.
    if as_custode podman secret exists custode-tunnel-token; then
        copia_come_custode "$SCRIPT_DIR/custode-tunnel.container" "$(quadlet_dir)/custode-tunnel.container"
        return 0
    fi
    return 1
}

pod_attivo() {
    user_systemctl is-active --quiet "$POD_SERVICE"
}

richiedi_immagine() {
    if ! as_custode podman image exists "$IMAGE"; then
        err "immagine $IMAGE assente: esegui prima 'configura.sh installa'"
        return 1
    fi
}

stato_presente() {
    # C'è già un database nel volume? Si guarda da un container usa e getta.
    as_custode podman run --rm -v "$VOLUME_STATO:/stato" "$IMAGE" \
        test -e /stato/data/database.sqlite
}

porta_443_aperta() {
    # Rootless non può pubblicare la 443 finché la soglia delle porte privilegiate
    # è sopra: il pod fallirebbe all'avvio con un errore di bind poco leggibile.
    [ "$(sysctl -n net.ipv4.ip_unprivileged_port_start)" -le 443 ]
}

apri_porta_443() {
    printf 'net.ipv4.ip_unprivileged_port_start=443\n' > "$SYSCTL_FILE"
    sysctl -p "$SYSCTL_FILE" >/dev/null
    ok "porte dalla 443 in su pubblicabili senza root ($SYSCTL_FILE)"
}

apri_firewall() {
    if firewall-cmd --state >/dev/null 2>&1; then
        firewall-cmd --permanent --add-service=https >/dev/null
        firewall-cmd --reload >/dev/null
        ok "firewall: servizio https aperto"
    else
        echo "firewalld non è attivo: nessuna regola aggiunta."
    fi
}

avvia_pod() {
    # L'unico punto da cui il pod parte o riparte.
    if ! porta_443_aperta; then
        err "la porta 443 non è pubblicabile senza root: esegui 'configura.sh installa'"
        return 1
    fi
    user_systemctl daemon-reload
    user_systemctl restart "$POD_SERVICE"
    user_systemctl enable --now "$TIMER"
    ok "pod avviato"
}

installa_lan_quadlet() {
    # Come il tunnel: custode-lan si installa solo quando ha un certificato da servire.
    local certs
    certs="$(lan_dir)/certs"
    if [ -f "$certs/custode.crt" ] && [ -f "$certs/custode.key" ]; then
        copia_come_custode "$SCRIPT_DIR/custode-lan.conf" "$(lan_dir)/conf.d/custode-lan.conf"
        copia_come_custode "$SCRIPT_DIR/custode-lan.container" "$(quadlet_dir)/custode-lan.container"
        return 0
    fi
    return 1
}

installa_unita() {
    local f
    crea_dir_utente "$(quadlet_dir)"
    crea_dir_utente "$(unit_dir)"
    crea_dir_utente "$(lan_dir)/conf.d"
    crea_dir_utente "$(lan_dir)/certs"
    for f in $QUADLETS; do
        copia_come_custode "$SCRIPT_DIR/$f" "$(quadlet_dir)/$f"
    done
    for f in $USER_UNITS; do
        copia_come_custode "$SCRIPT_DIR/$f" "$(unit_dir)/$f"
    done
    installa_tunnel_quadlet || echo "Tunnel non configurato: usa 'token'."
    installa_lan_quadlet || echo "Certificato LAN assente: usa 'certificato'."
    install -m 0755 "$SCRIPT_DIR/custode" /usr/local/bin/custode
    user_systemctl daemon-reload
    ok "Quadlet, unità di diagnosi e wrapper /usr/local/bin/custode installati"
}

costruisci_immagine() {
    # Come root (rete dell'host per il DNS della build), poi all'utente di servizio.
    podman build --network=host -t "$IMAGE" -f "$REPO_ROOT/Containerfile" "$REPO_ROOT"
    podman save "$IMAGE" | as_custode podman load
    ok "immagine $IMAGE disponibile per $APP_USER"
}

nome_host_valido() {
    [[ "${1:-}" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$ ]]
}

ip_valido() {
    local ip=${1:-} ottetto
    if ! [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
        return 1
    fi
    local IFS=.
    for ottetto in $ip; do
        if [ "$((10#$ottetto))" -gt 255 ]; then
            return 1
        fi
    done
    return 0
}

normalizza_token() {
    # Dal testo incollato tiene l'ultima parola: va bene il token nudo e va bene
    # l'intero comando "cloudflared service install eyJ..." copiato dalla dashboard.
    local raw=$1 last=""
    local parola
    for parola in $raw; do last=$parola; done
    if [[ "$last" =~ ^[A-Za-z0-9+/=_-]{40,}$ ]]; then
        printf '%s\n' "$last"
        return 0
    fi
    return 1
}

# --- testi per l'operatore (D11) -------------------------------------------------

mostra_ordine_migrazione() {
    cat <<EOF

MIGRAZIONE DA UN'INSTALLAZIONE ESISTENTE - l'ordine conta

  1. configura.sh installa
       prepara l'host, costruisce l'immagine, installa le unità. Non avvia nulla.
  2. systemctl stop $VECCHIO_SERVIZIO
       la vecchia applicazione va FERMATA prima di copiarne i dati, altrimenti il
       database copiato può essere a metà di una scrittura. Solo "stop": resta
       abilitata, così se qualcosa va storto basta riavviarla.
  3. configura.sh importa <cartella della vecchia installazione>
       copia data/, uploads/, backups/, config.json e config.local.json.
       config.local.json contiene encryption_key: senza quella chiave le
       password di posta salvate nel database non si decifrano più.
  4. configura.sh token
       il token del tunnel entra nel segreto di Podman custode-tunnel-token.
  5. systemctl disable --now <unità cloudflared di Custode sull'host>
     systemctl disable --now $VECCHIO_SERVIZIO
       le vecchie unità si spengono per sempre solo adesso.
  6. configura.sh avvia
       parte il pod: applicazione, tunnel e HTTPS sulla LAN.

Per tornare indietro: ferma il pod
  (systemctl --user -M $APP_USER@ stop $POD_SERVICE)
e riabilita le vecchie unità con "systemctl enable --now ...".
EOF
}

avviso_cloudflared() {
    cat <<EOF

ATTENZIONE: sull'host ci sono due processi cloudflared, uno è di mCorsi
(porta 5100): non va toccato. Per distinguerli:

  ps -eo pid,user,unit,args | grep '[c]loudflared' | sed 's/--token [^ ]*/--token ***/'

Quello da spegnere è il cloudflared di Custode: NON gira come utente mcorsi
e non appartiene alle sue unità.

Lo stesso token attivo in due posti significa due repliche dello stesso tunnel:
Cloudflare distribuisce le richieste tra le due, e quella rimasta sull'host,
che non ha più l'applicazione dietro, risponde 502 a intermittenza.

Il token si copia dalla dashboard Cloudflare:
  Zero Trust -> Networks -> Tunnels -> (il tunnel di Custode) -> Configure
Serve solo la stringa che comincia con eyJ...
NON usare "Refresh token" in dashboard: invalida subito il vecchio, e il
cloudflared ancora in servizio sull'host si scollega all'istante.
EOF
}

# --- installazione e avvio --------------------------------------------------------

cmd_installa() {
    if ! utente_esiste; then
        useradd --system --create-home --shell /sbin/nologin "$APP_USER"
        ok "utente di sistema $APP_USER creato"
    fi
    if ! grep -q "^$APP_USER:" /etc/subuid 2>/dev/null; then
        usermod --add-subuids "$SUBID" --add-subgids "$SUBID" "$APP_USER"
        ok "subuid/subgid $SUBID assegnati a $APP_USER"
    fi
    loginctl enable-linger "$APP_USER"
    apri_porta_443
    apri_firewall
    install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$BACKUP_DIR"
    costruisci_immagine
    installa_unita
    echo
    echo "Host pronto. Il pod NON è stato avviato. Passi successivi:"
    echo "  - installazione nuova:  configura.sh nuovo, poi certificato, token, avvia"
    echo "  - migrazione:           segui l'ordine qui sotto, dal passo 2"
    mostra_ordine_migrazione
}

cmd_nuovo() {
    # Stato vuoto per una prima installazione. Non avvia il pod.
    richiedi_utente || return 1
    richiedi_immagine || return 1
    if pod_attivo; then
        err "il pod è acceso: lo stato esiste già"
        return 1
    fi
    if stato_presente; then
        err "nel volume c'è già un database: per sostituirlo usa importa"
        return 1
    fi
    as_custode podman run --rm -v "$VOLUME_STATO:/stato" "$IMAGE" python3 /app/seed.py
    if ! as_custode podman run --rm -v "$VOLUME_STATO:/stato" "$IMAGE" \
            python3 /app/deploy/podman/config_container.py adegua; then
        err "adeguamento della configurazione fallito: non avviare il pod."
        return 1
    fi
    ok "stato iniziale creato"
    echo "Credenziali predefinite: admin@medinventory.local / admin123"
    echo "Da cambiare al primo accesso."
    echo "Per un'installazione multi-struttura serve anche un superadmin:"
    echo "  sudo custode crea_superadmin.py"
    echo "Passi successivi: configura.sh certificato, configura.sh token, configura.sh avvia"
}

cmd_avvia() {
    richiedi_utente || return 1
    richiedi_immagine || return 1
    if systemctl is-active --quiet "$VECCHIO_SERVIZIO"; then
        err "$VECCHIO_SERVIZIO è ancora attivo: due applicazioni sullo stesso tunnel."
        err "Spegnilo con: systemctl disable --now $VECCHIO_SERVIZIO"
        return 1
    fi
    if ! stato_presente; then
        err "nel volume $VOLUME_STATO non c'è un database: esegui prima 'nuovo' oppure 'importa'"
        return 1
    fi
    if pgrep -x cloudflared >/dev/null 2>&1; then
        avviso_cloudflared
        echo
        echo "Processi cloudflared sull'host in questo momento:"
        ps -eo pid,user,unit,args | grep '[c]loudflared' | sed 's/--token [^ ]*/--token ***/' || true
        echo
        conferma "Il cloudflared di Custode sull'host è spento (quello di mCorsi resta acceso)?" || return 1
    fi
    avvia_pod
    cmd_stato
}

cmd_aggiorna() {
    richiedi_utente || return 1
    costruisci_immagine
    installa_unita
    if pod_attivo; then
        avvia_pod
    else
        echo "pod fermo: avvialo con 'avvia'"
    fi
}

# --- tunnel -----------------------------------------------------------------------

cmd_token() {
    local incollato token
    richiedi_utente || return 1
    avviso_cloudflared
    echo
    echo "Incolla il token (o l'intero comando della dashboard) e premi Invio."
    echo "Non verrà mostrato."
    read -rs incollato
    echo
    if ! token=$(normalizza_token "$incollato"); then
        unset incollato
        err "non sembra un token di tunnel: attesa una stringa lunga che comincia con eyJ"
        return 1
    fi
    unset incollato
    printf '%s' "$token" | as_custode podman secret create --replace custode-tunnel-token - >/dev/null
    unset token
    ok "token salvato nel segreto custode-tunnel-token"
    installa_tunnel_quadlet
    user_systemctl daemon-reload
    if pod_attivo; then
        echo "Il pod è acceso: il tunnel parte adesso con il nuovo token."
        conferma "Il vecchio cloudflared di Custode sull'host è già spento?" || {
            echo "Tunnel non avviato. Spegni il vecchio, poi: configura.sh avvia"
            return 0
        }
        user_systemctl restart "$TUNNEL_SERVICE"
        ok "tunnel riavviato"
    else
        echo "Il tunnel partirà con il pod: configura.sh avvia"
    fi
}

cmd_tunnel_off() {
    richiedi_utente || return 1
    user_systemctl stop "$TUNNEL_SERVICE" 2>/dev/null || true
    rm -f "$(quadlet_dir)/custode-tunnel.container"
    user_systemctl daemon-reload
    ok "tunnel spento e unità rimossa"
    echo "Il segreto custode-tunnel-token resta: per sostituirlo usa 'configura.sh token',"
    echo "per rimuoverlo 'configura.sh disinstalla'."
}

# --- certificato LAN --------------------------------------------------------------

ca_crea() {
    # Una volta sola.
    install -d -m 0700 "$CA_DIR"
    (
        umask 077
        openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes -keyout "$CA_KEY" -out "$CA_CRT" -subj "/C=IT/O=Studio Bergamaschi/CN=Studio Bergamaschi Internal CA"
    )
    chmod 600 "$CA_KEY"
    ok "CA interna creata in $CA_DIR"
    echo "La chiave della CA resta su questo server, leggibile solo da root."
    echo "Chi la ottiene può emettere certificati validi per qualunque sito agli"
    echo "occhi dei PC che si fidano della CA. Ai client va distribuito solo $CA_CRT."
}

installa_certificato() {
    # installa_certificato <crt> <key>: gli unici due file che raggiungono il container.
    local crt=$1 key=$2 CERT_DIR
    CERT_DIR="$(lan_dir)/certs"
    crea_dir_utente "$(lan_dir)/conf.d"
    crea_dir_utente "$CERT_DIR"
    install -o "$APP_USER" -g "$APP_USER" -m 0644 "$crt" "$CERT_DIR/custode.crt"
    install -o "$APP_USER" -g "$APP_USER" -m 0600 "$key" "$CERT_DIR/custode.key"
    installa_lan_quadlet
    user_systemctl daemon-reload
    if pod_attivo; then
        user_systemctl restart "$LAN_SERVICE"
        ok "certificato installato, custode-lan riavviato"
    else
        ok "certificato installato: custode-lan partirà con il pod"
    fi
    echo "Scadenza: $(openssl x509 -noout -enddate -in "$crt" | cut -d= -f2)"
    echo "Segnala in agenda: alla scadenza il gestionale smette di aprirsi dalla LAN."
}

certificato_genera() {
    local nome ip giorni
    if [ ! -f "$CA_KEY" ] || [ ! -f "$CA_CRT" ]; then
        echo "In $CA_DIR non c'è una CA interna."
        conferma "Crearla adesso (vale dieci anni, si fa una volta sola)?" || return 1
        ca_crea
    fi
    read -r -p "Nome con cui i PC della LAN aprono il gestionale (es. custode.studio.lan): " nome
    if ! nome_host_valido "$nome"; then
        err "nome non valido: solo lettere, cifre, trattini e punti"
        return 1
    fi
    read -r -p "Indirizzo IP di questo server sulla LAN (es. 192.168.1.215): " ip
    if ! ip_valido "$ip"; then
        err "indirizzo IPv4 non valido"
        return 1
    fi
    read -r -p "Durata in giorni [825; 398 se un browser rifiuta la durata]: " giorni
    giorni=${giorni:-825}
    if ! [[ "$giorni" =~ ^[1-9][0-9]{0,3}$ ]]; then
        err "durata non valida"
        return 1
    fi
    (
        umask 077
        cd "$CA_DIR"
        cat > custode-san.cnf <<EOF
[req]
distinguished_name = dn
[dn]
[v3_req]
subjectAltName   = @alt
basicConstraints = CA:FALSE
keyUsage         = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
[alt]
DNS.1 = $nome
IP.1  = $ip
EOF
        openssl req -newkey rsa:2048 -nodes -keyout custode.key -out custode.csr -subj "/C=IT/O=Studio Bergamaschi/CN=$nome" -config custode-san.cnf
        openssl x509 -req -in custode.csr -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial -out custode.crt -days "$giorni" -sha256 -extfile custode-san.cnf -extensions v3_req
    )
    installa_certificato "$CA_DIR/custode.crt" "$CA_DIR/custode.key"
    echo
    echo "Ai client va installato SOLO $CA_CRT (è pubblico), tra le autorità radice attendibili."
    echo "Il nome $nome deve risolvere a $ip: record statico sul DNS del router."
}

certificato_esistente() {
    local crt key
    read -r -p "Percorso del certificato (.crt, PEM): " crt
    read -r -p "Percorso della chiave privata (.key, PEM): " key
    if [ ! -f "$crt" ] || [ ! -f "$key" ]; then
        err "file non trovato"
        return 1
    fi
    if openssl x509 -noout -ext basicConstraints -in "$crt" 2>/dev/null | grep -q 'CA:TRUE'; then
        err "quello è il certificato di una CA, non del server: la sua chiave non va installata qui"
        return 1
    fi
    if [ "$(openssl x509 -noout -pubkey -in "$crt" | sha256sum)" != "$(openssl pkey -pubout -in "$key" | sha256sum)" ]; then
        err "certificato e chiave non corrispondono"
        return 1
    fi
    installa_certificato "$crt" "$key"
}

cmd_certificato() {
    local scelta
    richiedi_utente || return 1
    echo "Certificato HTTPS per la LAN"
    echo "  1) genera e firma con la CA interna ($CA_DIR)"
    echo "  2) installa un certificato esistente"
    read -r -p "Scelta [1]: " scelta
    case "${scelta:-1}" in
        1) certificato_genera ;;
        2) certificato_esistente ;;
        *) err "scelta non valida"; return 1 ;;
    esac
}

# --- migrazione -------------------------------------------------------------------

cmd_importa() {
    local sorgente=${1:-} staging voce
    richiedi_utente || return 1
    richiedi_immagine || return 1
    if [ -z "$sorgente" ]; then
        read -r -p "Cartella dell'installazione esistente (es. /opt/medinventory): " sorgente
    fi
    if [ ! -f "$sorgente/data/database.sqlite" ]; then
        err "in '$sorgente' non c'è data/database.sqlite: non è un'installazione di MedInventory"
        return 1
    fi
    if systemctl is-active --quiet "$VECCHIO_SERVIZIO"; then
        err "$VECCHIO_SERVIZIO è ancora attivo: il database copiato potrebbe essere a metà di una scrittura."
        err "Fermalo con: systemctl stop $VECCHIO_SERVIZIO   (solo stop: il disable viene dopo il token)"
        return 1
    fi
    if pod_attivo; then
        err "il pod di Custode è acceso: fermalo prima (systemctl --user -M $APP_USER@ stop $POD_SERVICE)"
        return 1
    fi
    if stato_presente; then
        conferma "Nel volume c'è già un database: SOSTITUIRLO con quello di '$sorgente'?" || return 1
    fi

    # Copia di lavoro: root legge la vecchia installazione, custode legge la copia.
    # La cartella della vecchia installazione non viene mai montata né rietichettata.
    # La pulizia è legata all'uscita del processo (EXIT), non al ritorno della
    # funzione: scatta anche con set -e e su un errore del chiamante. Il menu
    # esegue ogni comando in una subshell, così scatta a fine comando.
    staging=$(mktemp -d /var/tmp/custode-importa.XXXXXX)
    trap "rm -rf '$staging'" EXIT
    chmod 0700 "$staging"
    for voce in data uploads config.json config.local.json; do
        if [ -e "$sorgente/$voce" ]; then
            cp -a "$sorgente/$voce" "$staging/" || return 1
        fi
    done
    chown -R "$APP_USER:$APP_USER" "$staging"

    echo "Controllo dei percorsi di config.json e config.local.json..."
    if ! as_custode podman run --rm -v "$staging:/sorgente:Z" "$IMAGE" \
            python3 /app/deploy/podman/config_container.py adegua --radice /sorgente --prova; then
        err "la configurazione importata ha percorsi che nel container non esistono (vedi sopra)."
        err "Nel volume non è stato scritto nulla. Correggi i percorsi nell'installazione di"
        err "origine (database in data/, allegati in uploads/) e rilancia 'importa'."
        return 1
    fi

    if ! as_custode podman run --rm \
        -v "$VOLUME_STATO:/stato" -v "$staging:/sorgente:Z" "$IMAGE" sh -c '
            set -eu
            rm -rf /stato/data /stato/uploads
            rm -f /stato/config.json /stato/config.local.json
            for voce in data uploads config.json config.local.json; do
                # Niente cp -a: copierebbe il contesto SELinux, cioè l etichetta MCS
                # privata del mount :Z, e i container successivi non leggerebbero più il volume.
                if [ -e "/sorgente/$voce" ]; then cp -R --preserve=mode,ownership,timestamps "/sorgente/$voce" /stato/; fi
            done
            mkdir -p /stato/data /stato/uploads /stato/logs'; then
        err "copia nel volume fallita: non avviare il pod."
        return 1
    fi
    if [ -d "$sorgente/backups" ]; then
        cp -a "$sorgente/backups/." "$BACKUP_DIR/"
        chown -R "$APP_USER:$APP_USER" "$BACKUP_DIR"
    fi
    if ! as_custode podman run --rm -v "$VOLUME_STATO:/stato" "$IMAGE" \
            python3 /app/deploy/podman/config_container.py adegua; then
        err "adeguamento della configurazione fallito dopo la copia: non avviare il pod."
        return 1
    fi
    ok "dati importati da $sorgente"
    echo "config.local.json è stato copiato con la sua encryption_key: le password di"
    echo "posta salvate restano leggibili."
    echo
    echo "Mancano, in quest'ordine: token, spegnimento delle vecchie unità, avvia."
    mostra_ordine_migrazione
}

# --- stato e rimozione ------------------------------------------------------------

cmd_stato() {
    local s c crt
    richiedi_utente || return 1
    for s in "$POD_SERVICE" custode-web.service "$TUNNEL_SERVICE" "$LAN_SERVICE" "$TIMER"; do
        user_systemctl status "$s" --no-pager --lines=0 || true
        echo
    done
    as_custode podman ps --pod || true
    echo
    printf 'Avvio automatico (linger): %s\n' \
        "$(loginctl show-user "$APP_USER" --property=Linger --value 2>/dev/null || echo no)"
    printf 'Tunnel configurato (segreto): %s\n' "$(si_no as_custode podman secret exists custode-tunnel-token)"
    crt="$(lan_dir)/certs/custode.crt"
    printf 'Certificato LAN: %s\n' "$(si_no test -f "$crt")"
    if [ -f "$crt" ]; then
        echo "  scadenza: $(openssl x509 -noout -enddate -in "$crt" | cut -d= -f2)"
    fi
    printf 'Porta 443 pubblicabile senza root: %s\n' "$(si_no porta_443_aperta)"
    echo
    user_systemctl list-timers "$TIMER" --no-pager || true
    echo
    for c in custode-web custode-tunnel custode-lan; do
        echo "--- ultimi log di $c ---"
        journalctl CONTAINER_NAME="$c" -n 15 --no-pager || true
    done
}

cmd_disinstalla() {
    local f
    richiedi_utente || return 1
    if ! conferma "Fermare e rimuovere il servizio Custode (i dati restano)?"; then
        echo "Annullato."
        return 0
    fi
    user_systemctl disable --now "$TIMER" || true
    user_systemctl stop "$POD_SERVICE" || true
    for f in $QUADLETS custode-tunnel.container custode-lan.container; do
        rm -f "$(quadlet_dir)/$f"
    done
    for f in $USER_UNITS; do
        rm -f "$(unit_dir)/$f"
    done
    user_systemctl daemon-reload || true
    loginctl disable-linger "$APP_USER"
    rm -f /usr/local/bin/custode
    ok "servizio rimosso. Dati, backup, token, certificati e utente sono stati conservati."
    cat <<EOF

ATTENZIONE: il volume $VOLUME_STATO contiene database e allegati.
La CA in $CA_DIR non viene toccata.

Per cancellare anche i dati (irreversibile), un comando per riga:
  sudo runuser -u $APP_USER -- env XDG_RUNTIME_DIR=/run/user/\$(id -u $APP_USER) podman volume rm $VOLUME_STATO
  sudo runuser -u $APP_USER -- env XDG_RUNTIME_DIR=/run/user/\$(id -u $APP_USER) podman secret rm custode-tunnel-token
  sudo rm -rf $BACKUP_DIR
  sudo rm -rf $(lan_dir)
  sudo userdel -r $APP_USER
  sudo rm -f $SYSCTL_FILE
  sudo firewall-cmd --permanent --remove-service=https
EOF
}

# --- dispatch ---------------------------------------------------------------------

esegui() {
    local comando=${1:-}
    if [ $# -gt 0 ]; then
        shift
    fi
    case "$comando" in
        installa)    cmd_installa ;;
        nuovo)       cmd_nuovo ;;
        importa)     cmd_importa "$@" ;;
        token)       cmd_token ;;
        certificato) cmd_certificato ;;
        avvia)       cmd_avvia ;;
        stato)       cmd_stato ;;
        aggiorna)    cmd_aggiorna ;;
        tunnel-off)  cmd_tunnel_off ;;
        disinstalla) cmd_disinstalla ;;
        *)
            err "comando sconosciuto: '$comando'"
            echo "Comandi: installa nuovo importa token certificato avvia stato aggiorna tunnel-off disinstalla"
            return 2
            ;;
    esac
}

dal_menu() {
    # Un comando del menu gira in una subshell (suoi trap di pulizia) con errexit
    # attivo davvero: dentro un contesto `||` bash lo spegnerebbe e un passo fallito
    # non fermerebbe i successivi. Il codice di uscita si legge con un assegnamento
    # a errexit sospeso, che poi si riattiva: lo script non muore sul fallimento.
    local rc
    set +e
    ( set -e; esegui "$@" )
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
        err "'$*' non è andato a buon fine (codice $rc)"
    fi
    return 0
}

menu() {
    local scelta
    while true; do
        cat <<'MENU'

Custode - configurazione Podman
  1) Installa
  2) Token del tunnel Cloudflare
  3) Certificato LAN
  4) Stato
  5) Aggiorna
  6) Disinstalla

Prima installazione e migrazione
  7) Nuovo (stato vuoto)
  8) Importa da un'installazione esistente
  9) Avvia
 10) Spegni il tunnel
  0) Esci
MENU
        printf 'Scelta: '
        read -r scelta || return 0
        case "$scelta" in
            1) dal_menu installa ;;
            2) dal_menu token ;;
            3) dal_menu certificato ;;
            4) dal_menu stato ;;
            5) dal_menu aggiorna ;;
            6) dal_menu disinstalla ;;
            7) dal_menu nuovo ;;
            8) dal_menu importa ;;
            9) dal_menu avvia ;;
            10) dal_menu tunnel-off ;;
            0) return 0 ;;
            *) echo "Scelta non valida." ;;
        esac
    done
}

if [ "${1:-}" = "--solo-funzioni" ]; then
    return 0 2>/dev/null || exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
    err "Esegui con: sudo bash deploy/podman/configura.sh"
    exit 1
fi

if [ $# -eq 0 ]; then
    menu
else
    esegui "$@"
fi
