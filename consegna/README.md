# KINE — Sistema di mira assistita basato su computer vision

Progetto per *Ingegneria del Software / Intelligenza Artificiale Applicata*.

Due PC in LAN. Sul primo gira il gioco; il programma ne cattura lo schermo e
manda i frame in UDP. Il secondo li analizza — rileva i bersagli, li insegue
con un filtro di Kalman, ne stima la velocità — e risponde dicendo dove sta il
bersaglio più vicino a dove l'utente sta puntando.

**L'utente gioca normalmente e resta sempre al comando.** Il programma non
muove il puntatore, non preme il grilletto, non scrive in altri processi.

## Il contratto, in una tabella

| | chi decide | limite |
|---|---|---|
| **«guarda lì»** (punto suggerito, mostrato mentre miri) | l'AI informa, tu guardi | nessuno: un'informazione troncata sarebbe sbagliata |
| **correzione applicata al colpo** | l'AI agisce, ma solo se premi tu | 6–8 px, imposto dalla validazione della config |
| **quando sparare** | solo tu | — |
| **dove puntare** | solo tu | — |

La separazione è il punto del progetto. La parte potente — dire dove guardare —
non ti vincola; la parte che ti vincola — spostare il colpo — è deliberatamente
troppo piccola per sostituire la tua mira. `config.py` rifiuta all'avvio una
`max_correction_px` sopra 32 e un orizzonte di previsione sopra 1 s: i limiti
sono verificati dal programma, non affidati alle buone intenzioni.

## Le due configurazioni

| file | sorgente | a cosa serve |
|---|---|---|
| `config.gioco.json` | schermo (`screen`), detection HSV, mirino fisso al centro | **la consegna**: il sistema che gira mentre giochi |
| `config.json` | poligono sintetico, detection BGR, mirino = mouse | **il banco di prova**: gira su qualsiasi macchina, senza gioco e senza schermo da catturare |

Il poligono non è il progetto: è lo strumento di misura. Genera i bersagli
invece di osservarli, e proprio per questo conosce la verità di riferimento —
dove sta ogni bersaglio, a che velocità, quale l'utente stava puntando — che su
video vero non esiste. È l'unico posto in cui l'errore dell'assist si può
**misurare** invece che descrivere.

## Esecuzione

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Su due PC nella stessa LAN (la consegna)

Sul **PC del gioco** (nodo A), supponendo che il PC dell'AI sia `192.168.1.20`:

```bash
python3 main.py --config config.gioco.json --role capture --peer 192.168.1.20
```

Sul **PC dell'AI** (nodo B):

```bash
python3 main.py --config config.gioco.json --role ai --bind 0.0.0.0
```

Prima di lanciare, in `config.gioco.json`:

- `capture.region` = `[left, top, width, height]` della finestra del gioco
  (oppure togli la chiave per prendere il monitor intero);
- `detection.colors[].bgr` = il colore del bersaglio da seguire;
- `network.peer_ip` = l'IP dell'altro PC, se non usi `--peer`.

Se non arriva niente: le porte UDP 5555 e 5565 devono passare il firewall su
entrambe le macchine (su Windows la prima esecuzione apre di norma una
richiesta; su Linux `sudo ufw allow 5555/udp` e `5565/udp`).

### Su un PC solo

```bash
python3 main.py                                       # poligono, due nodi su loopback
python3 main.py --source video --video partita.mp4 --detect hsv
python3 main.py --config config.gioco.json --role both
```

`--role both` **non** è una scorciatoia che salta la rete: avvia il nodo B in un
thread e lo fa dialogare su 127.0.0.1 con lo stesso identico protocollo usato
fra due macchine. La modalità distribuita viene esercitata a ogni esecuzione,
invece di restare codice mai percorso.

### Comandi

| | |
|---|---|
| `Spazio` | colpo assistito |
| `P` | colpo puro (nessuna correzione) — serve per il confronto |
| click / Shift+click | assistito / puro, con `aim_mode: mouse` |
| `R` | azzera le metriche |
| `Esc` | esci |

## Le sorgenti video

| `capture.source` | cosa legge | quando usarla |
|---|---|---|
| `screen` | una regione dello schermo, via `mss` | la consegna: il gioco gira sul nodo A |
| `video` | una clip registrata | dimostrazione **riproducibile**: lo stesso file dà lo stesso risultato su qualunque macchina, anche senza il gioco installato |
| `webcam` | la telecamera | prove su oggetti reali |
| `synthetic` | il poligono generato dal programma | test e misure |

La pipeline a valle è la stessa per tutte e quattro: cambia solo da dove
arrivano i pixel.

### `aim_mode`: dove sta il mirino

