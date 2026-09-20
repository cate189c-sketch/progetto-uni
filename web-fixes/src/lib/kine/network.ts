/**
 * Datagrammi simulati, semantica UDP.
 *
 * Un browser non può aprire un socket UDP verso un secondo PC: il vincolo
 * della demo è rispettare la semantica (nessuna garanzia di consegna, niente
 * ordine, MTU fittizia) così si misurano latenza e perdita. Il payload di un
 * frame non compresso 480×270 RGBA non entra in un datagramma: lo spezziamo
 * in N chunk. Se ne manca uno, il frame è perso — ed è esattamente il motivo
 * per cui 6 MB in un unico sendto() (versione iniziale) non può funzionare.
 */

export type FramePacket = {
  type: "chunk";
  frameId: number;
  index: number;
  total: number;
  sentAt: number;
};

export type CommandPacket = {
  type: "cmd";
  payload: string;
  sentAt: number;
};

type Wire = FramePacket | CommandPacket | { type: "ack"; sentAt: number; echo: number };

type Pending = {
  total: number;
  got: Set<number>;
  sentAt: number;
  image: ImageData | null;
};

export class SimulatedUdp {
  latencyMs = 40;
  jitterMs = 12;
  packetLoss = 0.04;
  timeoutMs = 180;

  packetsSent = 0;
  packetsLost = 0;
  framesOk = 0;
  framesDrop = 0;
  lastOneWay = 0;
  lastRtt = 0;

  private nextFrame = 1;
  private pending = new Map<number, Pending>();
  // Set e non array: ogni timer si cancella da solo quando scatta. Con
  // `timers.push(id)` e nessuna rimozione, a 20 fps x 8 chunk l'array cresceva
  // di 160 numeri al secondo e non veniva mai svuotato finche' la pagina
  // restava aperta.
  private timers = new Set<number>();
  onFrame: ((img: ImageData, sentAt: number, recvAt: number) => void) | null = null;
  onCommand: ((json: string, sentAt: number, recvAt: number) => void) | null = null;
  onAck: ((rtt: number) => void) | null = null;

  configure(p: {
    latencyMs: number;
    jitterMs: number;
    packetLoss: number;
    timeoutMs: number;
  }) {
    this.latencyMs = p.latencyMs;
    this.jitterMs = p.jitterMs;
    this.packetLoss = p.packetLoss;
    this.timeoutMs = p.timeoutMs;
  }

  sendFrame(image: ImageData, chunkCount: number) {
    const frameId = this.nextFrame++;
    const total = Math.max(2, chunkCount | 0);
    const sentAt = performance.now();
    const copy = new ImageData(new Uint8ClampedArray(image.data), image.width, image.height);
    this.pending.set(frameId, {
      total,
      got: new Set(),
      sentAt,
      image: copy,
    });
    for (let i = 0; i < total; i++) {
      this.dispatch(
        { type: "chunk", frameId, index: i, total, sentAt },
        (pkt) => this.ingestChunk(pkt as FramePacket, copy),
      );
    }
    const dropTimer = window.setTimeout(() => {
      this.timers.delete(dropTimer);
      const pend = this.pending.get(frameId);
      if (!pend) return;
      this.pending.delete(frameId);
      this.framesDrop++;
    }, this.timeoutMs + this.latencyMs + this.jitterMs + 30);
    this.timers.add(dropTimer);
  }

  sendCommand(json: string) {
    const sentAt = performance.now();
    this.dispatch({ type: "cmd", payload: json, sentAt }, (pkt) => {
      const p = pkt as CommandPacket;
      const recvAt = performance.now();
      this.onCommand?.(p.payload, p.sentAt, recvAt);
      this.dispatch({ type: "ack", sentAt: recvAt, echo: p.sentAt }, (ack) => {
        if (ack.type !== "ack") return;
        const rtt = performance.now() - ack.echo;
        this.lastRtt = rtt;
        this.onAck?.(rtt);
      });
    });
  }

  dispose() {
    for (const id of this.timers) clearTimeout(id);
    this.timers.clear();
    this.pending.clear();
  }

  private dispatch(packet: Wire, deliver: (p: Wire) => void) {
    this.packetsSent++;
    if (Math.random() < this.packetLoss) {
      this.packetsLost++;
      return;
    }
    const delay = Math.max(
      0,
      this.latencyMs + (Math.random() * 2 - 1) * this.jitterMs,
    );
    const id = window.setTimeout(() => {
      this.timers.delete(id);
      deliver(packet);
    }, delay);
    this.timers.add(id);
  }

  private ingestChunk(pkt: FramePacket, image: ImageData) {
    const pend = this.pending.get(pkt.frameId);
    if (!pend) return;
    pend.got.add(pkt.index);
    if (pend.got.size >= pend.total) {
      this.pending.delete(pkt.frameId);
      this.framesOk++;
      const recvAt = performance.now();
      this.lastOneWay = recvAt - pend.sentAt;
      this.onFrame?.(image, pend.sentAt, recvAt);
    }
  }

  get frameLossRate() {
    const n = this.framesOk + this.framesDrop;
    return n ? this.framesDrop / n : 0;
  }

  get packetLossRate() {
    return this.packetsSent ? this.packetsLost / this.packetsSent : 0;
  }
}
