# Installazione in container (Fedora, Podman, Quadlet)

Questo documento descrive l'installazione di MedInventory (Custode) in container su Fedora, con Podman rootless e unit Quadlet. Si legge nel repository sull'host: la cartella `docs/` non è inclusa nell'immagine.

## Cosa viene installato

- Un utente di sistema `custode`, senza shell, con linger attivo (i servizi partono all'avvio dell'host anche senza una sessione aperta).
- Un pod `custode` (`custode-pod.service`) con tre container:
  - `custode-web`: l'applicazione, in ascolto su `127.0.0.1:5000` dentro il pod;
  - `custode-lan`: nginx con TLS, l'unica porta pubblicata del pod è `8443:443`;
  - `custode-tunnel`: cloudflared, per l'accesso dall'esterno.
- Il volume `custode-stato`, montato in `/stato`: database, `uploads/`, `config.json`, `config.local.json` (`MEDINVENTORY_RADICE=/stato`; i sorgenti stanno in `/app`).
- La cartella dei backup sull'host, `/var/backups/custode`.
- Il timer `custode-diagnosi.timer`, che lancia periodicamente la diagnosi (`custode-diagnosi.service`).

La porta 5000 non è pubblicata e non va mai pubblicata: dalla LAN si entra solo dalla 8443, dall'esterno solo dal tunnel.

## Prerequisiti

Fedora con systemd, SELinux in modalità enforcing, Podman 5.0 o successivo.

```bash
sudo dnf install podman firewalld git openssl
podman --version
```

`podman --version` deve riportare 5.0 o superiore. Poi si clona il repository in una cartella dell'amministratore (`git clone ...`) e ci si entra con `cd`: tutti i comandi `configura.sh` di questo documento si eseguono da lì, come root, nella forma `sudo bash deploy/podman/configura.sh <sottocomando>`.

## Comandi di configura.sh

Senza argomenti `configura.sh` apre un menu. Con un sottocomando esegue direttamente quel passo.

| Comando | Effetto |
|---|---|
| `installa` | prepara l'host, costruisce l'immagine, installa le unit (non avvia) |
| `nuovo` | stato vuoto per una prima installazione |
| `importa <cartella>` | copia lo stato di un'installazione esistente |
| `token` | token del tunnel Cloudflare, nel secret di Podman |
| `tunnel-off` | spegne il tunnel e ne rimuove l'unit |
| `certificato` | certificato HTTPS per la LAN |
| `avvia` | avvia il pod (unico punto di avvio) |
| `stato` | stato di unit, container, secret, certificato, porta LAN 8443 |
| `aggiorna` | ricostruisce l'immagine e riallinea le unit |
| `disinstalla` | rimuove le unit (i dati restano) |

## Prima installazione

