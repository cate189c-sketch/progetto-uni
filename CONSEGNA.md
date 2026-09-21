# Corrispondenza fra la consegna e il codice

Consegna: *«Sistema di Mira Assistita (Aim-Assist) per Giochi Video basati su
Computer Vision»* — Ingegneria del Software / Intelligenza Artificiale
Applicata, prof. D'Andrea.

Questo documento serve a una cosa sola: per ogni competenza richiesta, dire
**dove sta nel codice** e **quale test lo dimostra**. Le affermazioni senza una
riga di codice o un test accanto non contano.

---

## Le cinque competenze richieste

### 1. Acquisizione ed elaborazione in tempo reale di video

| | |
|---|---|
| **Dove** | `consegna/sources.py` — `ScreenSource` (cattura dello schermo via `mss`), `VideoFileSource`, `WebcamSource`, `SyntheticSource` |
| **Configurazione** | `config.gioco.json`: `capture.source = "screen"`, `capture.region = [left, top, w, h]` |
| **In tempo reale** | `NodeA.run()` in `main.py` gira a `capture.fps` con un accumulatore che si riallinea quando è in ritardo, invece di accumulare deriva |
| **Test** | `tests/test_sources.py` (9 test), `tests/test_integrazione.py::test_pipeline_su_video_vero_stima_le_velocita_giuste` |

Il dettaglio che non è ovvio: il frame viene ridimensionato prima di partire in
rete, quindi **le coordinate del frame non sono pixel di schermo**. Una
correzione di 8 px suggerita su un frame 640×360 che viene da uno schermo
1280×720 vale 16 px di schermo. La conversione sta in un posto solo
(`sources._Base.to_frame` / `to_screen`) ed è verificata nei due sensi.

### 2. Rilevamento e tracciamento oggetti con stima della velocità

| | |
|---|---|
| **Rilevamento** | `consegna/detection.py` — maschere BGR, HSV o movimento, rese **mutuamente esclusive** (ogni pixel a una sola classe) |
| **Tracciamento** | `consegna/prediction.py` — filtro di Kalman 2D a velocità costante, forma di Joseph, associazione globale con doppio gate (euclideo + Mahalanobis a 3σ) |
| **Stima della velocità** | lo stato del filtro è `[px, py, vx, vy]ᵀ`: la velocità è stimata, non derivata a differenze finite |
| **Test** | `tests/test_detection.py` (11), `tests/test_prediction.py` (10) |

La prova che la stima è corretta e non solo plausibile: nel test su video vero
la clip muove i bersagli a 4 e 3 px/frame a 24 fps — cioè +96 e −72 px/s — e il
filtro, leggendo frame passati per compressione JPEG, li ricostruisce entro
8 px/s, con la componente verticale sotto 12 px/s su moto puramente
orizzontale.

### 3. Previsione moderata del movimento

La consegna è esplicita: *«non un calcolo esagerato che indovina dove sarà il
nemico tra 2 secondi»*. Tre meccanismi, dal più forte al più debole:

| | |
|---|---|
| **Tetto assoluto** | `assist.max_lead_ms` = 120 ms taglia l'orizzonte **totale**: qualunque latenza si misuri, e qualunque tempo di volo risolva l'intercetta, il punto suggerito non va oltre (`assist.py`, `min(tetto, sol[2] + lead)`) |
| **Intercetta spenta** | `assist.use_intercept = false` di default: ha senso solo con un proiettile che viaggia, non con un'arma hitscan |
| **Validazione** | `config.py` **rifiuta** `max_lead_ms > 1000` e avvisa sopra 250 ms |
| **Test** | `tests/test_assist.py::test_l_orizzonte_di_previsione_e_tagliato_anche_con_l_intercetta` e `..._quando_la_latenza_di_rete_e_enorme`; `tests/test_config.py` respinge `max_lead_ms: 2000` |

Il test del tetto è **discriminante**: rimuovendo il `min(tetto, ...)` da
`assist.py` fallisce. Non è un test che passa comunque.

### 4. Fusione fra input umano e assistenza AI

| | |
|---|---|
| **Dove** | `consegna/assist.py` (calcola), `consegna/input_control.py` (applica), `main.py` (protocollo) |
| **Al momento della mira** | messaggio `hint`: mentre l'utente punta, il nodo B risponde con il punto suggerito. Non applica niente |
| **Al momento dello sparo** | messaggio `fire`: l'unico che porta una correzione applicata, e nasce solo da un tasto premuto dall'utente |
| **Test** | `tests/test_assist.py` (13), `tests/test_integrazione.py::test_il_nodo_b_suggerisce_gia_mentre_l_utente_mira` |

Tre vincoli resi impossibili da violare, non affidati alle buone intenzioni:

1. **il bersaglio si sceglie vicino a dove punta l'utente**, mai il «migliore»
   del frame — `gate_px` attorno alla mira. Senza questo gate il sistema
   sarebbe un aimbot: sceglierebbe un bersaglio dall'altra parte dello schermo;
2. **la correzione è clampata** a `max_correction_px` (6–8 px), e `config.py`
   rifiuta un valore sopra 32;
3. **l'AI non muove il puntatore e non spara**: senza una chiamata a `on_fire()`
   originata da un tasto, `input_control` non produce nulla.

La distinzione portante: il **punto suggerito non è clampato** — è
informazione, e un'informazione troncata sarebbe sbagliata — mentre la
**correzione applicata** lo è. L'AI dice, l'utente decide.

### 5. Comunicazione UDP fra due PC in LAN

| | |
|---|---|
| **Dove** | `consegna/network.py` (trasporto), `main.py` (protocollo, `NodeA` / `NodeB`) |
| **Esecuzione** | `--role capture --peer <ip>` su un PC, `--role ai --bind 0.0.0.0` sull'altro |
| **Test** | `tests/test_network.py` (17), `tests/test_integrazione.py` (7) su socket UDP vere |

`--role both` non è una scorciatoia che salta la rete: avvia il nodo B in un
thread e lo fa parlare su 127.0.0.1 con lo **stesso** protocollo. La modalità
distribuita viene quindi esercitata a ogni esecuzione della demo, invece di
essere codice mai percorso — che è esattamente il difetto della versione
precedente.

Quattro decisioni di rete da poter difendere a voce:

- **`chunk_bytes` = 1400.** Sotto l'MTU Ethernet un datagramma è un pacchetto
  IP. A 12000 byte l'IP frammenta in ~9 pezzi, e basta perderne uno perché il
  kernel scarti l'intero datagramma: la perdita effettiva per chunk diventa
  `1−(1−p)⁹`, circa nove volte quella di rete.
- **`capture_ts` viaggia nell'header del frame.** Il nodo B data le misure con
  l'istante di *cattura*, non di arrivo: altrimenti il jitter di rete entrerebbe
  nel `dt` del Kalman e ne uscirebbe come rumore sulla velocità stimata, cioè
  proprio sulla grandezza da cui dipende l'anticipo.
- **`echo` riporta l'orologio di chi ha chiesto.** Fra due macchine i clock non
  sono sincronizzati: un RTT calcolato incrociando letture di orologi diversi
  non significa niente. Con `echo` è la differenza di due letture dello stesso
  clock.
- **Riassemblaggio limitato.** I frame incompleti stanno in una mappa con tetto
  (`max_pending_frames`) e sfratto del più vecchio. Senza tetto, un mittente che
  perde chunk fa crescere la memoria del ricevente senza limite.
- **B lega la porta comandi nota, A usa una porta effimera.** B è il servizio,
  A è il cliente; B risponde all'indirizzo del mittente. Legando *entrambi* i
  nodi al contrario, A manderebbe i comandi all'unico indirizzo che conosce —
  `port_cmds` — dove non c'è nessuno: fra due PC i comandi finirebbero nel
  vuoto, in locale tornerebbero ad A stesso. Senza errori e senza log.

Quest'ultimo punto è stato trovato **eseguendo** i due nodi come li avvia
`main()`, non leggendo il codice, ed è il motivo per cui esiste
`test_i_due_nodi_montati_come_in_main_si_parlano_davvero`: gli altri test di
integrazione mandano i comandi direttamente alla porta che il nodo B ha
ottenuto di fatto, quindi non avrebbero mai verificato la cosa che conta fra
due macchine — che A sappia **dove** trovare B partendo dalla sola
configurazione. Il test fallisce se si rimette il legame sbagliato.

---

## Le tre decisioni di progetto

### Il mirino sta fermo al centro

In uno sparatutto in prima persona il mirino **non si muove**: è il mondo a
ruotare sotto. Assumerlo (`capture.aim_mode = "center"`) elimina un problema
intero — non serve leggere il puntatore del sistema operativo, non serve
agganciarsi al processo del gioco. L'AI dice «il bersaglio è 12 px alla tua
destra» e l'utente ruota.

È anche ciò che rende il vincolo della consegna realizzabile *per costruzione*:
se il programma non sa nemmeno dove sia il puntatore, non può muoverlo.

### HSV invece di BGR su video vero

Una soglia in BGR è un cubo attorno al colore. Un bersaglio in ombra ha gli
stessi *rapporti* fra i canali ma valori molto più bassi: esce dal cubo e
sparisce. In HSV i tre assi si separano e ognuno prende la tolleranza che
merita — stretta sulla tinta, larghissima su saturazione e luminosità.

Non è un'opinione, è misurato: sulla stessa scena al 50% di luminosità, `bgr`
trova **0 bersagli su 3** e `hsv` li trova **tutti e tre**
(`tests/test_detection.py::test_in_ombra_la_soglia_bgr_perde_i_bersagli_e_hsv_no`).

