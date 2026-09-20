"""
Fusione input umano + AI.

L'utente fornisce (user_x, user_y) e il grilletto. Qui si applica il delta
dell'assist al *punto di sparo virtuale*. Non si inietta movimento nel sistema
operativo, non si scrive in altri processi, non si spara da soli: senza una
chiamata a `on_fire()` originata da un click o dalla barra spaziatrice questo
modulo non produce niente.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from assist import AssistHint, apply_assist

log = logging.getLogger("kine.input")


@dataclass(slots=True)
class FireCommand:
    user_x: float
    user_y: float
    x: float
    y: float
    dx: float
    dy: float
    track_id: int | None
    assist: bool
    seq: int
    sent_at: float

    def to_json(self) -> dict[str, Any]:
        return {
            "type": "fire",
            "userX": self.user_x,
            "userY": self.user_y,
            "x": self.x,
            "y": self.y,
            "dx": self.dx,
            "dy": self.dy,
            "trackId": self.track_id,
            "assist": self.assist,
            "seq": self.seq,
            "sentAt": self.sent_at,
        }

    @staticmethod
    def from_json(msg: dict[str, Any]) -> FireCommand:
        """Solleva (KeyError/TypeError/ValueError) se il messaggio non e' un fire valido."""
        return FireCommand(
            user_x=float(msg["userX"]),
            user_y=float(msg["userY"]),
            x=float(msg["x"]),
            y=float(msg["y"]),
            dx=float(msg["dx"]),
            dy=float(msg["dy"]),
            track_id=None if msg.get("trackId") is None else int(msg["trackId"]),
            assist=bool(msg.get("assist", False)),
            seq=int(msg.get("seq", 0)),
            sent_at=float(msg.get("sentAt", 0.0)),
        )


class VirtualTrigger:
    def __init__(self, cooldown_ms: float = 0.0) -> None:
        self.cooldown_s = cooldown_ms / 1000.0
        self.last_cmd: FireCommand | None = None
        self._seq = 0
        self._last_fire = float("-inf")

    def ready(self, now: float | None = None) -> bool:
        return (time.monotonic() if now is None else now) - self._last_fire >= self.cooldown_s

    def on_fire(
        self,
        user: tuple[float, float],
        hint: AssistHint,
        *,
        use_assist: bool,
    ) -> FireCommand | None:
        """None se il grilletto e' ancora in cooldown: il chiamante non deve sparare."""
        now = time.monotonic()
        if not self.ready(now):
            return None
        self._last_fire = now
        self._seq += 1

        effettivo = hint if use_assist else AssistHint()
        x, y = apply_assist(user, effettivo)
        cmd = FireCommand(
            user_x=user[0],
            user_y=user[1],
            x=x,
            y=y,
            dx=effettivo.dx,
            dy=effettivo.dy,
            track_id=effettivo.track_id if effettivo.active else None,
            assist=effettivo.active,
            seq=self._seq,
            sent_at=now,
        )
        self.last_cmd = cmd
        log.info(
            "SPARO #%d user=(%.0f,%.0f) delta=(%.1f,%.1f) px assist=%s",
            cmd.seq,
            user[0],
            user[1],
            cmd.dx,
            cmd.dy,
            cmd.assist,
        )
        return cmd
