"""Validazione degli URL che il server contatta per conto di un utente.

L'admin di una struttura configura l'indirizzo del server AI locale (Ollama,
LM Studio, endpoint OpenAI-compatibile) e il server va a interrogarlo. E' una
richiesta HTTP che parte da dentro la rete, decisa da chi amministra un solo
tenant: senza controlli diventa una sonda verso localhost, verso gli altri
servizi della LAN e verso gli indirizzi di metadata delle installazioni in
cloud (169.254.169.254 e simili), con la risposta riportata in pagina.

La difesa e' su tre livelli, dal piu' permissivo al piu' stretto:

1. Sempre: solo http/https, niente credenziali nell'URL, e nessuno degli
   indirizzi risolti puo' essere link-local, multicast, riservato o non
   specificato. Loopback e reti private restano ammessi: e' li' che vive il
   server AI di un'installazione LAN, che e' il motivo per cui la funzione
   esiste.
2. Sempre: porte sotto la 1024 rifiutate tranne 80 e 443. Sono le porte dei
   servizi di sistema (SSH, SMB, SMTP...), non quelle di un server AI, che
   sta sulla 11434, 1234, 8080 o 8000.
3. Se l'operatore compila `ai_local_url_allowlist` in config.local.json, passa
   soltanto quello che vi compare. E' l'unico modo per chiudere davvero la LAN
   e va usato dove il tenant non e' fidato.

La risoluzione DNS viene fatta qui e controllata riga per riga, ma la
connessione la apre httpx, che risolve di nuovo: fra i due momenti un nome
sotto controllo altrui puo' cambiare indirizzo (DNS rebinding). Chiuderlo
del tutto vuole un transport httpx che si colleghi all'IP gia' verificato;
per ora la validazione si ripete a ogni chiamata uscente, il che riduce la
finestra ma non la elimina. Un'allowlist di soli indirizzi IP non e'
attaccabile in quel modo.

Il modulo non importa Flask: i test lo usano direttamente e la stessa
funzione serve dentro e fuori da una richiesta.
"""
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

# Porte sotto la 1024 comunque ammesse: un server AI dietro un reverse proxy
# risponde su queste due.
PORTE_PRIVILEGIATE_AMMESSE = frozenset({80, 443})

# Reti che nessun server AI puo' legittimamente occupare e che sono invece i
# bersagli tipici di una SSRF. Loopback e reti private non sono qui: in una
# installazione LAN sono l'uso normale.
RETI_VIETATE = (
    ipaddress.ip_network('0.0.0.0/8'),          # "questa rete"
    ipaddress.ip_network('169.254.0.0/16'),     # link-local, contiene i metadata cloud
    ipaddress.ip_network('224.0.0.0/4'),        # multicast
    ipaddress.ip_network('240.0.0.0/4'),        # riservata
    ipaddress.ip_network('255.255.255.255/32'),  # broadcast
    ipaddress.ip_network('::/128'),             # non specificato
    ipaddress.ip_network('fe80::/10'),          # link-local IPv6
    ipaddress.ip_network('fec0::/10'),          # site-local IPv6, deprecata
    ipaddress.ip_network('ff00::/8'),           # multicast IPv6
)


def _normalizza_ip(indirizzo):
    """IPv4 nascosto in un IPv6 mappato (::ffff:169.254.169.254) torna IPv4."""
    ip = ipaddress.ip_address(indirizzo)
    if ip.version == 6 and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _ip_vietato(ip):
    return any(ip in rete for rete in RETI_VIETATE if ip.version == rete.version)


