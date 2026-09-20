# Code review — KINE, mira assistita

Progetto rivisto: `public/consegna/` (Python, la consegna vera e propria) e
`src/lib/kine/` (TypeScript, la demo web). Il resto dello zip è impalcatura
generata dal builder e non l'ho considerato.

Ogni difetto qui sotto è stato **riprodotto** prima di essere segnalato, e ogni
correzione ha un test che fallisce sulla versione originale.

**Cosa trovi**
- `consegna/` — la versione Python corretta, con 68 test e un banco di misura
- `web-fixes/` — i file TypeScript corretti, con 7 test (`node --test`)

---

## Il giudizio in breve

L'impianto è buono, e va detto prima del resto: la scelta della soglia
cromatica invece di una rete è motivata e difendibile, il modello a velocità
costante col rumore di processo sull'accelerazione è quello giusto per il
problema, e i commenti spiegano *perché* invece di ripetere il codice — inclusi
quelli che documentano un errore della versione precedente e la sua correzione.
L'algebra del Kalman in `kalman.ts` l'ho verificata a mano riga per riga (F P Fᵀ
espanso, la matrice Q, il guadagno K, la forma (I−KH)P): è corretta.

Però il progetto ha un problema che sta sopra tutti gli altri, e non è un bug
di dettaglio.

### Il difetto principale: metà del sistema non è mai stata eseguita

`run_ai()` riceve i frame, li analizza, aggiorna il tracker e poi:

```python
run_ai.tracks = tracks   # type: ignore[attr-defined]
```

Scrive in un attributo di funzione che nessuno legge mai. La socket comandi è
aperta e mai usata, `peer_c` è calcolato e mai usato, `compute_assist` non è
nemmeno importato in quel percorso. **Il messaggio `fire` da B ad A descritto
nel README non viene mai inviato da nessuna riga del progetto.**

E dall'altra parte `run_capture()` non apre nessuna finestra, quindi il nodo A
non ha né mira né grilletto: non ha niente da chiedere. Sono due metà che non si
parlano.

