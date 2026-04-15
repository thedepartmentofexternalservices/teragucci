"""
Teraguchi headless client — connects to a Teraguchi server via WebSocket,
decodes H.264 video frames into PIL Images, and sends mouse/keyboard input.

No Qt dependency. Designed for use by the AI MCP server.
"""

import asyncio
import base64
import collections
import io
import json
import logging
import ssl
import threading
import time
from typing import List, Optional, Tuple

import websockets

from common.messages import (
    MsgType, ClientHelloMsg,
    FrameType, VideoCodec, AudioCodec,
    decode_video_header, decode_jpeg_header, decode_audio_header,
    VIDEO_HEADER_SIZE, JPEG_HEADER_SIZE, AUDIO_HEADER_SIZE,
    AuthResponse, parse_message,
)

logger = logging.getLogger(__name__)


class HeadlessClient:
    """
    Headless WebSocket client for the Teraguchi protocol.

    Frame history size: last FRAME_HISTORY_SIZE decoded frames are kept in
    memory (ring buffer) so stream-watching tools can sample them without
    needing to decode on the fly.
    """

    FRAME_HISTORY_SIZE = 300   # ~5 min at 1 fps, ~10 s at 30 fps
    AUDIO_HISTORY_MS   = 30_000  # keep last 30 s of audio in RAM

    def __init__(self):
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._closing = False

        # Video buffer — latest frame + ring buffer for stream watching
        self._frame_lock = threading.Lock()
        self._latest_frame = None                          # PIL.Image or None
        # (timestamp_ms: int, PIL.Image) — last FRAME_HISTORY_SIZE frames
        self._frame_history: collections.deque = collections.deque(
            maxlen=self.FRAME_HISTORY_SIZE
        )
        self._screen_width = 0
        self._screen_height = 0

        # Audio buffer — raw PCM s16le chunks from server
        self._audio_lock = threading.Lock()
        # (timestamp_ms: int, codec: AudioCodec, pcm_bytes: bytes)
        self._audio_chunks: collections.deque = collections.deque()
        self._audio_sample_rate = 48_000
        self._audio_channels = 2
        self._audio_codec: Optional[AudioCodec] = None
        self._audio_frame_count = 0

        # Video decoder — lazy import so PIL/av failures are clear
        self._decoder_ctx = None
        self._decoder_codec = None

        # Connection params
        self._host = ""
        self._port = 0
        self._username = ""
        self._password = ""
        self._use_tls = True

        # Event: set once server_hello received (first usable state)
        self._ready = threading.Event()
        self._error: Optional[str] = None

    # ── Public API ────────────────────────────────────────────────

    def connect(self, host: str, port: int, username: str, password: str,
                use_tls: bool = True, timeout: float = 15.0,
                auto_unlock: bool = True,
                ssh_user: str = "", ssh_key: str = "") -> bool:
        """
        Connect to a Teraguchi server.  Blocks until server_hello is received
        or *timeout* seconds elapse.  Returns True on success.

        auto_unlock: if True (default), automatically runs `loginctl
        unlock-session` via SSH when a lock screen is detected after connect.
        ssh_user: SSH username for unlock (defaults to the Teraguchi username).
        ssh_key: path to SSH private key (default: ~/.ssh/id_ed25519).
        """
        if self._thread and self._thread.is_alive():
            self.disconnect()

        self._closing = False
        self._ready.clear()
        self._error = None
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="teraguchi-ai-io",
        )
        self._thread.start()

        self._ready.wait(timeout=timeout)
        if self._error:
            raise ConnectionError(self._error)
        if not self._connected:
            return False

        # PAM mode starts a new Xorg session; capture pipeline needs ~5-8s
        # to start sending frames. Wait here so callers get a ready client.
        frame_deadline = time.time() + 15.0
        while time.time() < frame_deadline:
            with self._frame_lock:
                if self._latest_frame is not None:
                    break
            time.sleep(0.1)

        if auto_unlock and self._connected:
            self._auto_unlock_if_needed(
                ssh_user=ssh_user or username,
                ssh_key=ssh_key or "",
            )

        return self._connected

    def _auto_unlock_if_needed(self, ssh_user: str, ssh_key: str = "") -> None:
        """
        Detect a GNOME lock screen by checking average frame brightness.
        A lock screen is mostly dark background (~10-30 average brightness).
        If detected, SSH in and run `loginctl unlock-session` for all sessions.
        """
        import subprocess
        with self._frame_lock:
            frame = self._latest_frame

        if frame is None:
            return

        # Quick brightness check: lock screen = dark purple gradient, avg ~30-60
        # Desktop with content = much brighter. Threshold: <80 = likely locked.
        import struct
        small = frame.resize((64, 40))
        pixels = list(small.getdata())
        # pixels can be RGB or RGBA tuples, or ints for grayscale
        if pixels and isinstance(pixels[0], (tuple, list)):
            avg = sum(sum(p[:3]) / 3 for p in pixels) / len(pixels)
        else:
            avg = sum(pixels) / len(pixels)

        logger.info("Auto-unlock brightness check: avg=%.1f", avg)
        if avg >= 80:
            # Bright enough - not a lock screen
            return

        logger.info("Lock screen detected (avg brightness %.1f) - unlocking via SSH", avg)
        cmd = ["ssh",
               "-o", "StrictHostKeyChecking=no",
               "-o", "BatchMode=yes",
               "-o", "ConnectTimeout=5"]
        if ssh_key:
            cmd += ["-i", ssh_key]
        cmd += [f"{ssh_user}@{self._host}",
                "for s in $(loginctl list-sessions --no-legend | awk '{print $1}'); do "
                "sudo loginctl unlock-session $s 2>/dev/null; done"]

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=10)
            logger.info("loginctl unlock-session: rc=%d", result.returncode)
            # Give GNOME a moment to dismiss the lock screen
            time.sleep(2)
        except Exception as e:
            logger.warning("Auto-unlock SSH failed: %s", e)

    def disconnect(self):
        """Disconnect and stop the background thread."""
        self._closing = True
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def screen_size(self) -> tuple:
        """Returns (width, height) of the remote display."""
        return (self._screen_width, self._screen_height)

    def screenshot(self) -> Optional[str]:
        """
        Return the latest decoded frame as a base64-encoded PNG string,
        or None if no frame has been received yet.
        """
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            return self._encode_frame(self._latest_frame)

    # ── Streaming / change-detection API ─────────────────────────

    def watch_frames(
        self,
        duration_ms: int = 2000,
        sample_every_ms: int = 500,
    ) -> List[Tuple[int, str]]:
        """
        Collect frames over *duration_ms* milliseconds, sampling one frame
        every *sample_every_ms* ms.  Returns a list of (timestamp_ms, base64_png).

        Frames come from the live ring buffer — no extra decoding needed.
        If the buffer has fewer frames than requested, waits up to duration_ms
        for new ones to arrive.
        """
        results: List[Tuple[int, str]] = []
        deadline = time.time() + duration_ms / 1000.0
        next_sample = time.time()

        while time.time() < deadline:
            now = time.time()
            if now >= next_sample:
                with self._frame_lock:
                    if self._frame_history:
                        ts, img = self._frame_history[-1]
                        results.append((ts, self._encode_frame(img)))
                next_sample = now + sample_every_ms / 1000.0
            time.sleep(0.01)

        return results

    def wait_for_change(
        self,
        timeout_ms: int = 10_000,
        sensitivity: float = 0.02,
    ) -> Optional[Tuple[int, str, float]]:
        """
        Block until a frame differs from the last captured frame by more than
        *sensitivity* (fraction of pixels that changed, 0.0–1.0).

        Returns (timestamp_ms, base64_png, diff_score) or None on timeout.

        sensitivity=0.01 triggers on tiny UI changes (cursor, clock tick).
        sensitivity=0.05 triggers only on significant scene changes.
        sensitivity=0.20 triggers only on major changes (new window, new scene).
        """
        # Grab reference frame
        with self._frame_lock:
            reference = self._latest_frame

        if reference is None:
            # Wait for first frame
            deadline = time.time() + timeout_ms / 1000.0
            while time.time() < deadline:
                time.sleep(0.05)
                with self._frame_lock:
                    if self._latest_frame is not None:
                        reference = self._latest_frame
                        break
            if reference is None:
                return None

        deadline = time.time() + timeout_ms / 1000.0
        ref_arr = self._to_array(reference)

        while time.time() < deadline:
            time.sleep(0.05)
            with self._frame_lock:
                if not self._frame_history:
                    continue
                ts, candidate = self._frame_history[-1]

            if candidate is reference:
                continue

            score = self._frame_diff(ref_arr, candidate)
            if score >= sensitivity:
                return ts, self._encode_frame(candidate), score

            # Update reference to avoid re-triggering on the same small change
            ref_arr = self._to_array(candidate)
            reference = candidate

        return None

    def latest_frame_history(self, count: int = 10) -> List[Tuple[int, str]]:
        """Return the last *count* frames from the ring buffer as (ts_ms, base64_png)."""
        with self._frame_lock:
            frames = list(self._frame_history)[-count:]
        return [(ts, self._encode_frame(img)) for ts, img in frames]

    # ── Frame helpers ─────────────────────────────────────────────

    @staticmethod
    def _encode_frame(img) -> str:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _to_array(img):
        """Convert PIL Image to uint8 numpy array, resized to a small diff resolution."""
        try:
            import numpy as np
            # Downscale for fast diff — 160x90 is plenty for change detection
            small = img.resize((160, 90)).convert("RGB")
            return np.asarray(small, dtype=np.float32)
        except Exception:
            return None

    @staticmethod
    def _frame_diff(ref_arr, candidate) -> float:
        """Return fraction of pixels that changed (0.0–1.0). 0 if numpy unavailable."""
        if ref_arr is None:
            return 0.0
        try:
            import numpy as np
            small = candidate.resize((160, 90)).convert("RGB")
            arr = np.asarray(small, dtype=np.float32)
            diff = np.abs(arr - ref_arr).mean(axis=2)  # per-pixel mean channel diff
            changed = (diff > 10).sum()                 # pixels changed by >10/255
            return float(changed) / diff.size
        except Exception:
            return 0.0

    def send_input(self, msg: dict):
        """Send a JSON input event (mouse/keyboard). Thread-safe."""
        if not self._connected or not self._ws:
            return
        data = json.dumps(msg)
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._ws.send(data), self._loop)

    def click(self, x: float, y: float, button: int = 1, double: bool = False):
        """Click at normalized coordinates (0.0–1.0). button: 1=left 2=mid 3=right."""
        self.send_input({"type": MsgType.MOUSE_MOVE, "x": x, "y": y})
        clicks = 2 if double else 1
        for _ in range(clicks):
            self.send_input({
                "type": MsgType.MOUSE_BUTTON,
                "button": button, "pressed": True, "x": x, "y": y,
            })
            time.sleep(0.03)
            self.send_input({
                "type": MsgType.MOUSE_BUTTON,
                "button": button, "pressed": False, "x": x, "y": y,
            })
            if double:
                time.sleep(0.05)

    def move_mouse(self, x: float, y: float):
        self.send_input({"type": MsgType.MOUSE_MOVE, "x": x, "y": y})

    def drag(self, x1: float, y1: float, x2: float, y2: float,
             steps: int = 20, duration_ms: int = 300, button: int = 1):
        """Click-and-drag from (x1,y1) to (x2,y2) over duration_ms milliseconds."""
        btn_map = {1: "left", 2: "middle", 3: "right"}
        btn_name = btn_map.get(button, "left")
        step_delay = duration_ms / 1000.0 / max(steps, 1)

        # Press at start
        self.send_input({"type": MsgType.MOUSE_BUTTON,
                         "button": btn_name, "pressed": True,
                         "x": x1, "y": y1})
        time.sleep(0.05)

        # Move in steps
        for i in range(1, steps + 1):
            t = i / steps
            x = x1 + (x2 - x1) * t
            y = y1 + (y2 - y1) * t
            self.send_input({"type": MsgType.MOUSE_MOVE, "x": x, "y": y})
            time.sleep(step_delay)

        # Release at end
        self.send_input({"type": MsgType.MOUSE_BUTTON,
                         "button": btn_name, "pressed": False,
                         "x": x2, "y": y2})

    def scroll(self, x: float, y: float, dx: int = 0, dy: int = -3):
        """Scroll at position. dy<0 = scroll up, dy>0 = scroll down."""
        self.send_input({
            "type": MsgType.MOUSE_SCROLL,
            "dx": dx, "dy": dy, "x": x, "y": y,
        })

    def send_key(self, qt_key: int, pressed: bool, modifiers: int = 0):
        """Send a raw Qt key event."""
        self.send_input({
            "type": MsgType.KEY_EVENT,
            "key": "", "scan_code": qt_key,
            "pressed": pressed, "modifiers": modifiers,
        })

    def type_text(self, text: str):
        """Type a string character by character."""
        from ai.keymap import char_to_qt_key, QT_MOD_SHIFT
        for ch in text:
            qt_key, needs_shift = char_to_qt_key(ch)
            if qt_key is None:
                continue
            mods = QT_MOD_SHIFT if needs_shift else 0
            self.send_key(qt_key, True, mods)
            time.sleep(0.02)
            self.send_key(qt_key, False, mods)
            time.sleep(0.01)

    def press_combo(self, combo: str):
        """
        Press a key combination such as "ctrl+c", "alt+F4", "enter", "escape".
        Modifier keys are pressed before the main key and released after.
        """
        from ai.keymap import parse_combo
        modifiers, main_key = parse_combo(combo)

        for mod_key in modifiers:
            self.send_key(mod_key, True)
            time.sleep(0.02)

        self.send_key(main_key, True)
        time.sleep(0.03)
        self.send_key(main_key, False)
        time.sleep(0.02)

        for mod_key in reversed(modifiers):
            self.send_key(mod_key, False)
            time.sleep(0.02)

    # ── Internal: event loop ───────────────────────────────────────

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_receive())
        except Exception as e:
            if not self._closing:
                logger.error("Connection error: %s", e)
                self._error = str(e)
                self._ready.set()
        finally:
            self._connected = False
            # Cancel all pending tasks before closing to suppress
            # "Task was destroyed" and SSL teardown noise from websockets 16.x
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            self._loop.close()
            self._loop = None

    async def _connect_and_receive(self):
        scheme = "wss" if self._use_tls else "ws"
        uri = f"{scheme}://{self._host}:{self._port}"
        logger.info("AI client connecting to %s", uri)

        ssl_context = None
        if self._use_tls:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(
            uri,
            max_size=50 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=30,
            ssl=ssl_context,
        ) as ws:
            self._ws = ws
            logger.info("AI client WebSocket connected")

            first_msg = await ws.recv()
            if isinstance(first_msg, str):
                msg = parse_message(first_msg)
                if msg.get("type") == MsgType.AUTH_REQUEST:
                    await self._handle_auth(ws, msg)
                elif msg.get("type") == MsgType.SERVER_HELLO:
                    self._handle_server_hello(msg)

            # Send client_hello BEFORE signalling ready, so the server
            # starts the capture pipeline and frames begin flowing immediately.
            hello = ClientHelloMsg(
                client_name="teraguchi-ai",
                screen_width=1920,
                screen_height=1080,
            )
            await ws.send(hello.to_json())
            await ws.send(json.dumps({"type": MsgType.REQUEST_FULL_FRAME}))

            self._connected = True
            self._ready.set()   # unblock connect() only after hello is sent

            async for message in ws:
                if self._closing:
                    break
                if isinstance(message, str):
                    self._handle_json(message)
                elif isinstance(message, bytes):
                    self._handle_binary(message)

    async def _handle_auth(self, ws, auth_msg: dict):
        auth_mode = auth_msg.get("auth_mode", "local")
        logger.info("AI client authenticating via %s as '%s'", auth_mode, self._username)

        resp = AuthResponse(
            method="pam" if auth_mode == "pam" else "password",
            username=self._username,
            credential=self._password,
            screen_width=1920,
            screen_height=1080,
        )
        await ws.send(resp.to_json())

        result_raw = await ws.recv()
        if isinstance(result_raw, str):
            result = parse_message(result_raw)
            if result.get("type") == MsgType.AUTH_RESULT:
                if not result.get("success", False):
                    raise ConnectionError(
                        f"Auth failed: {result.get('message', 'Invalid credentials')}"
                    )
                logger.info("AI client authenticated")

            # After auth_result, server immediately sends server_hello
            hello_raw = await ws.recv()
            if isinstance(hello_raw, str):
                hello = parse_message(hello_raw)
                if hello.get("type") == MsgType.SERVER_HELLO:
                    self._handle_server_hello(hello)

    def _handle_server_hello(self, msg: dict):
        self._screen_width = msg.get("screen_width", 1920)
        self._screen_height = msg.get("screen_height", 1080)
        logger.info("AI client server_hello: %dx%d", self._screen_width, self._screen_height)

    def _handle_json(self, data: str):
        try:
            msg = parse_message(data)
            mtype = msg.get("type")
            if mtype == MsgType.SERVER_HELLO:
                self._handle_server_hello(msg)
            elif mtype == MsgType.HEALTH_PING:
                pong = {"type": MsgType.HEALTH_PONG,
                        "timestamp": msg.get("timestamp", 0)}
                self.send_input(pong)
        except Exception as e:
            logger.debug("JSON parse error: %s", e)

    def _handle_binary(self, data: bytes):
        if not data:
            return
        frame_type = data[0]

        if frame_type in (FrameType.VIDEO_H264, FrameType.VIDEO_H265, FrameType.VIDEO_AV1):
            if len(data) < VIDEO_HEADER_SIZE:
                return
            ft, codec, chroma, flags, ts, mon, payload = decode_video_header(data)
            self._decode_video_frame(codec, payload)

        elif frame_type in (FrameType.VIDEO_FULL, FrameType.VIDEO_PARTIAL):
            if len(data) < JPEG_HEADER_SIZE:
                return
            ft, x, y, w, h, payload = decode_jpeg_header(data)
            self._decode_jpeg_frame(payload)

        elif frame_type == FrameType.AUDIO:
            if len(data) < AUDIO_HEADER_SIZE:
                return
            codec, ts, payload = decode_audio_header(data)
            self._store_audio_chunk(codec, ts, payload)

    def _decode_video_frame(self, codec: VideoCodec, payload: bytes):
        """Decode H.264/H.265/AV1 payload into a PIL Image using PyAV."""
        try:
            import av
            from PIL import Image

            codec_name = {
                VideoCodec.H264: "h264",
                VideoCodec.H265: "hevc",
                VideoCodec.AV1:  "av1",
            }.get(codec, "h264")

            if self._decoder_codec != codec_name:
                self._decoder_ctx = av.CodecContext.create(codec_name, "r")
                self._decoder_codec = codec_name

            packet = av.Packet(payload)
            for frame in self._decoder_ctx.decode(packet):
                img = Image.fromarray(frame.to_ndarray(format="rgb24"))
                self._store_frame(img)
                break

        except Exception as e:
            logger.debug("Video decode error: %s", e)

    def _decode_jpeg_frame(self, payload: bytes):
        """Decode a JPEG payload into a PIL Image."""
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(payload))
            img.load()
            self._store_frame(img.convert("RGB"))
        except Exception as e:
            logger.debug("JPEG decode error: %s", e)

    def _store_frame(self, img):
        """Push a decoded frame into the latest slot and the ring buffer."""
        ts = int(time.time() * 1000)
        with self._frame_lock:
            self._latest_frame = img
            self._frame_history.append((ts, img))

    def _store_audio_chunk(self, codec: AudioCodec, ts: int, pcm: bytes):
        """Append a PCM audio chunk to the rolling audio buffer."""
        with self._audio_lock:
            self._audio_codec = codec
            self._audio_frame_count += 1
            self._audio_chunks.append((ts, codec, pcm))
            # Trim old chunks: keep only last AUDIO_HISTORY_MS of audio
            bytes_per_ms = self._audio_sample_rate * self._audio_channels * 2 // 1000
            max_bytes = bytes_per_ms * self.AUDIO_HISTORY_MS
            total = sum(len(c) for _, _, c in self._audio_chunks)
            while self._audio_chunks and total > max_bytes:
                _, _, old = self._audio_chunks.popleft()
                total -= len(old)

    # ── Public audio API ──────────────────────────────────────────

    def audio_info(self) -> dict:
        """Return info about the incoming audio stream."""
        with self._audio_lock:
            codec_name = self._audio_codec.name if self._audio_codec else "none"
            chunk_count = len(self._audio_chunks)
            total_bytes = sum(len(c) for _, _, c in self._audio_chunks)
        bytes_per_ms = self._audio_sample_rate * self._audio_channels * 2 // 1000
        buffered_ms = total_bytes // bytes_per_ms if bytes_per_ms else 0
        return {
            "codec": codec_name,
            "sample_rate": self._audio_sample_rate,
            "channels": self._audio_channels,
            "frames_received": self._audio_frame_count,
            "buffered_chunks": chunk_count,
            "buffered_ms": buffered_ms,
            "receiving": self._audio_frame_count > 0,
        }

    def listen_audio(self, duration_ms: int = 3000) -> Optional[bytes]:
        """
        Record audio from the remote session for *duration_ms* milliseconds.
        Returns WAV-encoded bytes (PCM s16le, 48 kHz, stereo), or None if no
        audio has been received.

        Waits up to duration_ms + 2000 ms for audio to arrive if the buffer
        is empty at call time.
        """
        # Wait for audio to start arriving if not yet
        deadline = time.time() + duration_ms / 1000.0 + 2.0
        with self._audio_lock:
            already_have = self._audio_frame_count > 0

        if not already_have:
            while time.time() < deadline:
                time.sleep(0.1)
                with self._audio_lock:
                    if self._audio_frame_count > 0:
                        break

        # Collect duration_ms worth of new chunks
        start = time.time()
        collected: list[bytes] = []
        end = time.time() + duration_ms / 1000.0
        last_seen = 0

        while time.time() < end:
            time.sleep(0.02)
            with self._audio_lock:
                new = [(ts, p) for ts, _, p in self._audio_chunks if ts > last_seen]
            for ts, pcm in new:
                collected.append(pcm)
                last_seen = ts

        if not collected:
            return None

        raw = b"".join(collected)
        return self._encode_wav(raw)

    def _encode_wav(self, pcm_s16le: bytes) -> bytes:
        """Wrap raw PCM s16le bytes in a WAV container."""
        import struct as _struct
        sr = self._audio_sample_rate
        ch = self._audio_channels
        bits = 16
        byte_rate = sr * ch * bits // 8
        block_align = ch * bits // 8
        data_size = len(pcm_s16le)
        header = _struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + data_size, b"WAVE",
            b"fmt ", 16, 1, ch, sr, byte_rate, block_align, bits,
            b"data", data_size,
        )
        return header + pcm_s16le
