"""
UDP a chunk.

Un JPEG non entra in un datagramma, e nemmeno un frame raw. Si spezza in chunk;
se ne manca uno il frame e' perso. E' la semantica che si vuole mostrare:
nessuna garanzia di consegna, nessun ordine, MTU finita.

Due socket separate:
  - porta frame,  A -> B: header binario + payload JPEG
  - porta comandi, bidirezionale: JSON piccoli (aim, fire, ack)

Il canale comandi e' JSON su localhost/LAN, senza autenticazione ne' cifratura:
va bene per una demo didattica in laboratorio, non per una rete aperta. Per
questo la validazione dei messaggi in ingresso e' esplicita e nessun payload
ricevuto puo' far crollare il processo.
"""

from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

log = logging.getLogger("kine.net")

# frame_id, indice chunk, totale chunk, istante di cattura (monotonic del mittente)
HEADER = struct.Struct("!IHHd")
MAX_DATAGRAM = 65507  # limite del payload UDP su IPv4

# Oltre ~1472 byte di payload il datagramma viene frammentato a livello IP.
# Perdere un frammento fa scartare al kernel l'intero datagramma, quindi la
# perdita effettiva per chunk si moltiplica per il numero di frammenti.
MTU_SAFE_PAYLOAD = 1472 - HEADER.size


