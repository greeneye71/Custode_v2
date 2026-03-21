# MedInventory — Accesso Remoto via Cloudflare Tunnel

*by Studio Bergamaschi*

Questa guida spiega come rendere MedInventory accessibile da internet in modo sicuro
usando **Cloudflare Tunnel** (`cloudflared`), senza aprire porte sul router e senza
richiedere un IP pubblico fisso.

---

## Come funziona

```
Browser remoto
      │
      ▼ HTTPS (cifrato)
Cloudflare (edge globale)
      │
      ▼ Tunnel cifrato (connessione uscente)
cloudflared  ←── gira sullo stesso server di MedInventory
      │
      ▼ HTTP locale
MedInventory su localhost:5000
```

`cloudflared` apre una connessione **uscente** verso Cloudflare.
Non occorre configurare il router, non occorre un IP fisso.

---

## Prerequisiti

| Cosa | Note |
|---|---|
| Account Cloudflare gratuito | [cloudflare.com](https://cloudflare.com) |
| Un dominio gestito da Cloudflare | Es. `tuodominio.it` — i DNS devono essere su Cloudflare |
| MedInventory funzionante in locale | Su `http://localhost:5000` |
| Accesso amministrativo al server | Per installare il servizio |

> **Nota dominio:** serve un dominio reale (anche economico, ~10 €/anno). Cloudflare gestisce
> i DNS gratuitamente. Se non si ha un dominio, servizi come Namecheap o Porkbun offrono
> domini `.it` o `.eu` a basso costo.

---

## Parte 1 — Installazione di `cloudflared`

### Windows

**Metodo A — winget (Windows 10/11, consigliato):**

Aprire il Prompt dei comandi come Amministratore ed eseguire:

```bat
winget install --id Cloudflare.cloudflared
```

**Metodo B — download manuale:**

1. Scaricare `cloudflared-windows-amd64.exe` dalla pagina
   [github.com/cloudflare/cloudflared/releases](https://github.com/cloudflare/cloudflared/releases)
2. Creare la cartella `C:\Cloudflared\bin\`
3. Copiare il file scaricato in quella cartella e rinominarlo `cloudflared.exe`
4. Aggiungere `C:\Cloudflared\bin` alla variabile d'ambiente `PATH` di sistema

Verificare l'installazione:
```bat
cloudflared --version
```

---

### Linux (Debian / Ubuntu)

```bash
# Aggiunge il repository ufficiale Cloudflare
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
    | sudo tee /usr/share/keyrings/cloudflare-main.gpg > /dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
    https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" \
    | sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt update && sudo apt install cloudflared
```

**Oppure, download diretto del binario:**

```bash
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared
```

Verificare l'installazione:
```bash
cloudflared --version
```

---

## Parte 2 — Autenticazione

Eseguire il comando seguente (sia su Windows che su Linux).
Su Windows aprire il Prompt dei comandi **come Amministratore**.

```bash
cloudflared tunnel login
```

Si apre il browser sulla pagina di autorizzazione Cloudflare. Accedere con l'account
Cloudflare e selezionare il dominio da usare (es. `tuodominio.it`).

Al termine, viene salvato automaticamente il file `cert.pem`:

| Sistema | Percorso |
|---|---|
| Windows | `C:\Users\<Utente>\.cloudflared\cert.pem` |
| Linux | `~/.cloudflared/cert.pem` |

---

## Parte 3 — Creazione del tunnel

```bash
cloudflared tunnel create medinventory
```

L'output mostra l'**UUID del tunnel** (es. `a1b2c3d4-...`). Annotarlo.

Viene creato il file delle credenziali:

| Sistema | Percorso |
|---|---|
| Windows | `C:\Users\<Utente>\.cloudflared\<UUID>.json` |
| Linux | `~/.cloudflared/<UUID>.json` |

Verificare che il tunnel sia stato creato:
```bash
cloudflared tunnel list
```

---

## Parte 4 — File di configurazione

Creare il file `config.yml` nella cartella `.cloudflared`.

> Sostituire `<UUID>` con l'UUID ottenuto al passo precedente,
> e `medinventory.tuodominio.it` con il sottodominio scelto.

### Windows

Percorso: `C:\Users\<Utente>\.cloudflared\config.yml`

```yaml
tunnel: <UUID>
credentials-file: C:\Users\<Utente>\.cloudflared\<UUID>.json

ingress:
  - hostname: medinventory.tuodominio.it
    service: http://localhost:5000
  - service: http_status:404
```

### Linux

Percorso: `~/.cloudflared/config.yml`

```yaml
tunnel: <UUID>
credentials-file: /home/<utente>/.cloudflared/<UUID>.json

ingress:
  - hostname: medinventory.tuodominio.it
    service: http://localhost:5000
  - service: http_status:404
```

> L'ultima regola (`http_status:404`) è obbligatoria come catch-all per tutte le
> richieste che non corrispondono ad alcun hostname.

---

## Parte 5 — Configurazione DNS

Questo comando crea automaticamente il record DNS su Cloudflare
che punta il sottodominio al tunnel:

```bash
cloudflared tunnel route dns medinventory medinventory.tuodominio.it
```

Verificare che nel pannello DNS di Cloudflare sia comparso un record `CNAME`
di tipo `medinventory.tuodominio.it → <UUID>.cfargotunnel.com`.

---

## Parte 6 — Test manuale

Prima di installare il servizio, avviare il tunnel manualmente per verificare
che tutto funzioni:

```bash
cloudflared tunnel run medinventory
```

Aprire nel browser: `https://medinventory.tuodominio.it`

MedInventory deve essere raggiungibile con HTTPS (certificato gestito automaticamente
da Cloudflare). Premere `Ctrl+C` per fermare il tunnel.

---

## Parte 7 — Installazione come servizio (avvio automatico)

### Windows

Su Windows il servizio viene installato nella cartella del profilo di sistema.
Eseguire i seguenti comandi **come Amministratore**.

**1. Creare la cartella di sistema:**
```bat
mkdir C:\Windows\System32\config\systemprofile\.cloudflared
```

**2. Copiare i file di configurazione:**
```bat
copy C:\Users\<Utente>\.cloudflared\config.yml       C:\Windows\System32\config\systemprofile\.cloudflared\
copy C:\Users\<Utente>\.cloudflared\<UUID>.json       C:\Windows\System32\config\systemprofile\.cloudflared\
copy C:\Users\<Utente>\.cloudflared\cert.pem          C:\Windows\System32\config\systemprofile\.cloudflared\
```

**3. Aggiornare il percorso delle credenziali in `config.yml`:**

Aprire `C:\Windows\System32\config\systemprofile\.cloudflared\config.yml`
e aggiornare `credentials-file` con il percorso di sistema:

```yaml
tunnel: <UUID>
credentials-file: C:\Windows\System32\config\systemprofile\.cloudflared\<UUID>.json

ingress:
  - hostname: medinventory.tuodominio.it
    service: http://localhost:5000
  - service: http_status:404
```

**4. Installare il servizio:**
```bat
cloudflared.exe service install
```

**5. Avviare il servizio:**
```bat
sc start cloudflared
```

**Gestione del servizio Windows:**

| Azione | Comando |
|---|---|
| Avvia | `sc start cloudflared` |
| Ferma | `sc stop cloudflared` |
| Stato | `sc query cloudflared` |
| Rimuovi | `cloudflared.exe service uninstall` |

---

### Linux

**1. Copiare i file in `/etc/cloudflared/`:**

```bash
sudo mkdir -p /etc/cloudflared

sudo cp ~/.cloudflared/<UUID>.json  /etc/cloudflared/
sudo cp ~/.cloudflared/cert.pem     /etc/cloudflared/
```

**2. Creare `/etc/cloudflared/config.yml`:**

```bash
sudo nano /etc/cloudflared/config.yml
```

Contenuto (aggiornare UUID e utente):

```yaml
tunnel: <UUID>
credentials-file: /etc/cloudflared/<UUID>.json

ingress:
  - hostname: medinventory.tuodominio.it
    service: http://localhost:5000
  - service: http_status:404
```

**3. Installare il servizio:**
```bash
sudo cloudflared --config /etc/cloudflared/config.yml service install
```

**4. Avviare e abilitare:**
```bash
sudo systemctl start cloudflared
sudo systemctl enable cloudflared
```

**Gestione del servizio Linux:**

| Azione | Comando |
|---|---|
| Avvia | `sudo systemctl start cloudflared` |
| Ferma | `sudo systemctl stop cloudflared` |
| Riavvia | `sudo systemctl restart cloudflared` |
| Stato | `systemctl status cloudflared` |
| Log | `journalctl -u cloudflared -n 50` |
| Rimuovi | `sudo cloudflared service uninstall` |

---

## Parte 8 — Cloudflare Access (autenticazione aggiuntiva)

> **Consigliato per dati sanitari.** Aggiunge un layer di login davanti a MedInventory,
> indipendente dall'applicazione. Solo gli utenti autorizzati possono raggiungere
> la pagina di login di MedInventory.

### Configurazione

1. Accedere al pannello **Cloudflare Zero Trust**:
   `one.dash.cloudflare.com` → *Access* → *Applications* → **Add an application**

2. Scegliere **Self-hosted**

3. Configurare:
   - **Application name:** MedInventory
   - **Application domain:** `medinventory.tuodominio.it`

4. Creare una **Policy**:
   - **Policy name:** Accesso autorizzato
   - **Action:** Allow
   - **Include:** scegliere il metodo di verifica identità:

| Metodo | Quando usarlo |
|---|---|
| **One-time PIN** (OTP via email) | Soluzione più semplice, nessuna configurazione aggiuntiva |
| **Google / Microsoft** | Se gli utenti hanno già questi account |
| **Email list** | Whitelist di indirizzi email specifici |

5. Salvare. Da questo momento, accedendo a `https://medinventory.tuodominio.it`
   comparirà prima la schermata di verifica Cloudflare, poi il login di MedInventory.

---

## Parte 9 — Manutenzione

### Aggiornare `cloudflared`

**Windows (winget):**
```bat
winget upgrade --id Cloudflare.cloudflared
sc stop cloudflared
sc start cloudflared
```

**Windows (manuale):** scaricare il nuovo `.exe`, sostituire il file, riavviare il servizio.

**Linux (apt):**
```bash
sudo apt update && sudo apt upgrade cloudflared
sudo systemctl restart cloudflared
```

**Linux (binario):**
```bash
sudo cloudflared update
sudo systemctl restart cloudflared
```

### Verificare lo stato del tunnel

Dal pannello Cloudflare: **Zero Trust** → *Networks* → *Tunnels* →
il tunnel `medinventory` deve mostrare stato **Healthy**.

### Modificare la configurazione

Dopo ogni modifica al `config.yml` riavviare il servizio:

```bash
# Linux
sudo systemctl restart cloudflared

# Windows
sc stop cloudflared && sc start cloudflared
```

---

## Risoluzione problemi

**Il tunnel non si connette:**
```bash
cloudflared tunnel --loglevel debug run medinventory
```

**Errore "credentials file not found":**
Verificare che il percorso `credentials-file` in `config.yml` sia corretto
e che il file `<UUID>.json` esista in quel percorso.

**Errore 502 Bad Gateway:**
MedInventory non è in esecuzione o non è raggiungibile su `localhost:5000`.
Verificare con:
```bash
curl http://localhost:5000
```

**Il servizio si avvia ma il sito non risponde:**
Controllare i log:
```bash
# Linux
journalctl -u cloudflared -n 100 --no-pager

# Windows
sc query cloudflared
# poi aprire il Visualizzatore eventi → Registro Windows → Applicazione
```

---

## Note di sicurezza e GDPR

| Aspetto | Note |
|---|---|
| **Cifratura** | Tutto il traffico tra browser e Cloudflare è HTTPS (TLS 1.3). Il tunnel interno usa QUIC/HTTP2 cifrato. |
| **Dati in transito** | I dati passano per i server Cloudflare (USA/EU). Cloudflare è certificata ISO 27001 e GDPR-compliant. |
| **Dati a riposo** | MedInventory e il database rimangono sul server locale. Cloudflare non memorizza i dati applicativi. |
| **Autenticazione** | Con Cloudflare Access abilitato, nessun accesso non autorizzato può raggiungere l'applicazione. |
| **Alternativa totalmente locale** | Per chi non vuole che il traffico passi per terze parti, valutare WireGuard VPN (dati sempre nella rete locale). |

---

*Documentazione MedInventory v1.1.4 — Studio Bergamaschi*

**Riferimenti:**
- [Cloudflare Tunnel — Documentazione ufficiale](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
- [cloudflared — Release e download](https://github.com/cloudflare/cloudflared/releases)
- [Cloudflare Zero Trust — Access](https://developers.cloudflare.com/cloudflare-one/policies/access/)