1. `sudo bash deploy/podman/configura.sh installa` - crea l'utente, costruisce `localhost/custode:latest`, installa i Quadlet e il wrapper `/usr/local/bin/custode`, rimuove l'eventuale `/etc/sysctl.d/90-custode-porta-443.conf` delle versioni precedenti, apre il firewall (`firewall-cmd --permanent --add-port=8443/tcp`).
2. `sudo bash deploy/podman/configura.sh nuovo` - stato vuoto nel volume: schema, utente amministratore predefinito, `config.local.json` con una `encryption_key` nuova.
3. `sudo bash deploy/podman/configura.sh certificato` - vedi la sezione sul certificato.
4. `sudo bash deploy/podman/configura.sh token` - vedi la sezione sulla migrazione per il token; si salta se l'accesso è solo dalla LAN.
5. `sudo bash deploy/podman/configura.sh avvia`. Il primo avvio attende che `custode-web` risulti sano e può richiedere circa 30 secondi (il limite di attesa dell'unit lo prevede): è normale.
6. `sudo custode crea_superadmin.py` per un'installazione multi-struttura (`seed.py` crea solo un admin).
7. Controllo: `sudo bash deploy/podman/configura.sh stato` e, da un PC della LAN, `curl -sk https://NOME-DEL-SERVER:8443/health/ready` con risposta `{"status":"ok"}`.
8. Cambiare subito la password di `admin@medinventory.local`.

## Migrazione da un'installazione esistente

Si copiano `data/`, `uploads/`, `backups/`, `config.json` e `config.local.json` della vecchia installazione nel volume.

> **Attenzione.** `config.local.json` contiene `encryption_key`: senza quella chiave le password IMAP e SMTP cifrate nel database non si decifrano più. Va copiato lo stesso file, non uno nuovo, e va trattato come un segreto: niente copie in cartelle condivise o in posta.

Oltre alla copia, `importa` esegue `deploy/podman/config_container.py adegua`, che porta `host` a `127.0.0.1`, la porta a `5000` e i percorsi a quelli relativi del container. Esce con 1, senza scrivere nulla, se trova un percorso che non può essere portato (per esempio un `database_path` assoluto fuori dall'installazione): in quel caso si corregge il file nella vecchia installazione e si ripete.

L'ordine che segue è obbligatorio.

1. Preparare il nuovo ambiente senza avviarlo:

   ```bash
   sudo bash deploy/podman/configura.sh installa
   ```

2. Fermare la vecchia applicazione **prima** di importare (è l'unit creata da `install_service.sh`):

   ```bash
   sudo systemctl stop medinventory.service
   ```

   Una copia fatta con l'applicazione accesa può prendere un database a metà scrittura e perde ciò che viene inserito dopo.

3. Importare:

   ```bash
   sudo bash deploy/podman/configura.sh importa /percorso/della/vecchia/installazione
   ```

4. Individuare il cloudflared giusto. Su questo host girano **due** cloudflared, uno di Custode e uno di mCorsi (che serve la porta 5100). Fermare quello sbagliato spegne mCorsi, che non va toccato. Per distinguerli senza mostrare i token:

   ```bash
   ps -eo pid,user,unit,args | grep [c]loudflared | sed 's/--token [^ ]*/--token ***/'
   ```

   La colonna `unit` dice quale unit systemd governa ciascun processo; quello di mCorsi si riconosce dall'utente e dall'unit di mCorsi e non va toccato.

5. Mettere il token nel secret:

   ```bash
   sudo bash deploy/podman/configura.sh token
   ```

   Il token si prende dalla dashboard (Zero Trust -> Networks -> Tunnels -> il tunnel di Custode -> Configure) e si incolla quando richiesto: l'input è nascosto; serve la sola stringa che comincia con `eyJ...`. Il token non si passa mai sulla riga di comando, non si scrive in un file e non si incolla in una chat o in un ticket. **Non usare "Refresh token"** nella dashboard: invalida il token in uso e il vecchio servizio smette di funzionare prima che il nuovo sia pronto, togliendo la via del rollback.

6. Spegnere in modo definitivo il vecchio cloudflared di Custode e la vecchia applicazione:

   ```bash
   sudo systemctl disable --now <unit cloudflared di Custode trovata al passo 4>
   sudo systemctl disable --now medinventory.service
   ```

   Lo stesso token attivo in due posti non dà un errore ma **502 intermittenti**, perché Cloudflare distribuisce le richieste tra i due connettori e uno dei due punta a un'applicazione ferma. Il vecchio cloudflared va quindi spento prima di avviare il pod, non dopo.

7. Avviare:

   ```bash
   sudo bash deploy/podman/configura.sh avvia
   ```

8. Controlli:

   ```bash
   sudo bash deploy/podman/configura.sh stato
   sudo custode manutenzione diagnosi
   sudo custode cloudflare_mode.py --test --hostname NOME-PUBBLICO
   ```

   Ripetere poi il comando `ps` del passo 4 e verificare che restino il cloudflared di mCorsi e quello del container, e nessun altro.

La vecchia cartella non viene modificata né cancellata: resta la base del rollback finché non si decide di archiviarla.

## Rollback

1. Fermare il pod (ferma web, lan e tunnel insieme):

   ```bash
   sudo systemctl --user -M custode@ stop custode-pod.service
   ```

2. Verificare con il comando `ps` della migrazione che il cloudflared del container non ci sia più.
3. Riaccendere la vecchia installazione:

   ```bash
   sudo systemctl enable --now medinventory.service
   sudo systemctl enable --now <unit cloudflared di Custode>
   ```

Vale la stessa regola del token: mai i due cloudflared insieme. I dati inseriti nel container dopo la migrazione non tornano da soli nella vecchia installazione: vanno riportati a mano oppure si accetta di perderli. Per questo il rollback si decide presto.

## Certificato per la LAN e CA interna

```bash
sudo bash deploy/podman/configura.sh certificato
```

Le strade sono due: firma con la CA interna oppure installazione di un certificato già emesso. Certificato e chiave del server stanno in `~custode/lan/certs/custode.crt` e `~custode/lan/certs/custode.key`; la configurazione di nginx in `~custode/lan/conf.d`.

> **Attenzione.** La chiave della CA, `ca.key`, resta in `/etc/pki/minventory-ca` e non esce da lì: non si copia in `~custode/lan/certs`, non si monta in nessun container e non compare in nessun comando `podman`. Chi ha quella chiave può emettere certificati creduti da tutti i PC dello studio.

Sui client si installa la sola CA pubblica, `ca.crt`:

- Windows, da prompt amministratore: `certutil -addstore -f Root ca.crt`
- Fedora: `sudo cp ca.crt /etc/pki/ca-trust/source/anchors/minventory-ca.crt && sudo update-ca-trust`
- Firefox usa un archivio proprio e richiede l'importazione dalle impostazioni.

Rinnovo: ripetere `certificato`, poi riavviare il container nginx:

```bash
sudo systemctl --user -M custode@ restart custode-lan.service
```

Nota: `custode-lan` e `custode-tunnel` dipendono da `custode-web` (`Requires=`). Riavviare `custode-web.service` riavvia quindi anche loro, e il tunnel resta giù per qualche secondo; riavviare `custode-lan.service` da solo non tocca gli altri.

## La porta 8443

Sull'host il pod pubblica la `8443` (`PublishPort=8443:443`): i client aprono `https://NOME-DEL-SERVER:8443`. Dentro il pod nginx resta sulla 443. La 8443 è sopra la soglia delle porte privilegiate, quindi un pod rootless la pubblica senza modificare il kernel: niente `sysctl`, niente porte 443-1023 aperte a tutti gli utenti dell'host (il server è condiviso con altre applicazioni).

Le installazioni precedenti avevano abbassato la soglia a 443: `installa` rimuove `/etc/sysctl.d/90-custode-porta-443.conf` (solo quel file) e riporta subito `net.ipv4.ip_unprivileged_port_start` a 1024. Il servizio firewall `https`, se presente, non viene toccato: `installa` avvisa e, se lo aveva aggiunto Custode, si toglie con `sudo firewall-cmd --permanent --remove-service=https && sudo firewall-cmd --reload`.

Verifica: `sysctl net.ipv4.ip_unprivileged_port_start` deve rispondere `1024`. Firewall: `sudo firewall-cmd --list-ports` deve contenere `8443/tcp`.

## Manutenzione: il comando custode

Il wrapper `/usr/local/bin/custode` esegue nel container le CLI di manutenzione. Script ammessi: `manutenzione.py`, `migrate.py`, `toggle_modalita.py`, `pulisci_uploads.py`, `importa_installazione.py`, `crea_superadmin.py`, `seed.py`, `cloudflare_mode.py`, `recover_import_history.py`. Con `esterna` sono ammessi solo `importa_installazione.py` e `manutenzione.py`. Le quattro forme:

```bash
sudo custode crea_superadmin.py                           # script, applicazione accesa
sudo custode manutenzione diagnosi                        # abbreviazione di manutenzione.py
sudo custode fermo migrate.py                             # ferma l'applicazione, esegue, la riavvia
sudo custode esterna /srv/vecchio-medinventory manutenzione.py stato   # lavora su un'altra installazione
```

- `fermo` serve a tutto ciò che vuole il database per sé (migrazioni, ripristini); il pod riparte sempre al termine.
- Dopo `toggle_modalita.py` e dopo `cloudflare_mode.py --on` / `--off` l'applicazione va riavviata: `sudo systemctl --user -M custode@ restart custode-web.service` (riavvia anche lan e tunnel, vedi sopra).
- `esterna` monta la cartella indicata in sola lettura e senza rietichettarla per SELinux (`--security-opt label=disable`, nessun `:Z`), quindi la cartella resta utilizzabile dal suo proprietario.
- `esterna` è di sola lettura anche per il database della vecchia installazione: funzionano solo i comandi che leggono (`manutenzione.py stato`, `diagnosi`, `utenti elenca`, `importa_installazione.py --dry-run`). Le azioni di scrittura del menu (`migra`, `utenti azzera`) falliscono con "attempt to write a readonly database": vanno eseguite sull'installazione di Custode, non su quella esterna.
- Il timer di diagnosi non riaccende l'applicazione durante un `fermo`.

## Log

```bash
sudo journalctl CONTAINER_NAME=custode-web -n 100 --no-pager
sudo journalctl CONTAINER_NAME=custode-lan -n 100 --no-pager
sudo journalctl CONTAINER_NAME=custode-tunnel -n 100 --no-pager
sudo journalctl CONTAINER_NAME=custode-web -f
```

Non si usa `journalctl --user`: l'utente `custode` non ha una shell e da root `journalctl --user -M custode@` risponde "Connecting to a machine as non-root is not supported". I container scrivono nel journal di sistema, dove si filtrano per nome. Lo stato delle unit invece si legge con:

```bash
sudo systemctl --user -M custode@ status custode-pod.service custode-web.service custode-lan.service custode-tunnel.service --no-pager
```

## Backup e ripristino

I backup automatici e manuali dell'applicazione finiscono in `/var/backups/custode` (0750, proprietario `custode`), sull'host e non nel volume, montata in scrittura dal solo `custode-web`. Non montare quella cartella con `:Z` da un altro container e non lanciarvi `chcon` o `restorecon` a mano mentre l'applicazione è accesa, perché le si toglie l'accesso.

La cartella va inclusa nel backup dell'host verso un altro disco: un backup che resta sullo stesso server non protegge dalla perdita del server.

Ripristino: dalla pagina di amministrazione come descritto in `DOCUMENTAZIONE.md` (sezione "Backup e ripristino"), oppure con gli strumenti a riga di comando attraverso `sudo custode fermo ...`. L'archivio di ripristino completo contiene database, allegati e configurazione con `encryption_key`: va conservato come un segreto.

## Aggiornamento

```bash
git pull
sudo bash deploy/podman/configura.sh aggiorna
```

`aggiorna` ricostruisce l'immagine e riavvia il pod; volume, backup, secret e certificati non vengono toccati; le migrazioni dello schema girano all'avvio di `custode-web`. Controllo:

```bash
sudo bash deploy/podman/configura.sh stato
sudo custode manutenzione diagnosi
```

## Disinstallazione

```bash
sudo bash deploy/podman/configura.sh disinstalla
```

Rimuove le unit e i Quadlet, il timer, il linger dell'utente e il wrapper `/usr/local/bin/custode`. Conserva dati, backup, secret del token, certificati, la CA e l'utente `custode`. Al termine lo script stampa i comandi, uno per riga, per cancellare anche i dati.

Il volume `custode-stato` e `/var/backups/custode` contengono dati sanitari dell'inventario e non vanno cancellati senza un backup verificato.

La regola firewall `8443/tcp` resta: per toglierla `sudo firewall-cmd --permanent --remove-port=8443/tcp && sudo firewall-cmd --reload`.

## Verifiche dopo l'installazione

Oltre ai controlli indicati nei singoli passi, dopo la prima installazione e dopo ogni cambio importante conviene fare a mano le verifiche che la suite di test non può provare, perché dipendono dall'host reale:

- rete del pod: `custode-lan` raggiunge `custode-web` su `127.0.0.1:5000`, e la 5000 non risponde dall'esterno;
- riavvio reale: dopo un riavvio dell'host il pod riparte da solo (linger) e `stato` è tutto verde;
- etichette SELinux: nessun rifiuto AVC su volume, backup e certificati (`sudo ausearch -m avc -ts recent`);
- raggiungibilità del tunnel: l'indirizzo pubblico risponde e `sudo custode cloudflare_mode.py --test --hostname NOME-PUBBLICO` è positivo.