def _risolvi(host, porta):
    """Indirizzi IP di un host. Un IP scritto per esteso non passa dal DNS."""
    try:
        return [_normalizza_ip(host)]
    except ValueError:
        pass
    try:
        info = socket.getaddrinfo(host, porta, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("Nome host non risolvibile: %s" % host)
    indirizzi = []
    for famiglia, _tipo, _proto, _nome, indirizzo in info:
        try:
            ip = _normalizza_ip(indirizzo[0])
        except ValueError:
            continue
        if ip not in indirizzi:
            indirizzi.append(ip)
    if not indirizzi:
        raise ValueError("Nome host non risolvibile: %s" % host)
    return indirizzi


def leggi_allowlist(config):
    """Estrae `ai_local_url_allowlist` dalla configurazione globale.

    Accetta una lista o una stringa con voci separate da virgole o spazi, cosi'
    che l'operatore possa scriverla in entrambi i modi in config.local.json.
    La lista sta solo nella configurazione di sistema: un admin di struttura
    non deve poter allargare il perimetro che lo limita.
    """
    if not config:
        return []
    voci = config.get('ai_local_url_allowlist') or []
    if isinstance(voci, str):
        voci = voci.replace(',', ' ').split()
    return [str(v).strip() for v in voci if str(v).strip()]


def _analizza_voce(voce):
    """Scompone una voce dell'allowlist in (host o rete, porta o None).

    Formati ammessi: `host`, `host:porta`, `1.2.3.0/24`, `1.2.3.0/24:porta`,
    `[::1]`, `[::1]:porta`.
    """
    resto, porta = voce, None
    if resto.startswith('['):
        chiusa = resto.find(']')
        if chiusa == -1:
            raise ValueError("Voce di allowlist non valida: %s" % voce)
        indirizzo = resto[1:chiusa]
        coda = resto[chiusa + 1:]
        if coda.startswith(':'):
            porta = coda[1:]
        resto = indirizzo
    elif resto.count(':') == 1:
        resto, porta = resto.split(':', 1)
    if porta is not None:
        try:
            porta = int(porta)
        except ValueError:
            raise ValueError("Porta non valida nell'allowlist: %s" % voce)
    try:
        return ipaddress.ip_network(resto, strict=False), porta
    except ValueError:
        return resto.lower(), porta


def _voce_soddisfatta(voce, host, indirizzi, porta):
    """Una voce vale se il nome coincide, o se *tutti* gli indirizzi risolti
    stanno nella rete indicata: bastasse uno, un nome con piu' record A
    porterebbe la connessione dove vuole chi controlla il DNS."""
    bersaglio, porta_voce = _analizza_voce(voce)
    if porta_voce is not None and porta_voce != porta:
        return False
    if isinstance(bersaglio, str):
        return bersaglio == host.lower()
    return all(ip.version == bersaglio.version and ip in bersaglio for ip in indirizzi)


def valida_url_ai_locale(url, allowlist=None):
    """Verifica un base URL configurato dall'utente e lo restituisce normalizzato.

    Solleva ValueError con un messaggio leggibile in interfaccia: e' il testo
    che l'admin vede quando prova la connessione al server AI.
    """
    if not url or not str(url).strip():
        raise ValueError("URL del server AI non configurato.")

    pezzi = urlsplit(str(url).strip())
    if pezzi.scheme not in ('http', 'https'):
        raise ValueError(
            "L'indirizzo del server AI deve iniziare per http:// o https:// (ricevuto: %s)."
            % (pezzi.scheme or 'nessuno schema'))
    if pezzi.username or pezzi.password:
        raise ValueError("L'indirizzo del server AI non puo' contenere credenziali.")

    host = pezzi.hostname
    if not host:
        raise ValueError("L'indirizzo del server AI non contiene un nome host.")

    try:
        porta = pezzi.port
    except ValueError:
        raise ValueError("Porta non valida nell'indirizzo del server AI.")
    if porta is None:
        porta = 443 if pezzi.scheme == 'https' else 80

    indirizzi = _risolvi(host, porta)
    for ip in indirizzi:
        if _ip_vietato(ip):
            raise ValueError(
                "Indirizzo non ammesso per il server AI: %s risolve su %s, che appartiene "
                "a una rete di sistema (link-local, multicast o riservata)." % (host, ip))

    voci = leggi_allowlist(allowlist) if isinstance(allowlist, dict) else (allowlist or [])
    if voci:
        if not any(_voce_soddisfatta(v, host, indirizzi, porta) for v in voci):
            raise ValueError(
                "Indirizzo non ammesso per il server AI: %s non compare in "
                "ai_local_url_allowlist." % url)
    elif porta not in PORTE_PRIVILEGIATE_AMMESSE and porta < 1024:
        raise ValueError(
            "Porta non ammessa per il server AI: %d. Sotto la 1024 sono consentite solo "
            "la 80 e la 443; per un'altra porta di sistema serve una voce esplicita in "
            "ai_local_url_allowlist." % porta)

    netloc = pezzi.netloc
    percorso = pezzi.path.rstrip('/')
    return urlunsplit((pezzi.scheme, netloc, percorso, '', ''))
