"""Per-WebSocket client connection state (STAB-05 / D-11 extraction).

Moved wholesale from ``server/main.py`` as Plan 01-11 Task 1. Preserves
every behavioral contract established in earlier phase-1 plans:

  * Plan 01-01 STAB-04: ``send_queue`` ``maxsize=4`` + drop-OLDEST-on-full
    + request one IDR on the FIRST drop of a streak; keyframe enqueue
    clears the queue + resets the streak counter.
  * Plan 01-08 STAB-06: ``ServerFSM`` instantiated per-session;
    ``last_reported_client_state`` captures the most recent client-stamped
    state for Plan 01-17 observability.
  * Pure mechanical move — no signature changes, no renames, no logic
    edits. ``server/main.py`` now re-exports ``ClientSession`` from this
    module for backward compatibility with callers that still import
    ``from server.main import ClientSession``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

import websockets
from websockets.server import WebSocketServerProtocol

from common.messages import QualitySettings
from common.session_fsm import ServerFSM

from typing import Tuple  # Phase 3 D-02 — crop_rect type hint

if TYPE_CHECKING:
    from server.session_runtime import SessionRuntime


logger = logging.getLogger("teraguchi.server.client_session")


# Phase 3 WR-02 — bounded _dropped_seqs cap (mirrors client-side). Prevents
# soft-DoS memory growth from attacker-controlled peers spamming chunk-0
# with random sequence_ids. See client/protocol.py._track_dropped_seq for
# the rationale + ordering notes.
_DROPPED_SEQS_MAX = 4096


def track_dropped_seq(dropped_seqs: set, seq_id: int) -> None:
    """WR-02 — add ``seq_id`` to ``dropped_seqs`` with bounded growth.

    Pops an arbitrary existing entry when the set reaches
    ``_DROPPED_SEQS_MAX``. Module-level helper so session_runtime.py can
    apply the cap without duplicating the constant.
    """
    if len(dropped_seqs) >= _DROPPED_SEQS_MAX:
        dropped_seqs.pop()
    dropped_seqs.add(seq_id)


class ClientSession:
    """Tracks per-client connection state."""

    def __init__(self, ws: WebSocketServerProtocol):
        self.ws = ws
        self.client_id = str(id(ws))
        addr = ws.remote_address
        self.client_host = addr[0] if addr else ""
        self.authenticated = False
        self.username = ""
        self.challenge = ""
        self.quality = QualitySettings()
        self.monitor_id = 1
        self.supports_h264 = True
        self.supports_h265 = False
        self.supports_yuv444 = True
        self.supports_audio = True
        self.client_screen_width = 0
        self.client_screen_height = 0
        self.runtime: Optional["SessionRuntime"] = None
        # STAB-04: maxsize=4 (was 30) — a bigger queue just delays the stall.
        # IDR-on-drop recovery in .enqueue() bounds any stall to ~100 ms.
        self.send_queue: asyncio.Queue = asyncio.Queue(maxsize=4)
        self._drops_since_keyframe = 0
        self._send_task: Optional[asyncio.Task] = None
        # STAB-06 / Plan 01-08: per-session FSM. current_state.id is stamped
        # onto every outbound HealthPong (see _health_ping_loop). The
        # last_reported_client_state field caches the most recent ping so
        # Plan 01-17 observability can log disagreement pairs.
        self.fsm = ServerFSM()
        self.last_reported_client_state: str = ""
        # Phase 3 D-02 — per-session capture-mode state. Defaults preserve
        # pre-Phase-3 behavior (mirror_all ⇔ crop_rect=None ⇔ unchanged
        # always-full-virtual-desktop capture path). apply_capture_mode
        # in session_runtime.py flips crop_rect for single / pick_one.
        # capture_mode_degraded=True signals Plan 05's fall-back-to-primary
        # toast surface.
        self.capture_mode: str = "mirror_all"
        self.picked_monitor_id: int = -1
        self.picked_monitor_name: str = ""
        self.crop_rect: Optional[Tuple[int, int, int, int]] = None
        self.capture_mode_degraded: bool = False
        # Phase 3 D-15 / Plan 03-06 — per-direction clipboard toggles.
        # Defaults all True: D-16 "secure defaults = all directions ON"
        # + preserves pre-Phase-3 always-on clipboard behavior for
        # legacy clients that don't negotiate toggles. Populated from
        # ClientHelloMsg at handshake time in SessionRuntime.handle_input.
        self.clipboard_text_c2s: bool = True
        self.clipboard_text_s2c: bool = True
        self.clipboard_image_c2s: bool = True
        self.clipboard_image_s2c: bool = True
        # Phase 3 D-17 / Plan 03-06 — per-session chunk-assembler state.
        # Keyed by sequence_id. ``_dropped_seqs`` tracks sequence_ids
        # that were disabled at chunk-0 boundary (Pitfall 7 mid-stream
        # toggle race fix) so subsequent chunks of the same sequence
        # drop silently without re-checking the toggle.
        # WR-02: growth capped at _DROPPED_SEQS_MAX via track_dropped_seq().
        self._clipboard_chunks: dict = {}
        self._dropped_seqs: set = set()

    def start_sender(self):
        self._send_task = asyncio.create_task(self._send_loop())

    async def _send_loop(self):
        try:
            while True:
                data = await self.send_queue.get()
                if data is None:
                    break
                await self.ws.send(data)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.debug("Send error: %s", e)

    async def enqueue(self, data, is_keyframe: bool = False):
        """Enqueue a frame for send.

        STAB-04 / VIDEO-06: on overflow, drop OLDEST (favor freshness) and on
        the FIRST drop of a streak call self.runtime.encoder.request_keyframe()
        so an IDR arrives within ~100 ms (bounded stall, not the ~2s GOP stall
        the old large-queue + return-False policy produced).

        On keyframe enqueue, clear the queue — the new IDR supersedes any
        pending P-frames that would reference a frame the decoder will skip.
        """
        if is_keyframe:
            # New IDR supersedes pending P-frames
            while not self.send_queue.empty():
                try:
                    self.send_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            self._drops_since_keyframe = 0
        try:
            self.send_queue.put_nowait(data)
            return True
        except asyncio.QueueFull:
            # Drop OLDEST (favor freshness), then enqueue the new item.
            try:
                self.send_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self.send_queue.put_nowait(data)
            except asyncio.QueueFull:
                pass   # Shouldn't happen after get_nowait, but be defensive
            self._drops_since_keyframe += 1
            # Request IDR on the FIRST drop of a streak only.
            if self._drops_since_keyframe == 1 and self.runtime and self.runtime.encoder:
                try:
                    self.runtime.encoder.request_keyframe()
                    # OBS-03 (Plan 01-14) — bump request counter for overlay /
                    # dashboard keyframe-request-rate visibility. Paired with
                    # SessionRuntime._on_encoded_frame's record_keyframe_emitted
                    # so the request/emit ratio is observable.
                    self.runtime.health.record_keyframe_requested()
                    logger.warning(
                        "broadcaster.idr_requested client=%s drops=%d",
                        self.client_id, self._drops_since_keyframe)
                except Exception as e:
                    logger.debug("request_keyframe failed: %s", e)
            return False

    def stop(self):
        if self._send_task:
            self.send_queue.put_nowait(None)