- **`center`** — in uno sparatutto in prima persona il mirino sta **fermo al
  centro** dello schermo ed è il mondo a ruotare sotto. Questo elimina un
  problema intero: non serve leggere il puntatore del sistema operativo né
  agganciarsi al processo del gioco. L'AI dice «il bersaglio è 12 px alla tua
  destra» e sei tu a ruotare.
- **`mouse`** — il puntatore sopra la finestra. Giochi a camera fissa, visuale
  dall'alto, e il poligono.

### `detection.mode`: come si riconoscono i bersagli

| modo | come | limite |
|---|---|---|
| `bgr` | cubo attorno al colore di riferimento | esatto su tinte piatte, **si rompe in ombra**: al 50% di luminosità perde tutti i bersagli |
| `hsv` | soglia stretta sulla tinta + minimi su saturazione e luminosità | è il default per video vero: l'ombra abbassa V e lascia H dov'era |
| `motion` | sottrazione dello sfondo (MOG2) | nessuna palette, ma **solo a inquadratura ferma**: appena ruoti, l'intero fotogramma si muove e la maschera si accende ovunque |

La differenza fra `bgr` e `hsv` non è un'opinione, è misurata in
`tests/test_detection.py::test_in_ombra_la_soglia_bgr_perde_i_bersagli_e_hsv_no`:
sulla stessa scena al 50% di luminosità `bgr` restituisce **0** bersagli su 3,
`hsv` li trova tutti e tre.

## La previsione è moderata, e il codice lo impone

La consegna chiede un anticipo leggero, non un calcolo che indovina dove sarà
il nemico fra due secondi. Tre meccanismi, in ordine di forza:

1. `assist.max_lead_ms` (120 ms di default) taglia l'orizzonte **totale**:
   qualunque latenza di rete venga misurata, e qualunque tempo di volo risolva
   l'intercetta, il punto suggerito non va oltre quel tetto.
2. `assist.use_intercept` è **spento** di default. Risolvere l'intercetta
   balistica ha senso solo quando il colpo *viaggia*; con un'arma hitscan il
   colpo arriva nel frame stesso e non c'è niente da anticipare.
3. `config.py` **rifiuta** `max_lead_ms > 1000` e avvisa sopra 250 ms.

Verificato in `tests/test_assist.py`: anche con un bersaglio che scappa quasi
alla velocità del proiettile, e con 5 secondi di latenza simulata, lo
spostamento del punto suggerito resta sotto `max_lead_ms × velocità`.

## Misure

```bash
python3 bench.py --shots 300 --aim-error 18
python3 -m pytest tests/ -q
```

`bench.py` esegue la pipeline a passo fisso, senza finestra e con seed fisso,
con un «utente» simulato che sbaglia la mira di una quantità nota. Tre
condizioni a confronto:

```
300 colpi per condizione | errore di mira sigma = 18.0 px | seed 7 | tetto orizzonte 600 ms

condizione                  colpi   a segno  mancato medio    mediana  corr. media
assist OFF                    300     26.7%         20.08p     13.02p        0.00p
assist ON (solo nudge)        300     28.0%         16.43p      8.88p        7.77p
assist ON + segui il sugg.    300     75.3%          4.96p      0.00p        7.77p
```

La lettura: la sola correzione automatica vale poco, **ed è voluto** — 8 px non
possono compensare i ~50 px che il bersaglio percorre mentre il marcatore vola.
Il valore sta nell'**informazione**: seguire il punto suggerito porta i colpi a
segno dal 27% al 75%.

Il banco ha un proiettile lento (380 px/s), quindi `bench.py` accende
l'intercetta e alza l'orizzonte a 600 ms. È l'eccezione documentata al default
hitscan, dichiarata nel codice, nell'intestazione del bench e nel log.

### Quanto costa il vincolo della consegna

`--lead-cap` misura il prezzo del tetto sull'anticipo, invece di lasciarlo
implicito:

| tetto orizzonte | a segno | mancato medio |
|---|---|---|
| 600 ms | 75.3% | 4.96 px |
| 1000 ms | 80.7% | 5.60 px |

Cinque punti di colpi a segno in più, pagati con un errore medio **peggiore**:
quando l'estrapolazione a orizzonte lungo sbaglia, sbaglia di molto. È il
numero da discutere nella relazione, non da nascondere — e mostra che il
vincolo della consegna non è solo etico, ma anche tecnicamente difendibile.

**Distanza di mancato** (`miss_px`): 0 se il colpo va a segno, altrimenti di
quanto il marcatore ha sfiorato la superficie del bersaglio *preso di mira*.
Non è la distanza alla quale scatta la collisione: quella vale circa il raggio
con qualunque mira, e non misura niente.

## Moduli

