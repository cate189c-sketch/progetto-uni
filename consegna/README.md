# KINE — Mira assistita (consegna)

Sistema didattico: computer vision + tracciamento Kalman + **mira assistita**
su due nodi collegati in UDP.

L'utente punta e preme il grilletto. L'AI non muove il mouse del sistema
operativo e non spara da sola: quando l'utente spara, calcola una correzione
clampata (8 px di default) verso il bersaglio più vicino *al puntamento
corrente*, e mostra un punto suggerito ("guarda lì") che tiene conto del tempo
di volo del marcatore.

La separazione è deliberata:

| | chi decide | limite |
|---|---|---|
| **punto suggerito** | l'AI informa, l'utente guarda | nessuno: un'informazione troncata sarebbe sbagliata |
| **correzione applicata** | l'AI agisce sul colpo | 8 px, imposto dalla validazione della config |

## Cosa non è

Non è un aimbot su un videogioco commerciale. `input_control` applica l'offset
a un crosshair **virtuale** su un poligono sintetico generato dal programma
stesso. Niente cattura dello schermo, niente AutoHotkey, niente iniezione di
input in altri processi. Senza un click o una barra spaziatrice dell'utente il
sistema non emette nulla.

## Esecuzione

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python3 main.py                                  # demo su un PC, due nodi su loopback
python3 main.py --role capture --peer 192.168.1.20
python3 main.py --role ai --bind 0.0.0.0
```

`--role both` non è una scorciatoia che salta la rete: avvia il nodo B in un
thread e lo fa dialogare su 127.0.0.1 con lo stesso identico protocollo usato
fra due macchine. La modalità distribuita viene quindi esercitata a ogni
esecuzione della demo, invece di restare codice mai percorso.

Controlli nella finestra: muovi il mouse, click sinistro = sparo assistito,
Shift+click = sparo puro, `R` azzera le metriche, `Esc` esce.

## Misure

```bash
python3 bench.py --shots 300 --aim-error 18
python3 -m pytest tests/ -q
```

`bench.py` esegue la pipeline completa a passo fisso, senza finestra e con seed
fisso, con un "utente" simulato che sbaglia la mira di una quantità nota. Tre
condizioni a confronto — le righe da mettere nella relazione:

```
condizione                  colpi   a segno  mancato medio    mediana  corr. media
assist OFF                    300     26.7%         20.08p     13.02p        0.00p
assist ON (solo nudge)        300     28.0%         16.41p      8.66p        7.79p
assist ON + segui il sugg.    300     81.0%          6.51p      0.00p        7.79p
```

La lettura: la sola correzione automatica vale poco, ed è voluto — 8 px non
possono compensare i ~50 px di spostamento del bersaglio durante il volo del
marcatore. Il valore del sistema sta nell'**informazione**: seguire il punto
suggerito porta i colpi a segno dal 27% all'81%. L'AI dice dove guardare, il
giocatore decide e spara.

**Distanza di mancato** (`miss_px`): 0 se il colpo va a segno, altrimenti di
quanto il marcatore ha sfiorato la superficie del bersaglio *preso di mira*.
Non è la distanza alla quale scatta la collisione: quella vale circa il raggio
con qualunque mira, e non misura niente.

## Moduli

| File | Ruolo |
|---|---|
| `config.py` | Config tipizzata e validata: un errore di battitura si ferma all'avvio |
| `capture.py` | Poligono sintetico o webcam, registro dei colpi |
| `detection.py` | Soglia cromatica OpenCV, maschere mutuamente esclusive, blob, centroidi |
| `prediction.py` | Kalman 2D a velocità costante (forma di Joseph), associazione globale, id persistenti |
| `intercept.py` | Soluzione dell'intercetta cinematica: dove sarà il bersaglio quando il marcatore arriva |
| `assist.py` | Gate sul puntamento + clamp + anticipo |
| `network.py` | Datagrammi a chunk, riassemblaggio limitato, validazione dell'input |
| `input_control.py` | Applica dx, dy al punto di sparo virtuale |
| `bench.py` | Esperimento riproducibile assist on/off |
| `tests/` | 68 test, `python3 -m pytest tests/ -q` |

## Protocollo

```
A -> B  frame    header !IHHd (frame_id, indice, totale, capture_ts) + JPEG
A -> B  comando  {"type":"aim", "x","y","sentAt"}
A -> B  comando  {"type":"fire_request", "x","y","useAssist","seq","sentAt","rttMs"}
B -> A  comando  {"type":"fire", "x","y","userX","userY","dx","dy",
                  "trackId","assist","seq","sentAt","echo","suggested"}
entrambi         {"type":"ack", "echo": <sentAt del mittente>}
```

Due dettagli che non sono cosmetici:

- **`capture_ts` nell'header del frame.** Il nodo B data le misure con
  l'istante di cattura, non di arrivo. Usando l'ora di arrivo, il jitter di
  rete entrerebbe nel `dt` del filtro di Kalman e ne uscirebbe come rumore
  sulla velocità stimata — cioè proprio sulla grandezza da cui dipende
  l'anticipo.
- **`echo` riporta sempre l'istante letto sull'orologio di chi ha fatto la
  richiesta.** Fra due macchine gli orologi non sono sincronizzati: un RTT
  calcolato come differenza fra letture di clock diversi non vorrebbe dire
  niente. Con `echo`, l'RTT è la differenza fra due letture dello stesso clock.

Il canale comandi è JSON in chiaro, senza autenticazione: va bene per una demo
in laboratorio, non per una rete aperta. Per questo ogni messaggio in ingresso
viene validato e nessun payload ricevuto può far cadere un nodo.