### Il poligono resta, ma come strumento di misura

Il poligono sintetico genera i bersagli invece di osservarli, quindi **non**
soddisfa il requisito 1 — e infatti non è la configurazione della consegna. Ma
conosce la verità di riferimento (dove sta ogni bersaglio, a che velocità,
quale l'utente stava puntando), e su video vero quella verità non esiste. È
l'unico posto in cui l'errore dell'assist si può *misurare* invece che
descrivere.

Passa dalla stessa interfaccia `FrameSource` delle altre sorgenti, quindi il
banco esercita la pipeline vera e non una sua imitazione.

---

## Il risultato misurato

`python3 bench.py --shots 300 --aim-error 18` — 300 colpi per condizione, seed
fisso, un «utente» simulato che sbaglia la mira di una quantità nota:

| condizione | a segno | mancato medio |
|---|---|---|
| assist OFF | 26.7% | 20.08 px |
| assist ON, solo correzione automatica | 28.0% | 16.43 px |
| assist ON, seguendo il punto suggerito | 75.3% | 4.96 px |

**La correzione automatica da sola vale 1.3 punti percentuali.** Non è un
difetto: è la dimostrazione che il sistema rispetta la consegna. Otto pixel non
possono compensare i ~50 px che un bersaglio percorre mentre il colpo vola, e
per costruzione non lo faranno mai.

**Il valore sta nell'informazione**: seguire il punto suggerito porta i colpi a
segno dal 27% al 75%. L'assistenza che conta è quella che dice *dove guardare*,
ed è la parte che lascia all'utente la decisione.

Il costo del vincolo è anch'esso misurato: alzando il tetto dell'anticipo da
600 a 1000 ms i colpi a segno salgono a 80.7%, ma l'errore medio **peggiora**
(5.60 px contro 4.96) — quando un'estrapolazione lunga sbaglia, sbaglia di
molto. Il tetto della consegna non è solo etico: è anche tecnicamente la
scelta migliore.

---

## Cosa il programma non fa, e perché

- **Non inietta input nel sistema operativo.** Nessun movimento del puntatore,
  nessun click sintetico, nessun hook. Senza un tasto premuto dall'utente non
  esiste comando di sparo.
- **Non legge la memoria di altri processi** e non si aggancia al gioco. Legge i
  pixel del desktop, gli stessi che l'utente sta guardando.
- **Non decide quando sparare.** Il grilletto è dell'utente, sempre.

È pensato per il bersaglio incluso, per una clip registrata o per un gioco
locale in singolo. Usarlo in un multigiocatore online violerebbe le condizioni
d'uso di quel gioco, per quanto piccola sia la correzione.

---

## Limiti dichiarati

Quello che un progetto onesto dice prima che glielo chiedano.

- **La detection cromatica funziona quando il bersaglio ha un colore
  distintivo.** Su un nemico mimetizzato serve un riconoscitore semantico. Il
  punto di innesto è `detection.py`: basta che produca oggetti `Detection`, il
  resto della pipeline non cambia.
- **La modalità `motion` non regge la camera in movimento.** Appena l'utente
  ruota, l'intero fotogramma si muove rispetto allo sfondo appreso e la maschera
  si accende ovunque. Vale a inquadratura ferma. Il rimedio corretto sarebbe
  stimare l'omografia fra frame consecutivi e compensare il moto di fondo prima
  della sottrazione: è fuori dallo scopo di questa consegna.
- **Il modello è a velocità costante.** Un bersaglio che cambia direzione
  bruscamente viene inseguito con qualche frame di ritardo. È anche il motivo
  per cui l'orizzonte è 120 ms e non un secondo: oltre, il modello non descrive
  più niente.
- **Su video vero non c'è verità di riferimento.** Le percentuali di colpi a
  segno si possono misurare solo sul poligono. Su schermo il sistema si osserva,
  non si misura.
- **Il canale comandi è JSON in chiaro, senza autenticazione.** Va bene per una
  demo in laboratorio, non per una rete aperta. Per questo ogni messaggio in
  ingresso è validato e nessun payload ricevuto può far cadere un nodo.
- **L'associazione è greedy globale, non ungherese.** Con ≤ 8 tracce la
  differenza non si misura; oltre, l'algoritmo ungherese è il passo successivo.

---

## Verifica

```bash
cd consegna
pip install -r requirements-dev.txt
python3 -m pytest tests/ -q          # 98 test
python3 bench.py --shots 300         # la misura del risultato
python3 main.py --role both --headless --seconds 10    # i due nodi in UDP
```

Il documento `REVIEW.md` contiene la revisione della versione precedente del
codice (quella basata solo sul poligono) e spiega, difetto per difetto, cosa
non funzionava e perché.