class FrameSocket:
    """Invio e riassemblaggio di frame spezzati in chunk."""

    def __init__(self, bind_ip: str, port: int, max_pending: int = 8):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 21)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 21)
        self.sock.bind((bind_ip, port))
        self.sock.settimeout(0.05)
        self.max_pending = max_pending
        # OrderedDict = coda a capacita' fissa. Il vecchio defaultdict non
        # veniva mai svuotato per i frame incompleti: bastava perdere un chunk
        # perche' quel frame restasse in RAM per sempre. A 24 fps con il 4% di
        # perdita sono ~1 frame al secondo trattenuto, cioe' un leak lineare.
        self._pending: OrderedDict[int, dict[int, bytes]] = OrderedDict()
        self._meta: dict[int, tuple[int, float]] = {}
        self._last_delivered = 0
        self.frames_ok = 0
        self.frames_drop = 0
        self.chunks_rx = 0

    @property
    def port(self) -> int:
        return self.sock.getsockname()[1]

    def send_jpeg(self, jpeg: bytes, peer: tuple[str, int], frame_id: int, chunk_bytes: int, capture_ts: float) -> int:
        chunk = max(1, min(int(chunk_bytes), MAX_DATAGRAM - HEADER.size))
        if chunk > MTU_SAFE_PAYLOAD:
            log.debug("chunk di %d byte: oltre l'MTU, il datagramma verra' frammentato", chunk)
        n = max(1, (len(jpeg) + chunk - 1) // chunk)
        if n > 0xFFFF:
            raise ValueError(f"frame troppo grande: {n} chunk, il campo 'total' e' a 16 bit")
        for i in range(n):
            part = jpeg[i * chunk : (i + 1) * chunk]
            try:
                self.sock.sendto(HEADER.pack(frame_id, i, n, capture_ts) + part, peer)
            except OSError as e:
                # Buffer di invio pieno: e' UDP, il chunk si perde. Il frame
                # sara' incompleto e verra' scartato dal ricevitore, che e'
                # esattamente il comportamento da mostrare.
                log.debug("chunk %d/%d perso in invio: %s", i, n, e)
        return n

    def recv_frame(self, timeout_s: float) -> tuple[bytes, float] | None:
        """
        Restituisce (jpeg, capture_ts) del primo frame completo, o None allo
        scadere del timeout. capture_ts serve al nodo B per datare le misure:
        usare l'ora di arrivo introdurrebbe il jitter di rete dentro il dt del
        Kalman, e la velocita' stimata ne porterebbe il rumore.
        """
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            restante = deadline - time.monotonic()
            if restante <= 0:
                return None
            try:
                self.sock.settimeout(min(0.05, restante))
                data, _ = self.sock.recvfrom(MAX_DATAGRAM)
            except (socket.timeout, TimeoutError):
                continue
            except OSError as e:
                log.debug("recvfrom: %s", e)
                continue

            completo = self._ingest(data)
            if completo is not None:
                return completo

    def _ingest(self, data: bytes) -> tuple[bytes, float] | None:
        if len(data) < HEADER.size:
            return None
        fid, idx, total, capture_ts = HEADER.unpack(data[: HEADER.size])
        # Validazione: i campi arrivano dalla rete, non ci si fida.
        if total == 0 or idx >= total:
            log.debug("header incoerente: fid=%s idx=%s total=%s", fid, idx, total)
            return None
        if fid <= self._last_delivered:
            return None  # chunk in ritardo di un frame gia' consegnato o superato

        self.chunks_rx += 1
        parti = self._pending.get(fid)
        if parti is None:
            parti = {}
            self._pending[fid] = parti
            self._meta[fid] = (total, capture_ts)
            self._evict()
        parti[idx] = data[HEADER.size :]

        atteso, ts = self._meta[fid]
        if len(parti) < atteso:
            return None

        blob = b"".join(parti[i] for i in range(atteso))
        self._forget(fid)
        # I frame piu' vecchi di questo non servono piu': mostrare un frame
        # scaduto dopo uno recente farebbe retrocedere il tracker nel tempo.
        for vecchio in [k for k in self._pending if k < fid]:
            self._forget(vecchio)
            self.frames_drop += 1
        self._last_delivered = fid
        self.frames_ok += 1
        return blob, ts

    def _forget(self, fid: int) -> None:
        self._pending.pop(fid, None)
        self._meta.pop(fid, None)

    def _evict(self) -> None:
        while len(self._pending) > self.max_pending:
            fid, _ = self._pending.popitem(last=False)
            self._meta.pop(fid, None)
            self.frames_drop += 1

    @property
    def frame_loss(self) -> float:
        n = self.frames_ok + self.frames_drop
        return self.frames_drop / n if n else 0.0

    def close(self) -> None:
        self.sock.close()


class CommandSocket:
    """JSON su UDP. Piccolo, non frammentato, nessuna ritrasmissione."""

    def __init__(self, bind_ip: str, port: int):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind_ip, port))
        self.sock.settimeout(0.05)

    @property
    def port(self) -> int:
        return self.sock.getsockname()[1]

    def send_json(self, obj: dict[str, Any], peer: tuple[str, int]) -> None:
        try:
            data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as e:
            log.error("messaggio non serializzabile, non inviato: %s", e)
            return
        if len(data) > MAX_DATAGRAM:
            log.error("messaggio di %d byte: troppo grande per un datagramma", len(data))
            return
        try:
            self.sock.sendto(data, peer)
        except OSError as e:
            log.debug("send_json: %s", e)

    def recv_json(self) -> tuple[dict[str, Any], tuple[str, int]] | None:
        """
        Restituisce (messaggio, mittente) oppure None.

        Il mittente NON viene iniettato dentro il dict: la versione precedente
        faceva `msg["_addr"] = addr`, che (a) va in crash con TypeError su ogni
        JSON valido ma non-oggetto - `[1,2,3]`, `5`, `null` -, e (b) mescola
        metadati di trasporto con il payload applicativo.
        """
        try:
            data, addr = self.sock.recvfrom(MAX_DATAGRAM)
        except (socket.timeout, TimeoutError):
            return None
        except OSError as e:
            log.debug("recvfrom: %s", e)
            return None
        try:
            msg = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.debug("payload non JSON da %s, scartato", addr)
            return None
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            log.debug("messaggio senza 'type' da %s, scartato", addr)
            return None
        return msg, addr

    def close(self) -> None:
        self.sock.close()


def command_loop(
    sock: CommandSocket,
    on_cmd: Callable[[dict[str, Any], tuple[str, int]], None],
    stop: threading.Event,
    *,
    auto_ack: bool = True,
) -> None:
    """
    Loop di ricezione comandi, pensato per girare in un thread.

    Ogni handler e' avvolto in un try/except: nella versione precedente un
    singolo messaggio senza la chiave "x" sollevava KeyError dentro un thread
    daemon, il thread moriva in silenzio e il nodo smetteva di rispondere senza
    che nulla lo segnalasse.
    """
    while not stop.is_set():
        ricevuto = sock.recv_json()
        if ricevuto is None:
            continue
        msg, addr = ricevuto
        tipo = msg.get("type")
        if tipo == "ack":
            continue
        try:
            on_cmd(msg, addr)
        except Exception:
            log.exception("handler del comando %r fallito, messaggio scartato", tipo)
            continue
        if auto_ack and "sentAt" in msg:
            sock.send_json({"type": "ack", "echo": msg["sentAt"]}, addr)
