"""Il trasporto: cio' che arriva dalla rete non e' fidato."""
import json
import socket
import threading

import pytest

from network import HEADER, MTU_SAFE_PAYLOAD, CommandSocket, FrameSocket, command_loop


@pytest.fixture
def coppia_frame():
    a = FrameSocket("127.0.0.1", 0, max_pending=4)
    b = FrameSocket("127.0.0.1", 0, max_pending=4)
    yield a, b
    a.close()
    b.close()


def test_round_trip_di_un_frame_spezzato(coppia_frame):
    a, b = coppia_frame
    payload = bytes(range(256)) * 60          # ~15 KB, 11 chunk
    n = a.send_jpeg(payload, ("127.0.0.1", b.port), 1, 1400, capture_ts=3.5)
    assert n > 1
    blob, ts = b.recv_frame(1.0)
    assert blob == payload
    assert ts == 3.5                           # il timestamp di cattura sopravvive


@pytest.mark.parametrize("cattivo", [b"", b"x", b"\x00" * 7])
def test_datagrammi_troppo_corti_ignorati(coppia_frame, cattivo):
    a, b = coppia_frame
    a.sock.sendto(cattivo, ("127.0.0.1", b.port))
    assert b.recv_frame(0.2) is None


def test_header_incoerente_ignorato(coppia_frame):
    """total=0 o index>=total arrivano dalla rete: non devono produrre un frame vuoto."""
    a, b = coppia_frame
    a.sock.sendto(HEADER.pack(1, 0, 0, 0.0) + b"dati", ("127.0.0.1", b.port))
    a.sock.sendto(HEADER.pack(2, 9, 3, 0.0) + b"dati", ("127.0.0.1", b.port))
    assert b.recv_frame(0.2) is None


def test_frame_incompleti_non_si_accumulano(coppia_frame):
    """
    Regressione del leak: il vecchio defaultdict non scartava mai un frame a cui
    mancava un chunk. A 24 fps e 4% di perdita cresceva di ~1 frame al secondo
    per tutta la durata dell'esecuzione.
    """
    a, b = coppia_frame
    for fid in range(1, 201):
        a.sock.sendto(HEADER.pack(fid, 0, 3, 0.0) + b"x" * 500, ("127.0.0.1", b.port))
    b.recv_frame(0.5)
    assert len(b._pending) <= b.max_pending
    assert b.frames_drop > 0


def test_frame_scaduti_non_vengono_consegnati_dopo_uno_recente(coppia_frame):
    """Un frame vecchio che si completa in ritardo farebbe tornare indietro il tracker."""
    a, b = coppia_frame
    peer = ("127.0.0.1", b.port)
    a.sock.sendto(HEADER.pack(1, 0, 2, 0.0) + b"vecchio", peer)   # incompleto
    a.send_jpeg(b"nuovo", peer, 2, 1400, capture_ts=1.0)          # completo
    assert b.recv_frame(0.5)[0] == b"nuovo"
    a.sock.sendto(HEADER.pack(1, 1, 2, 0.0) + b"-coda", peer)     # completa il vecchio
    assert b.recv_frame(0.2) is None


def test_chunk_di_default_entra_in_un_pacchetto_ip():
    assert MTU_SAFE_PAYLOAD > 1000


@pytest.fixture
def coppia_cmd():
    a = CommandSocket("127.0.0.1", 0)
    b = CommandSocket("127.0.0.1", 0)
    yield a, b
    a.close()
    b.close()


@pytest.mark.parametrize("payload", [b"[1,2,3]", b"5", b'"ciao"', b"null", b"non-json", b"{}"])
def test_payload_ostili_non_fanno_crashare(coppia_cmd, payload):
    """
    Regressione. `msg["_addr"] = addr` sollevava TypeError su ogni JSON valido
    ma non-oggetto, dentro un thread daemon: il thread moriva in silenzio e il
    nodo smetteva di rispondere senza un solo messaggio d'errore.
    """
    a, b = coppia_cmd
    a.sock.sendto(payload, ("127.0.0.1", b.port))
    assert b.recv_json() is None


def test_messaggio_valido_con_mittente(coppia_cmd):
    a, b = coppia_cmd
    a.send_json({"type": "fire", "x": 1.0}, ("127.0.0.1", b.port))
    msg, addr = b.recv_json()
    assert msg["type"] == "fire" and addr[0] == "127.0.0.1"
    assert "_addr" not in msg          # i metadati di trasporto restano fuori dal payload


def test_handler_che_esplode_non_uccide_il_loop(coppia_cmd):
    """Un solo messaggio senza la chiave attesa non deve fermare il nodo."""
    a, b = coppia_cmd
    visti, stop = [], threading.Event()
    def on_cmd(msg, addr):
        visti.append(msg["type"])
        if msg["type"] == "cattivo":
            raise KeyError("x")        # come il vecchio float(msg["x"])
    t = threading.Thread(target=command_loop, args=(b, on_cmd, stop), daemon=True)
    t.start()
    for tipo in ("cattivo", "buono"):
        a.send_json({"type": tipo, "sentAt": 1.0}, ("127.0.0.1", b.port))
        threading.Event().wait(0.1)
    stop.set(); t.join(1.0)
    assert visti == ["cattivo", "buono"]


def test_ack_automatico(coppia_cmd):
    a, b = coppia_cmd
    stop = threading.Event()
    t = threading.Thread(target=command_loop, args=(b, lambda m, addr: None, stop), daemon=True)
    t.start()
    a.send_json({"type": "aim", "x": 1, "y": 2, "sentAt": 42.0}, ("127.0.0.1", b.port))
    ricevuto = None
    for _ in range(20):
        ricevuto = a.recv_json()
        if ricevuto:
            break
    stop.set(); t.join(1.0)
    assert ricevuto is not None and ricevuto[0] == {"type": "ack", "echo": 42.0}