La conseguenza è più grande dei due moduli: `--role both` fa tutto in locale con
un percorso di codice separato, quindi **l'unica modalità che gira davvero è
anche l'unica che non usa la rete**. Detection, tracking e assist non hanno mai
visto un frame passato per JPEG e UDP. È il motivo per cui il difetto
[D5](#d5) è rimasto invisibile: si manifesta solo sul frame compresso.

Nel codice corretto `--role both` avvia il nodo B in un thread e lo fa dialogare
su 127.0.0.1 **con lo stesso protocollo** usato fra due macchine. La modalità
distribuita viene esercitata a ogni esecuzione della demo.

### Il secondo difetto: il progetto non misura il proprio effetto

Un progetto che si chiama "mira assistita" deve rispondere a una domanda:
*l'assist migliora la precisione, e di quanto?* La versione Python non ha
nessuna metrica — i marcatori non collidono con niente, non c'è conteggio di
colpi a segno. La versione TS ha `meanError`, che però registra
`errors.push(d)` al momento dell'impatto, dove `d` è la distanza alla quale
*scatta* la collisione, cioè circa il raggio del bersaglio. Misurato:

```
errore di mira reale  ->  meanError mostrato dal cruscotto
      0 px                        25.94 px
      5 px                        21.36 px
     24 px                        23.32 px
```

Il numero è costante entro il rumore, e se mai è *anti*-correlato con la qualità
della mira. È una costante spacciata per una metrica.

---

## Difetti (Python)

### D1 — `recv_json` va in crash su ogni JSON valido che non sia un oggetto

`network.py:73`

```python
msg["_addr"] = addr    # TypeError se msg è una lista, un numero, una stringa o null
```

Riprodotto:

```
b'[1,2,3]'  -> TypeError: list indices must be integers or slices, not str
b'5'        -> TypeError: 'int' object does not support item assignment
b'null'     -> TypeError: 'NoneType' object does not support item assignment
```

L'eccezione esplode dentro `ack_loop`, che gira in un thread daemon senza
try/except: il thread muore in silenzio e **il nodo smette di rispondere senza
un solo messaggio d'errore**. Basta un datagramma per spegnerlo.

Lo stesso vale per l'handler: `float(msg["x"])` in `main.on_cmd` solleva
KeyError su un `fire` senza coordinate, con lo stesso esito.

*Correzione:* `recv_json` restituisce `(msg, addr)` e valida `isinstance(msg,
dict)` — i metadati di trasporto non entrano nel payload applicativo. Ogni
handler in `command_loop` è avvolto in un try/except che registra e prosegue.

### D2 — Perdita di memoria nel riassemblaggio dei frame

`network.py:22,42`

`self._buf` è un `defaultdict` che non viene mai svuotato per i frame
incompleti. Se manca un chunk, quel `frame_id` resta in RAM per sempre.
Riprodotto: 300 frame incompleti → 300 voci trattenute, crescita lineare per
tutta la durata dell'esecuzione. A 24 fps con il 4% di perdita è circa un frame
al secondo.

Mancano anche due controlli: `total == 0` produce un blob vuoto (i campi
arrivano dalla rete, non ci si fida), e un frame vecchio che si completa in
ritardo viene consegnato dopo uno più recente, facendo *tornare indietro nel
tempo* il tracker.

*Correzione:* coda `OrderedDict` a capacità fissa, scarto dei frame superati,
validazione dell'header. Dopo: 8 voci trattenute invece di 300.

### D3 — `chunk_bytes: 12000` moltiplica la perdita per nove

`config.json`

Sopra ~1472 byte il datagramma viene frammentato a livello IP. Perdere un
frammento fa scartare al kernel l'intero datagramma: con 12000 byte ogni chunk
viaggia in ~9 frammenti, e la probabilità che arrivi intero è `(1-p)⁹`. Su una
rete al 2% di perdita, la perdita effettiva per chunk passa dal 2% al 17%.

Il commento in cima al file dice "un JPEG non entra in un datagramma" — giusto,
ma la conclusione va portata fino all'MTU, non fermata a 64 KB.

*Correzione:* default a 1400 byte, con il calcolo spiegato nel commento.

### D4 — Il muro appiccicoso

`capture.py:35`

```python
if o["x"] < 24 or o["x"] > self.w - 24:
    o["vx"] *= -1
```

Il segno si inverte a *ogni frame* in cui l'oggetto è ancora oltre il bordo, non
una volta sola. Riprodotto con un disco lento (x=20, vx=−5, dt=1/24):

```
step 0: x=19.792  vx= 5.0
step 1: x=20.000  vx=-5.0
step 2: x=19.792  vx= 5.0     ... all'infinito
```

Resta incastrato fuori dall'arena a vibrare, e non ne esce mai. Con un `dt`
grande (finestra trascinata, processo in swap) l'oggetto attraversa proprio il
muro: `dt` non è clampato in `run_both`, mentre in `run_capture` sì — due
percorsi che si comportano diversamente.

*Correzione:* si riporta la posizione dentro il bordo **e** si impone il segno
(`vx = abs(vx)`), come già fa correttamente `arena.ts`. `dt` clampato nel metodo
`step`, un posto solo.

### D5 — Lo stesso bersaglio rilevato come due bersagli diversi {#d5}

`detection.py:20`

Le soglie per classe sono prese in modo indipendente, quindi non sono una
partizione: i box cromatici si intersecano e un pixel nell'intersezione finisce
in due maschere. Con `tolerance: 72` i tre colori della palette si intersecano
tutti e tre a coppie.

Sul frame grezzo non succede mai. Sul frame **compresso** sì: il JPEG a qualità
70 sposta di qualche unità i pixel sui bordi dei dischi. Osservato al frame 2:

```
verde     area=1451  centro=(431.0, 195.0)
azzurro   area=1453  centro=(431.0, 195.0)     <- stesso disco, due classi
```

Il tracker apre due track sullo stesso bersaglio e l'assist salta dall'una
all'altra. **Questo difetto è la prova del problema principale**: esiste solo
sul percorso di rete, che non veniva mai percorso.

*Correzione:* ogni pixel viene assegnato alla classe di colore più vicina fra
quelle entro tolleranza — la sovrapposizione diventa impossibile per
costruzione, invece che improbabile se si sceglie bene la tolleranza. La
distanza si calcola solo sui pixel effettivamente contesi (qualche decina su
230.000), quindi il costo resta 2,86 ms/frame contro i 41 ms di budget.

### D6 — Una traccia ruba la detection di un'altra

`prediction.py:55` (e identico in `tracker.ts:41`)

Il ciclo è *per traccia*: scorre le tracce nell'ordine della lista e ognuna si
prende la detection più vicina ancora libera. L'ordine della lista decide.
Riprodotto: due tracce, id 1 a x=0 e id 2 a x=50; arriva una sola detection a
x=49.

```
versione originale:  id=1 x=12.6 missed=0     <- id 1 se l'è presa
                     id=2 x=50.0 missed=1        (49 px, ma dentro il gate di 72)
```

La detection distava 1 px da id 2. Id 1 salta sul bersaglio sbagliato e id 2 va
in missed.

*Correzione:* si ordinano **tutte** le coppie (traccia, detection) per distanza
e si consumano in ordine. In più un gate di Mahalanobis a 3σ, che stringe il
cancello quando il filtro sa dov'è l'oggetto e lo allarga quando non lo sa —
con un gate in pixel fisso, una traccia appena nata e una consolidata vengono
trattate allo stesso modo. L'ottimo globale sarebbe l'algoritmo ungherese; con
≤ 8 tracce la differenza non si misura, ma vale la pena nominarlo nella
relazione.

### D7 — `np.linalg.inv` e la covarianza che perde la simmetria

`prediction.py:41,43`

```python
K = self.P @ H.T @ np.linalg.inv(S)
self.P = (np.eye(4) - K @ H) @ self.P
```

Due cose. `inv()` su una matrice quasi singolare restituisce numeri enormi
senza dirlo, mentre `solve()` è più stabile, più veloce e solleva. E la forma
breve `(I−KH)P`, algebricamente corretta, in virgola mobile perde la simmetria
di P: dopo qualche migliaio di update la covarianza può diventare non definita
positiva e il filtro smette di correggere, senza errori.

*Correzione:* `np.linalg.solve` e forma di Joseph
`P = (I−KH)P(I−KH)ᵀ + KRKᵀ`, simmetrica per costruzione. Verificato su 5000
update: P resta simmetrica con autovalori positivi.

### D8 — `__import__("network")` dentro una lambda dentro un thread

`main.py:103`

```python
threading.Thread(target=lambda: __import__("network").ack_loop(cmds, on_cmd, stop), daemon=True).start()
```

`ack_loop` non è importato in cima al file e viene recuperato a runtime con
`__import__`. Funziona, ma nasconde la dipendenza a chiunque legga gli import.
`stop` non viene mai impostato, la webcam non viene mai rilasciata,
`cv2.VideoCapture(0)` non viene verificata con `isOpened()`, e nessun loop ha
una via d'uscita oltre a Ctrl+C.

### D9 — Configurazione senza rete di sicurezza

`config.json` è un dict nudo, letto con `cfg["prediction"]["gate_px"]`. Una
chiave mancante o scritta male esplode con KeyError dentro il loop a 24 fps,
lontanissimo dal punto in cui il JSON è stato scritto — oppure, peggio, viene
ignorata in silenzio e il campo resta al default: il classico "cambio la config
e non succede niente".

*Correzione:* dataclass tipizzate con validazione all'avvio. Chiave sconosciuta
→ errore che nomina il campo e elenca quelli attesi; valore fuori range →
errore che dice il range. Compreso un limite su `max_correction_px`: oltre 32 px
non è più assistenza, e la validazione lo impedisce. Il vincolo della consegna
diventa una proprietà del codice, non una convenzione.

### D10 — Dettagli

- `apply_assist` è importato in `main.py` e mai usato; la stessa somma è
  riscritta a mano dentro `VirtualTrigger.on_fire`. Due punti in cui applicare
  il delta, uno solo di troppo.
- `frame_id` in `run_both` viene incrementato e mai usato.
- `run_both.tracks` come attributo di funzione: stato globale travestito.
- `print("[SPAR0] ...")` — è uno zero al posto della O. E `print` al posto di
  `logging`, che in un sistema a due nodi si sente.
- `confidence = area / (max_area * 0.25)`: con `max_area=20000` un disco pieno
  dà 0,30 e sembra una detection incerta. Il denominatore non ha significato
  fisico; il riferimento giusto è l'area nominale del bersaglio.
- La detection gira sul frame *con il mirino già disegnato sopra*. Qui non dà
  fastidio perché il bianco è lontano dai colori dei bersagli, ma è un cappio:
  il nodo B analizza i disegni del nodo A. Nel codice corretto il frame che va
  in rete è pulito e l'overlay si disegna solo sulla copia a schermo.
- Il canale comandi è JSON in chiaro senza autenticazione. Per un laboratorio va
  bene; va detto in una riga nella relazione, e va detto perché ogni messaggio
  in ingresso viene validato.

---

## Difetti (TypeScript)

### T1 — `solveIntercept` non è chiamata da nessuna parte

`intercept.ts:14`

È la funzione meglio documentata del progetto — la derivazione della quadratica
in testa al file è corretta e ben spiegata — e `grep` su tutto il repo trova
solo la sua definizione. `engine.ts` importa `leadPosition`, non lei.

Non è solo codice morto: è **proprio la funzione che risolve l'errore
dominante**. Il marcatore impiega ~0,5 s ad attraversare il poligono e un disco
a 100 px/s si sposta di ~50 px nel frattempo. I 50 ms di `lead_ms` compensano
la latenza della *misura*, non il tempo di *volo*: sono due ritardi diversi, e
il secondo è dieci volte il primo.

*Correzione:* portata in `intercept.py` e collegata ad `assist`. Misurato sul
banco, 300 colpi per condizione:

```
condizione                  colpi   a segno  mancato medio    mediana  corr. media
assist OFF                    300     26.7%         20.08p     13.02p        0.00p
assist ON (solo nudge)        300     28.0%         16.41p      8.66p        7.79p
assist ON + segui il sugg.    300     81.0%          6.51p      0.00p        7.79p
```

Questo dà anche al progetto la sua tesi: **il valore sta nell'informazione, non
nella correzione**. Gli 8 px automatici valgono poco ed è giusto così; il punto
suggerito porta i colpi a segno dal 27% all'81%. Per questo, nel codice
corretto, il punto suggerito *non* è clampato — è informazione mostrata
all'utente, e un'informazione troncata sarebbe sbagliata — mentre la correzione
applicata al colpo resta limitata a 8 px.

### T2 — La cattura gira a 20 fps invece di 24

`engine.ts:239`

```js
if (this.acc >= period) { this.acc = 0; ... }
```

Azzerando si butta via il resto e la cattura si aggancia al ritmo del
`requestAnimationFrame`. Con rAF a 60 Hz e `fps: 24`, l'accumulatore supera la
soglia ogni 3 frame. Simulato:

```
fps configurati : 24
con `acc = 0`   : 20.0 fps      <- e la metrica captureFps continua a dire 24
con `acc -= period` : 24.0 fps
```

### T3 — Il jitter di rete entra nel filtro di Kalman

`engine.ts:309`

```js
const snaps = this.tracker.update(dets, recvAt / 1000, this.cfg);
```

`recvAt` è l'istante di *arrivo*. Con `jitterMs: 12` su frame da 42 ms, il `dt`
del filtro oscilla del ±30% per ragioni che non hanno niente a che vedere col
movimento del bersaglio, e il rumore esce dalla velocità stimata — cioè proprio
dalla grandezza su cui si basa l'anticipo. `sentAt` è già disponibile nella
firma della callback.

### T4 — `nextId` è una variabile di modulo

`tracker.ts:16`

```js
let nextId = 1;   // fuori dalla classe: condivisa da tutte le istanze
```

`reset()` la riporta a 1 **per tutti**. Due engine vivi sullo stesso tab —
StrictMode che monta due volte in sviluppo, o due pannelli affiancati — bastano
a far riassegnare a uno gli id già in uso dall'altro. Verificato:

```
b: ['rosso#2', 'verde#1', 'azzurro#2']     <- due track con id 2 nello stesso tracker
```

Chiavi React duplicate, e un `hint.trackId` che non individua più un solo
bersaglio. Il difetto ha anche reso il mio primo test dipendente dall'ordine:
fallisce da solo, passa nella suite intera perché i test precedenti hanno già
fatto avanzare il contatore. È un buon argomento contro lo stato globale di
modulo, da solo.

### T5 — `meanError` non misura la precisione

`arena.ts:120` — già documentato sopra. Correzione: distanza di mancato dalla
superficie, aggiornata durante il volo e 0 se il colpo va a segno.

### T6 — Un canvas nuovo a ogni frame

`draw.ts:90`

```js
const tmp = document.createElement("canvas");   // dentro drawPip, ogni frame
```

20-60 elementi DOM al secondo, tutti da raccogliere. Riusare un canvas di
appoggio costa una riga.

### T7 — I timer non vengono mai ripuliti

`network.ts:90,125`

`this.timers.push(id)` senza mai rimuovere: a 20 fps × 8 chunk sono 160 numeri
al secondo che si accumulano finché la pagina resta aperta. `dispose()` li
svuota, ma solo alla chiusura.

### T8 — La barra spaziatrice viene mangiata su tutta la pagina

`lab-view.tsx:44`

```js
if (e.code !== "Space") return;
e.preventDefault();
```

Il listener è su `window` e non guarda il bersaglio dell'evento: non si può
scrivere uno spazio in un campo di testo, e premendo la barra su un pulsante a
fuoco si spara *e* si attiva il pulsante. Manca anche `e.repeat`, quindi
tenendola premuta si spara a raffica (il `cooldownMs` lo limita, non lo
impedisce).

### T9 — Dettagli

- `kalman.ts:19` — `private Q = new Float64Array(16)` è allocata per ogni track
  e mai letta: Q è calcolata inline in `predict`.
- `kalman.ts:38` — `private qAcc = 20` è dichiarata *sotto* il metodo che la
  usa. Funziona (gli inizializzatori di campo girano prima del corpo del
  costruttore), ma è una cosa che il lettore deve verificare invece di vedere.
- `engine.ts:143` — `startWebcam` non controlla che `getUserMedia` esista (su
  HTTP non-localhost non c'è) e, se `video.play()` fallisce, lo stream resta
  aperto con la spia della webcam accesa.
- `engine.ts:115` — `loop` non controlla `this.running`, quindi dipende
  dall'ordine di `cancelAnimationFrame`.
- `setConfig` resetta l'arena quando cambia `capture.width/height`, ma non
  richiama `resize()`: gli attributi `width`/`height` dei canvas restano quelli
  vecchi. Oggi è latente perché la risoluzione non è esposta nell'interfaccia.
- `config.ts` e `config.json` sono due copie divergenti della stessa
  configurazione (il TS ha `cooldownMs`, `markerSpeed`, `arena.scenario`; il
  Python ha `capture.source`). Per una consegna va bene, ma va detto quale dei
  due è la fonte.
- `Toggle` in `lab-view.tsx` non ha `role="switch"` né `aria-checked`.

---

## Cosa mancava del tutto

**I test.** Zero, su entrambi i lati. È l'intervento col rapporto
risultato/sforzo più alto per un progetto del genere, perché i difetti D1, D2,
D4, D6, T2 e T4 sono tutti esprimibili in cinque righe di test e nessuno di
loro si vede guardando la finestra.

Nel codice corretto: 68 test Python (`python3 -m pytest tests/ -q`) e 7
TypeScript (`node --import ./tests/estensioni.mjs --experimental-strip-types
--test tests/kine.test.ts`), compresi i test di regressione per ognuno dei
difetti qui sopra e un test di integrazione che percorre la catena intera —
poligono → JPEG → UDP a chunk → detection → Kalman → assist → comando →
marcatore. Serve proprio perché i moduli presi uno a uno possono essere tutti
corretti e il sistema restare inerte: è esattamente quello che succedeva.

**La riproducibilità.** `Math.random()` e `random` senza seed: due esecuzioni
della stessa configurazione danno numeri diversi, e un confronto "con assist /
senza assist" misura il rumore invece dell'effetto. Nel codice corretto il seed
è esplicito e `bench.py` è deterministico.

---

## Per la relazione

Tre cose che valgono più di un elenco di correzioni:

1. **La tesi del progetto.** Non "l'AI corregge la mira" — 8 px non correggono
   niente contro i 50 px di spostamento durante il volo, e i numeri lo dicono.
   La tesi è che *l'informazione vale più della correzione*: il punto suggerito
   porta i colpi a segno dal 27% all'81%, la correzione automatica dal 27% al
   28%. È anche la ragione per cui il sistema rispetta la consegna: quello che
   è potente (dire dove guardare) non è vincolante, e quello che è vincolante
   (spostare il colpo) è limitato a 8 px.

2. **Due ritardi distinti.** La latenza della misura (rete + età del frame,
   ~50 ms) e il tempo di volo del marcatore (~500 ms) sono grandezze diverse
   che si sommano. Confonderle è il motivo per cui `lead_ms` da solo non basta.

3. **Perché il difetto D5 era invisibile.** Vale un paragrafo: un difetto che
   esiste solo sul percorso compresso, in un sistema in cui il percorso
   compresso non veniva mai eseguito. È l'argomento migliore che puoi portare
   a favore del test di integrazione.