| File | Ruolo |
|---|---|
| `sources.py` | **Sorgenti video**: schermo, clip, webcam, poligono — e la conversione schermo ↔ frame |
| `config.py` | Config tipizzata e validata: un errore di battitura si ferma all'avvio |
| `detection.py` | Maschere BGR / HSV / movimento, mutuamente esclusive, blob, centroidi |
| `prediction.py` | Kalman 2D a velocità costante (forma di Joseph), associazione globale, id persistenti |
| `assist.py` | Gate sul puntamento + clamp sulla correzione + tetto sull'anticipo |
| `intercept.py` | Intercetta cinematica: dove sarà il bersaglio quando il proiettile arriva |
| `network.py` | Datagrammi a chunk, riassemblaggio limitato, validazione dell'input |
| `input_control.py` | Applica dx, dy al punto di sparo. Unico punto in cui una correzione diventa effettiva |
| `capture.py` | Poligono sintetico e registro dei colpi (banco di misura) |
| `bench.py` | Esperimento riproducibile assist on/off |
| `tests/` | 98 test — `python3 -m pytest tests/ -q` |

## Protocollo

```
A -> B  frame    header !IHHd (frame_id, indice, totale, capture_ts) + JPEG
A -> B  comando  {"type":"aim", "x","y","sentAt","rttMs"}
B -> A  comando  {"type":"hint", "suggested","dx","dy","trackId","nTracks","sentAt","echo"}
A -> B  comando  {"type":"fire_request", "x","y","useAssist","seq","sentAt","rttMs"}
B -> A  comando  {"type":"fire", "x","y","userX","userY","dx","dy",
                  "trackId","assist","seq","sentAt","echo","suggested"}
```

`hint` è l'assistenza **al momento della mira**: mentre punti, il nodo B dice
dove sta il bersaglio più probabile. Non tocca niente, si disegna e basta.
`fire` è l'unico messaggio che porta una correzione applicata, e nasce solo da
un grilletto premuto da te.

Tre dettagli che non sono cosmetici:

- **`capture_ts` nell'header del frame.** Il nodo B data le misure con
  l'istante di cattura, non di arrivo. Usando l'ora di arrivo, il jitter di
  rete entrerebbe nel `dt` del filtro di Kalman e ne uscirebbe come rumore
  sulla velocità stimata — cioè proprio sulla grandezza da cui dipende
  l'anticipo.
- **`echo` riporta sempre l'istante letto sull'orologio di chi ha fatto la
  richiesta.** Fra due macchine gli orologi non sono sincronizzati: un RTT
  calcolato come differenza fra letture di clock diversi non vorrebbe dire
  niente. Con `echo`, l'RTT è la differenza fra due letture dello stesso clock.
- **`chunk_bytes` = 1400, non 12000.** Sotto l'MTU Ethernet il datagramma
  viaggia in un solo pacchetto IP. A 12000 byte l'IP frammenta in ~9 pezzi e
  basta perderne uno perché il kernel scarti l'intero datagramma: la perdita
  effettiva per chunk diventa circa nove volte quella di rete.

Il canale comandi è JSON in chiaro, senza autenticazione: va bene per una demo
in laboratorio, non per una rete aperta. Per questo ogni messaggio in ingresso
viene validato e nessun payload ricevuto può far cadere un nodo.

## Limiti dichiarati

- **Detection cromatica.** Funziona quando il bersaglio ha un colore
  distintivo. Su un nemico mimetizzato serve un riconoscitore semantico: il
  punto di innesto è `detection.py`, basta che produca oggetti `Detection` e il
  resto della pipeline non cambia.
- **`motion` non regge la camera in movimento.** Il rimedio corretto sarebbe
  stimare l'omografia fra frame consecutivi e compensare il moto di fondo prima
  della sottrazione. È fuori dallo scopo di questa consegna, e va detto invece
  che nascosto.
- **Modello a velocità costante.** Un bersaglio che cambia direzione bruscamente
  viene inseguito con un ritardo pari a qualche frame. È il motivo per cui
  l'orizzonte è di 120 ms e non di un secondo.
- **Nessuna verità di riferimento su video vero.** Le percentuali di colpi a
  segno si possono misurare solo sul poligono. Su schermo il sistema si osserva,
  non si misura.

## Cosa questo programma non fa

Non inietta input nel sistema operativo, non muove il puntatore, non legge la
memoria di altri processi e non si aggancia al processo del gioco. Legge i
pixel del desktop — gli stessi che l'utente sta guardando — e disegna un
suggerimento in una finestra propria. Senza un tasto premuto dall'utente non
produce nessun comando di sparo.

È pensato per il bersaglio incluso, per una clip registrata o per un gioco
locale in singolo: usarlo in un multigiocatore online violerebbe le condizioni
d'uso di quel gioco, indipendentemente da quanto sia piccola la correzione.
