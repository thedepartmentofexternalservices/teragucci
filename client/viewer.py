"""
Remote desktop viewer widget with full pen/tablet pressure support.

Uses PySide6's QTabletEvent for Wacom pen pressure, tilt, and rotation
on both macOS and Windows. Falls back to mouse events for standard mice.

Phase 2 D-02 addition: ``VideoBlitWidget`` is a QRhiWidget subclass that
uploads decoded P010 Y / UV planes as ``QRhiTexture.Format.R16`` and
``QRhiTexture.Format.RG16`` and runs a BT.709 video-range fragment
shader for 10-bit YUV->RGB conversion. The class lives in this file
alongside RemoteViewer so the video layer + overlays compose cleanly:
RemoteViewer's QPainter ``paintEvent`` continues to own cursor + health
overlay rendering; the VideoBlitWidget owns ONLY the video layer. See
``client/shaders/video_blit.{vert,frag}.qsb`` for the baked shaders and
``scripts/build_shaders.sh`` for the bake pipeline.

Escape hatch: if the QRhi/Metal path misbehaves on a given GPU, set
the env var ``TERAGUCHI_LEGACY_GL_BLIT=1`` (or pass ``--legacy-gl-blit``
on the CLI -- threaded through ``client/app.py``) to fall back to the
OpenGL backend with the same shader pipeline.
"""

from __future__ import annotations

import logging
import os
import pathlib
import struct
import sys
from typing import Optional

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, Signal

# QRhi types live in PySide6.QtGui (not QtWidgets). Imported eagerly so
# the VideoBlitWidget class definition below is self-contained; PySide6
# 6.10+ is the floor per Phase 1 STACK.md so these symbols are always
# present in production environments.
from PySide6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QInputMethodEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QRhiBuffer,
    QRhiGraphicsPipeline,
    QRhiSampler,
    QRhiShaderResourceBinding,
    QRhiShaderResourceBindings,
    QRhiShaderStage,
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
    QRhiVertexInputAttribute,
    QRhiVertexInputBinding,
    QRhiVertexInputLayout,
    QShader,
    QTabletEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QRhiWidget, QWidget

# Phase 2 WR-08: wire-format modifier bit constants live in
# common/keymap.py so the encoder + decoder + viewer key-handlers all
# reference the same source of truth instead of magic numbers.
from common.keymap import (
    MODIFIER_BIT_ALT,
    MODIFIER_BIT_CTRL,
    MODIFIER_BIT_KEYPAD,
    MODIFIER_BIT_META,
    MODIFIER_BIT_SHIFT,
)

logger = logging.getLogger(__name__)


# --- Phase 2 D-02: VideoBlitWidget (QRhiWidget) ---
#
# Module-level constants for the BT.709 10-bit YUV->RGB shader pipeline.
# Kept at module scope so they're trivially inspectable / unit-testable
# without instantiating the widget.

_SHADER_DIR = pathlib.Path(__file__).resolve().parent / "shaders"
_VERT_QSB_NAME = "video_blit.vert.qsb"
_FRAG_QSB_NAME = "video_blit.frag.qsb"

# Full-screen quad: 4 vertices in a triangle-strip (bottom-left,
# bottom-right, top-left, top-right). Each vertex is (x, y, u, v) so
# 4 floats per vertex, 16 floats total. Y-flipped UV (v=1 at the
# bottom, v=0 at the top) so the texture coordinate system matches the
# Qt/image origin (top-left) on top of Metal's bottom-left NDC.
_QUAD_VERTICES_BYTES = struct.pack(
    "16f",
    -1.0, -1.0, 0.0, 1.0,   # pos0, uv0
     1.0, -1.0, 1.0, 1.0,   # pos1, uv1
    -1.0,  1.0, 0.0, 0.0,   # pos2, uv2
     1.0,  1.0, 1.0, 0.0,   # pos3, uv3
)


def _load_qsb(name: str) -> QShader:
    """Load a baked .qsb shader binary from client/shaders/.

    .qsb files are produced by ``scripts/build_shaders.sh`` (which calls
    ``pyside6-qsb --qt6``) and committed alongside their GLSL sources.
    The CI macos-14 job re-bakes and ``git diff --exit-code`` fails on
    drift (T-02-19 mitigation in the threat register).
    """
    data = (_SHADER_DIR / name).read_bytes()
    shader = QShader.fromSerialized(QByteArray(data))
    return shader


def _legacy_gl_blit_requested() -> bool:
    """True when the user has opted into the OpenGL escape-hatch path.

    Two trigger surfaces: (a) env var ``TERAGUCHI_LEGACY_GL_BLIT`` set
    to a truthy value, or (b) ``--legacy-gl-blit`` present on the
    process argv (threaded through from client/app.py argparse). Either
    flips ``VideoBlitWidget`` from its default Metal/Vulkan/D3D backend
    to the OpenGL backend with the same shader pipeline.
    """
    env = os.environ.get("TERAGUCHI_LEGACY_GL_BLIT", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if "--legacy-gl-blit" in sys.argv:
        return True
    return False


class VideoBlitWidget(QRhiWidget):
    """Phase 2 D-02: 10-bit P010 video blit with BT.709 YUV->RGB Metal shader.

    This widget OWNS the video layer only. Cursor + health-overlay
    rendering stays in ``RemoteViewer.paintEvent`` (QPainter on top of
    QImage), per the plan's "do not put video + overlay + cursor all
    through the Metal path" anti-pattern (RESEARCH.md Anti-Patterns).

    Texture format choice:
      * Y plane  -> ``QRhiTexture.Format.R16`` — 16-bit single-channel,
        holds 10-bit value left-shifted into the top 10 bits of the
        16-bit container (P010 layout). Sampling normalizes to [0,1]
        and the BT.709 video-range constants in the shader expect that.
      * UV plane -> ``QRhiTexture.Format.RG16`` — interleaved 4:2:0
        chroma at half horizontal and vertical resolution.

    Backend selection:
      * Default:  Metal on macOS, OpenGL elsewhere (Qt 6.10 picks a
        sensible platform default when ``setApi`` isn't called).
      * Override: ``TERAGUCHI_LEGACY_GL_BLIT=1`` env var or
        ``--legacy-gl-blit`` CLI arg forces ``Api.OpenGL`` everywhere.
        Same shader pipeline applies — pyside6-qsb's ``--qt6`` mode
        bakes GLSL/HLSL/MSL variants from the same source file.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # Backend selection — flip to OpenGL when the escape hatch is on,
        # otherwise pick Metal explicitly on macOS (the documented v1
        # production path) and let Qt's platform-default kick in elsewhere.
        if _legacy_gl_blit_requested():
            self.setApi(QRhiWidget.Api.OpenGL)
        elif sys.platform == "darwin":
            self.setApi(QRhiWidget.Api.Metal)
        # else: Qt picks the platform default (OpenGL on Linux, D3D11/12
        # on Windows). Phase 2 ships Mac-only client; non-Mac is best-effort.

        # Resource handles — created in initialize() once the QRhi
        # backend is live. Keep them as attributes so the unit tests in
        # tests/client/test_viewer_qrhi_video_layer.py can introspect
        # the texture formats without touching the GPU.
        self._tex_y: Optional[QRhiTexture] = None
        self._tex_uv: Optional[QRhiTexture] = None
        self._sampler: Optional[QRhiSampler] = None
        self._pipeline: Optional[QRhiGraphicsPipeline] = None
        self._srb: Optional[QRhiShaderResourceBindings] = None
        self._vbuf: Optional[QRhiBuffer] = None
        self._vbuf_uploaded = False

        # Latched frame state. ``feed_frame`` latches the latest plane
        # buffers under the GIL; render() consumes on the GUI thread.
        # No additional locking needed because Qt's update() coalesces
        # repaints onto the main thread.
        self._latest_y: Optional[bytes] = None
        self._latest_uv: Optional[bytes] = None
        self._frame_size = QSize(1920, 1080)
        self._frame_size_dirty = False

    # ---- Public API ------------------------------------------------

    def feed_frame(self, y_bytes: bytes, uv_bytes: bytes,
                   width: int, height: int) -> None:
        """Latch the latest decoded P010 planes for the next render().

        Called from the decoder thread once the PyAV decode handoff is
        wired (Phase 5+ networking work — this method exists today as
        the documented interface). ``width`` and ``height`` are the Y
        plane dimensions in pixels; UV plane is half-res in both
        dimensions for 4:2:0.
        """
        self._latest_y = y_bytes
        self._latest_uv = uv_bytes
        new_size = QSize(width, height)
        if new_size != self._frame_size:
            self._frame_size = new_size
            self._frame_size_dirty = True
            # Force texture re-creation on the next render() pass
            self._tex_y = None
            self._tex_uv = None
        self.update()  # schedules a render() on the GUI thread

    # ---- QRhiWidget overrides -------------------------------------

    def initialize(self, cb) -> None:  # noqa: N802 — Qt API name
        """Create / re-create QRhi resources.

        Called by Qt when the widget is first shown, when the surface
        is recreated (e.g. backend switch), and when the widget is
        resized in a way that invalidates the render target. Safe to
        re-enter; we recreate resources only when the frame size has
        changed or they were never created. Delegates to
        ``_create_resources`` so unit tests can drive the same path
        with an explicit Null-backend QRhi (no Qt platform plugin
        needed).
        """
        rhi = self.rhi()
        if rhi is None:
            return
        self._create_resources(rhi, with_pipeline=True)

    def _create_resources(self, rhi, with_pipeline: bool = True) -> None:
        """Idempotent resource setup driven by the supplied QRhi.

        Extracted so tests can call the same code path with a directly
        constructed ``QRhi.create(QRhi.Implementation.Null, ...)``
        without standing up a real Qt platform/window. ``with_pipeline``
        is False in tests that only need to assert texture-format
        invariants — graphics-pipeline creation requires a render-pass
        descriptor which the Null backend does not surface here.
        """
        # --- Y plane texture -- QRhiTexture.Format.R16 -- 10-bit-in-16
        if self._tex_y is None:
            self._tex_y = rhi.newTexture(
                QRhiTexture.Format.R16, self._frame_size, 1
            )
            self._tex_y.create()
            # Phase 2 WR-05: textures were just (re)created — the SRB's
            # baked-in sampledTexture bindings now reference the new
            # handle, but the SRB binding list was built off the OLD
            # one. Force the SRB to be rebuilt below.
            self._srb = None

        # --- UV plane texture -- QRhiTexture.Format.RG16 -- 4:2:0 half-res
        if self._tex_uv is None:
            uv_size = QSize(
                max(1, self._frame_size.width() // 2),
                max(1, self._frame_size.height() // 2),
            )
            self._tex_uv = rhi.newTexture(
                QRhiTexture.Format.RG16, uv_size, 1
            )
            self._tex_uv.create()
            # Phase 2 WR-05: see note above — invalidate stale SRB.
            self._srb = None

        # --- Sampler (linear filter, clamp-to-edge -- standard for video) ---
        if self._sampler is None:
            self._sampler = rhi.newSampler(
                QRhiSampler.Filter.Linear,
                QRhiSampler.Filter.Linear,
                QRhiSampler.Filter.None_,
                QRhiSampler.AddressMode.ClampToEdge,
                QRhiSampler.AddressMode.ClampToEdge,
            )
            self._sampler.create()

        # --- Shader Resource Bindings (textures bind at slots 1, 2;
        #     matches the layout(binding = N) declarations in the shader) ---
        if self._srb is None:
            self._srb = rhi.newShaderResourceBindings()
            self._srb.setBindings([
                QRhiShaderResourceBinding.sampledTexture(
                    1,
                    QRhiShaderResourceBinding.StageFlag.FragmentStage,
                    self._tex_y, self._sampler,
                ),
                QRhiShaderResourceBinding.sampledTexture(
                    2,
                    QRhiShaderResourceBinding.StageFlag.FragmentStage,
                    self._tex_uv, self._sampler,
                ),
            ])
            self._srb.create()

        # --- Vertex buffer (immutable full-screen quad) ---
        if self._vbuf is None:
            self._vbuf = rhi.newBuffer(
                QRhiBuffer.Type.Immutable,
                QRhiBuffer.UsageFlag.VertexBuffer,
                len(_QUAD_VERTICES_BYTES),
            )
            self._vbuf.create()
            self._vbuf_uploaded = False  # uploaded on first render()

        if not with_pipeline:
            return

        # --- Graphics pipeline (vertex + fragment + vertex layout) ---
        if self._pipeline is None:
            try:
                vert = _load_qsb(_VERT_QSB_NAME)
                frag = _load_qsb(_FRAG_QSB_NAME)
            except FileNotFoundError as exc:
                logger.error(
                    "VideoBlitWidget: baked shaders missing (%s). "
                    "Run scripts/build_shaders.sh.", exc,
                )
                return

            self._pipeline = rhi.newGraphicsPipeline()
            self._pipeline.setShaderStages([
                QRhiShaderStage(QRhiShaderStage.Type.Vertex, vert),
                QRhiShaderStage(QRhiShaderStage.Type.Fragment, frag),
            ])
            # Vertex input: one binding (the quad VB), two attributes
            # (pos2 + uv2) packed as 4 floats per vertex.
            stride = 4 * 4  # 4 floats * 4 bytes each
            v_layout = QRhiVertexInputLayout()
            v_layout.setBindings([QRhiVertexInputBinding(stride)])
            v_layout.setAttributes([
                # location 0: pos.xy at offset 0
                QRhiVertexInputAttribute(
                    0, 0, QRhiVertexInputAttribute.Format.Float2, 0,
                ),
                # The fragment shader derives UV from pos.xy itself
                # (v_uv = position.xy * 0.5 + 0.5) so we don't need a
                # separate UV attribute. Keeping a single Float2 input
                # matches the layout(location = 0) declaration in the
                # vertex shader and keeps the pipeline minimal.
            ])
            self._pipeline.setVertexInputLayout(v_layout)
            self._pipeline.setShaderResourceBindings(self._srb)
            # Use the widget's own render target's render-pass
            # descriptor — required so the pipeline is compatible with
            # the framebuffer Qt hands us each frame.
            self._pipeline.setRenderPassDescriptor(
                self.renderTarget().renderPassDescriptor()
            )
            # Triangle strip topology matches the 4-vertex quad above.
            self._pipeline.setTopology(
                QRhiGraphicsPipeline.Topology.TriangleStrip
            )
            self._pipeline.create()

    def render(self, cb) -> None:  # noqa: N802 — Qt API name
        """Upload the latest planes + draw the BT.709 blit pass.

        Called by Qt every frame after ``update()`` is requested.
        Short-circuits if no frame has been latched yet (initial render
        before the decoder hand-off).
        """
        rhi = self.rhi()
        if rhi is None or self._pipeline is None:
            return

        target = self.renderTarget()
        if target is None:
            return

        # Re-create textures if the frame size changed (initialize()
        # handles the create; we just need to call it again).
        if self._tex_y is None or self._tex_uv is None or self._frame_size_dirty:
            self.initialize(cb)
            self._frame_size_dirty = False

        # Build a resource update batch -- vertex buffer (one-shot) +
        # texture plane uploads (every frame when a new frame has been
        # latched).
        batch = rhi.nextResourceUpdateBatch()

        if not self._vbuf_uploaded and self._vbuf is not None:
            batch.uploadStaticBuffer(self._vbuf, _QUAD_VERTICES_BYTES)
            self._vbuf_uploaded = True

        if self._latest_y is not None and self._tex_y is not None:
            y_sub = QRhiTextureSubresourceUploadDescription(self._latest_y)
            # P010: 2 bytes per sample on the Y plane.
            y_sub.setDataStride(self._frame_size.width() * 2)
            y_entry = QRhiTextureUploadEntry(0, 0, y_sub)
            batch.uploadTexture(
                self._tex_y, QRhiTextureUploadDescription([y_entry])
            )

        if self._latest_uv is not None and self._tex_uv is not None:
            uv_sub = QRhiTextureSubresourceUploadDescription(self._latest_uv)
            # UV plane (interleaved): 4 bytes per pixel-pair (RG16),
            # half-width relative to Y.
            uv_sub.setDataStride((self._frame_size.width() // 2) * 4)
            uv_entry = QRhiTextureUploadEntry(0, 0, uv_sub)
            batch.uploadTexture(
                self._tex_uv, QRhiTextureUploadDescription([uv_entry])
            )

        # Begin the render pass against the widget's framebuffer. Clear
        # to opaque black so the first frames before any video has
        # arrived render as black rather than uninitialized memory.
        cb.beginPass(target, QColor(0, 0, 0, 255), None, batch)
        if self._vbuf is not None:
            cb.setGraphicsPipeline(self._pipeline)
            cb.setShaderResources(self._srb)
            cb.setVertexInput(0, [(self._vbuf, 0)])
            cb.draw(4)  # triangle-strip => 1 quad
        cb.endPass()


# --- RemoteViewer (existing widget, untouched video-layer behavior) ---


class RemoteViewer(QWidget):
    """
    Widget that displays the remote desktop and captures all input events
    including pen/stylus with pressure sensitivity.
    """

    # Signals for input events (emitted to be picked up by the protocol layer).
    #
    # Phase 3 D-05 — mouse signals carry the server-physical-pixel coords
    # alongside the legacy normalized floats. Arity went from 2/4/4 to
    # 4/6/6 (x_norm, y_norm, server_x_px, server_y_px). ``pen_event`` is
    # a dict payload, so ``server_x`` / ``server_y`` join the dict keys
    # without breaking the signal shape.
    mouse_moved = Signal(float, float, int, int)  # x_norm, y_norm, server_x, server_y
    mouse_button_changed = Signal(int, bool, float, float, int, int)  # button, pressed, x, y, sx, sy
    mouse_scrolled = Signal(int, int, float, float, int, int)  # dx, dy, x, y, sx, sy
    key_changed = Signal(int, int, bool, int)  # qt_key, scan_code, pressed, modifiers
    pen_event = Signal(dict)  # Full pen event data (D-05: dict carries server_x/server_y)
    request_full_frame = Signal()
    paste_requested = Signal()  # Ctrl+V or Cmd+V detected — push clipboard
    files_dropped = Signal(list)  # list of file paths dropped onto viewer
    # Phase 2 D-11 — release-all-modifiers trigger.
    # Reason values: "focus_out" (focusOutEvent), "panic_f9" (F9 panic
    # shortcut), "reconnect" (ConnectionSupervisor post-auth hook —
    # emitted from connection_supervisor.py, not from this widget),
    # "periodic" (server-side timer — does not pass through this signal).
    # Plumbed through client/session.py::_wire_viewer to ClientProtocol
    # which serializes a KeyResetModifiersMsg(reason=...) on the wire.
    reset_modifiers_requested = Signal(str)
    # Phase 2 D-15 — IME / dead-key commit string passthrough. Fired from
    # inputMethodEvent when commitString() is non-empty. Empty (preedit-
    # only) events are suppressed because synthesizing them as keycodes
    # would mangle dead-key composition (the anti-pattern that REQ-INPUT-05
    # exists to forbid).
    text_commit = Signal(str)
    # Phase 2 D-19 — pen-proximity re-synth on focusIn / showEvent. Emitted
    # as a dict {"in_proximity": bool, "pen_type": "pen"|"eraser"} so the
    # protocol layer can serialize a PenProximityMsg without this widget
    # importing common/messages. Idempotent on the server PenFSM —
    # duplicate emissions after Cmd-Tab / lockscreen / restore cycles are
    # safe by construction (D-19 PITFALLS #3 mitigation).
    pen_proximity = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Remote screen dimensions (updated on server hello)
        self._remote_width = 1920
        self._remote_height = 1080

        # The current remote screen image
        self._screen_image: Optional[QImage] = None
        self._pixmap: Optional[QPixmap] = None

        # Display scaling
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._offset_x = 0
        self._offset_y = 0

        # Stretch mode: False = preserve aspect ratio (correct geometry)
        self._stretch_fill = False

        # Monitor crop: list of monitor dicts with x, y, width, height
        # When set, only these regions of the full frame are shown (stitched side by side)
        self._monitor_regions: list = []  # empty = show everything
        # Computed composite dimensions (sum of selected monitors)
        self._composite_w = 0
        self._composite_h = 0

        # Track whether we're using pen or mouse to avoid duplicate events
        self._pen_active = False

        # Phase 2 D-19 — pen-proximity re-synth bookkeeping. ``True`` while
        # the pen has been in tablet range; flipped on TabletLeaveProximity.
        # Read by ``focusInEvent`` to decide whether to synthesize a
        # PenProximityMsg(in_proximity=True) on focus return — we only re-
        # sync when the pen *was* in-proximity before focus loss, so a
        # fresh-focus where the user never touched the tablet doesn't
        # spam the wire (D-19 wire-traffic guard).
        self._pen_was_in_proximity = False
        # Last observed pen type — "pen" or "eraser". Echoed back in the
        # re-synth PenProximityMsg so the server's pen FSM can route to
        # the right device shape after focus return.
        self._last_pen_type = "pen"

        # macOS transforms Control+LeftClick into a RightButton event at
        # the OS level BEFORE Qt sees it. When the user is doing a
        # Ctrl+drag gesture (common in Flame navigation), we need to
        # translate that back to a LeftButton click so Flame sees
        # Ctrl+LeftClick instead of a right-click. We track this across
        # the press→move→release sequence so the release also swaps.
        self._mac_ctrl_click_swap = False

        # Enable tablet tracking for hover events
        self.setTabletTracking(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        # Accept all input
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.setAttribute(Qt.WA_TabletTracking, True)

        # Accept file drag-and-drop
        self.setAcceptDrops(True)

        # Cursor state. We render Flame's real cursor LOCALLY using
        # shape updates shipped out-of-band from the server (via
        # XFixesGetCursorImage on the Linux side). That gives us
        # zero-latency cursor motion — PCoIP/RDP/VNC/Parsec all do
        # this. Start blank so we don't show the macOS arrow in the
        # millisecond between connect and the first cursor_update.
        self._remote_cursor_serial: int = -1
        self.setCursor(Qt.BlankCursor)

        # --- Phase 3 D-05 / D-06 / D-07 -----------------------------
        #
        # Per-screen devicePixelRatio cache. Refreshed in showEvent via
        # ``_current_screen_dpr`` and on every QWindow::screenChanged
        # signal via ``_on_screen_changed``. Used by _widget_to_remote
        # (D-05) and the F12 dev overlay (D-07) to display the actual
        # DPR of the widget's current QScreen — NOT the primary's
        # (Pitfall 1 root cause).
        self._current_dpr: float = 1.0

        # Phase 3 D-07 / Pitfall 8 — F12 coord debug overlay. Gated on
        # TERAGUCHI_DEBUG=1 at HANDLER-INSTALL time (per research
        # Pitfall 8 — gate handler, not visibility). Release builds
        # leave ``_coord_overlay`` as None and F12 keyPressEvent falls
        # through to the existing paste-detect + key_changed.emit path.
        self._coord_overlay = None
        if os.environ.get("TERAGUCHI_DEBUG") == "1":
            # Lazy import so production builds never touch the module
            # even by side-effect.
            from client.coord_debug_overlay import CoordDebugOverlay
            self._coord_overlay = CoordDebugOverlay(self)
            self._coord_overlay.reposition()

        logger.info("RemoteViewer initialized")

    def set_remote_size(self, width: int, height: int):
        """Set the remote screen dimensions."""
        self._remote_width = width
        self._remote_height = height
        self._screen_image = QImage(width, height, QImage.Format_RGB888)
        self._screen_image.fill(Qt.black)
        self._update_scaling()
        logger.info("Remote screen size: %dx%d", width, height)

    def set_monitor_regions(self, regions: list):
        """Set which monitor regions to display from the full frame.

        Args:
            regions: list of dicts with x, y, width, height (pixel coords in
                     the full virtual desktop). Empty list = show everything.
        """
        self._monitor_regions = regions
        if regions:
            # Composite: monitors laid out side by side
            self._composite_w = sum(r["width"] for r in regions)
            self._composite_h = max(r["height"] for r in regions)
        else:
            self._composite_w = 0
            self._composite_h = 0
        self._pixmap = None
        self._update_scaling()
        self.update()

    def set_remote_cursor(self, serial: int, width: int, height: int,
                          hot_x: int, hot_y: int, rgba_bytes: bytes):
        """Replace the viewer's cursor with a new shape from the server.

        ``rgba_bytes`` is RGBA8888 with premultiplied alpha (that's
        what XFixes produces). Qt's ``Format_RGBA8888_Premultiplied``
        handles the compositing correctly.
        """
        if serial == self._remote_cursor_serial:
            return
        if width <= 0 or height <= 0:
            return
        expected = width * height * 4
        if len(rgba_bytes) != expected:
            logger.warning("Cursor payload size mismatch: got %d, expected %d",
                           len(rgba_bytes), expected)
            return

        img = QImage(rgba_bytes, width, height, width * 4,
                     QImage.Format_RGBA8888_Premultiplied)
        if img.isNull():
            return
        # Qt doesn't copy the backing bytes unless we ask it to — the
        # rgba_bytes lifetime ends when this function returns, so we
        # must detach a copy before handing it to QCursor.
        pix = QPixmap.fromImage(img.copy())
        if pix.isNull():
            return
        cursor = QCursor(pix, hot_x, hot_y)
        self.setCursor(cursor)
        self._remote_cursor_serial = serial

    def update_full_frame(self, jpeg_data: bytes):
        """Update the entire screen from JPEG data."""
        img = QImage()
        img.loadFromData(jpeg_data, "JPEG")
        if img.isNull():
            logger.warning("Failed to decode full frame JPEG")
            return
        self._screen_image = img.convertToFormat(QImage.Format_RGB888)
        self._pixmap = None  # Invalidate cache
        self.update()

    def update_partial_frame(self, x: int, y: int, w: int, h: int, jpeg_data: bytes):
        """Update a rectangular region of the screen from JPEG data."""
        if self._screen_image is None:
            return

        region = QImage()
        region.loadFromData(jpeg_data, "JPEG")
        if region.isNull():
            logger.warning("Failed to decode partial frame JPEG")
            return

        painter = QPainter(self._screen_image)
        painter.drawImage(x, y, region)
        painter.end()
        self._pixmap = None  # Invalidate cache
        self.update()

    def _update_scaling(self):
        """Recalculate display scaling to fit remote screen in widget."""
        # Use composite dimensions if monitor regions are selected
        if self._monitor_regions:
            src_w = self._composite_w
            src_h = self._composite_h
        else:
            src_w = self._remote_width
            src_h = self._remote_height

        if src_w == 0 or src_h == 0:
            return

        widget_w = self.width()
        widget_h = self.height()

        # Always maintain aspect ratio
        src_aspect = src_w / src_h
        widget_aspect = widget_w / widget_h

        if widget_aspect > src_aspect:
            display_h = widget_h
            display_w = int(display_h * src_aspect)
        else:
            display_w = widget_w
            display_h = int(display_w / src_aspect)

        self._scale_x = display_w / src_w
        self._scale_y = display_h / src_h
        self._offset_x = (widget_w - display_w) // 2
        self._offset_y = (widget_h - display_h) // 2

    def _widget_to_remote(self, x: float, y: float) -> tuple:
        """Phase 3 D-05 — widget coords → (rx_norm, ry_norm, server_x, server_y).

        Returns a 4-tuple:
          * ``rx_norm`` / ``ry_norm``: float in [0.0, 1.0] — Phase 1/2
            wire-compat normalized coords. Older servers consume these.
          * ``server_x`` / ``server_y``: int in server physical pixels.
            D-05 preferred wire path — server consumers pick these up
            when ``server_x >= 0`` and fall back to the normalized
            floats otherwise.

        When monitor regions are active, maps through the composite
        layout back to full virtual desktop coordinates so the remote
        input-injector (XTest / CGEvent / uinput) moves the cursor to
        the correct pixel.

        Integer server_x / server_y are NOT clamped to [0, width-1];
        the composite past-last-monitor edge case can produce values
        outside the visible bounds, and server-side input_injector
        enforces the final clamp (T-03-15 defense-in-depth per the
        plan's threat register).
        """
        if not self._monitor_regions:
            # Simple: widget → full remote desktop
            rx = (x - self._offset_x) / (self._scale_x * self._remote_width)
            ry = (y - self._offset_y) / (self._scale_y * self._remote_height)
            rx = max(0.0, min(1.0, rx))
            ry = max(0.0, min(1.0, ry))
            sx = int(round(rx * self._remote_width))
            sy = int(round(ry * self._remote_height))
            return rx, ry, sx, sy

        # Composite mode: find which monitor the click is in
        # Convert widget coords to composite pixel coords
        cx = (x - self._offset_x) / self._scale_x
        cy = (y - self._offset_y) / self._scale_y

        # Walk through monitors (laid out side by side)
        composite_x = 0
        for region in self._monitor_regions:
            rw = region["width"]
            rh = region["height"]
            if cx < composite_x + rw:
                # Click is in this monitor
                local_x = cx - composite_x
                local_y = cy
                # Map back to full virtual desktop (server physical px).
                desktop_x = region["x"] + local_x
                desktop_y = region["y"] + local_y
                rx = max(0.0, min(1.0, desktop_x / self._remote_width))
                ry = max(0.0, min(1.0, desktop_y / self._remote_height))
                sx = int(round(desktop_x))
                sy = int(round(desktop_y))
                return rx, ry, sx, sy
            composite_x += rw

        # Past the last monitor — clamp to last monitor's right edge
        last = self._monitor_regions[-1]
        sx = last["x"] + last["width"] - 1
        sy = int(round(cy))
        rx = max(0.0, min(1.0, sx / self._remote_width))
        ry = max(0.0, min(1.0,
                         cy / self._remote_height if self._remote_height else 0))
        return rx, ry, sx, sy

    # --- Phase 3 D-06: per-screen DPR helpers + screenChanged hook ---

    def _current_screen_dpr(self) -> float:
        """D-06 — return DPR of the QScreen the viewer widget is currently on.

        Always look up the CURRENT screen's DPR — never cache the
        primary's (Pitfall 1 / Mozilla bz #794038). Re-evaluated on
        QWindow.screenChanged via ``_on_screen_changed``. Falls back to
        ``devicePixelRatioF()`` when ``windowHandle()`` is None
        (pre-show / offscreen platform edge cases).
        """
        win = self.window().windowHandle() if self.window() else None
        if win is None:
            return float(self.devicePixelRatioF())
        screen = win.screen()
        if screen is None:
            return float(self.devicePixelRatioF())
        return float(screen.devicePixelRatio())

    def _current_screen_name(self) -> str:
        """D-07 — return the QScreen.name() for the F12 overlay header.

        Defensive None-guards: pre-show / offscreen / stubbed widget →
        returns "unknown" instead of raising.
        """
        try:
            win = self.window().windowHandle() if self.window() else None
            if win is None:
                return "unknown"
            screen = win.screen()
            if screen is None:
                return "unknown"
            return str(screen.name())
        except Exception:
            return "unknown"

    def _current_monitor_under_widget(self, x: float, y: float) -> Optional[dict]:
        """D-07 — find the server monitor region under widget coord (x, y).

        Used by the F12 overlay to display `monitor: NAME WxH+X+Y`.
        Returns the first matching region dict from self._monitor_regions,
        or None when composite mode is inactive / coords fall past the
        last monitor.
        """
        if not self._monitor_regions:
            return None
        cx = (x - self._offset_x) / self._scale_x if self._scale_x else 0
        composite_x = 0
        for region in self._monitor_regions:
            rw = region["width"]
            if cx < composite_x + rw:
                return region
            composite_x += rw
        return None

    def _connect_screen_changed(self):
        """Wire QWindow.screenChanged → _on_screen_changed after showEvent.

        windowHandle() is None until Qt creates the native window, so
        this must be called from showEvent (not __init__). Idempotent:
        safe to call multiple times — disconnects any prior connection
        before reconnecting.
        """
        win = self.window().windowHandle() if self.window() else None
        if win is None:
            return
        try:
            win.screenChanged.disconnect(self._on_screen_changed)
        except (TypeError, RuntimeError):
            # No existing connection, or the underlying C++ object is
            # gone. Both are benign — proceed to (re)connect.
            pass
        try:
            win.screenChanged.connect(self._on_screen_changed)
        except (TypeError, RuntimeError):
            pass

    def _on_screen_changed(self, screen):
        """D-06 — recompute scaling cache when viewer migrates between screens.

        Symmetric to Phase 2 D-19 focusIn pen-proximity re-synth
        (Pitfall 2): re-emit ``pen_proximity`` if the pen was in range
        so the server PenFSM doesn't lose state across the screen
        change. Idempotent on the server side (PenFSM accepts duplicate
        enter_proximity transitions).
        """
        old_dpr = getattr(self, "_current_dpr", 1.0)
        self._current_dpr = self._current_screen_dpr()
        self._update_scaling()
        screen_name = screen.name() if screen is not None and hasattr(
            screen, "name") else "unknown"
        if old_dpr != self._current_dpr:
            logger.info(
                "viewer.screen_changed old_dpr=%s new_dpr=%s screen=%s",
                old_dpr, self._current_dpr, screen_name,
            )
        # Pitfall 2 — re-synth pen proximity on screen migration via the
        # canonical Phase 2 D-19 helper. Same shape as focusInEvent /
        # showEvent so the server PenFSM sees a consistent proximity
        # envelope across all re-synth triggers.
        if getattr(self, "_pen_was_in_proximity", False):
            self._emit_pen_proximity(
                in_proximity=True,
                pen_type=getattr(self, "_last_pen_type", "pen"),
            )
        # D-07 — refresh F12 overlay metadata when the screen changes
        # while the overlay is on. Coords stay the last-known widget
        # coord; dpr + screen name refresh on the next paintEvent.
        if self._coord_overlay is not None and self._coord_overlay.isVisible():
            self._coord_overlay.update_screen(self._current_dpr, screen_name)

    # --- Paint ---
    #
    # Phase 2 D-02 boundary: this paintEvent owns the LEGACY JPEG-frame
    # update path (``update_full_frame`` / ``update_partial_frame``) and
    # the cursor / overlay layers. The 10-bit HEVC P010 video path lives
    # in ``VideoBlitWidget`` above and uses Metal/QRhi textures — those
    # bytes never touch a QImage or QPainter.drawPixmap call. Any
    # ``drawPixmap`` / ``drawImage`` use here is overlay-territory only;
    # do NOT add HEVC frame painting to this method (it would silently
    # downgrade 10-bit content to 8-bit per PITFALLS #2).

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)

        if self._screen_image is None:
            painter.end()
            return

        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        if self._pixmap is None:
            self._pixmap = QPixmap.fromImage(self._screen_image)

        if not self._monitor_regions:
            # No crop — draw the full image scaled
            src_w = self._remote_width
            src_h = self._remote_height
            display_w = int(self._scale_x * src_w)
            display_h = int(self._scale_y * src_h)
            painter.drawPixmap(
                self._offset_x, self._offset_y,
                display_w, display_h,
                self._pixmap,
            )
        else:
            # Crop and stitch selected monitors side by side
            dest_x = self._offset_x
            for region in self._monitor_regions:
                # Source rect in the full pixmap
                src_x = region["x"]
                src_y = region["y"]
                src_w = region["width"]
                src_h = region["height"]

                # Destination rect in the widget
                dest_w = int(self._scale_x * src_w)
                dest_h = int(self._scale_y * src_h)
                dest_y = self._offset_y

                painter.drawPixmap(
                    QRectF(dest_x, dest_y, dest_w, dest_h),
                    self._pixmap,
                    QRectF(src_x, src_y, src_w, src_h),
                )
                dest_x += dest_w

        painter.end()

    def resizeEvent(self, event: QResizeEvent):
        self._update_scaling()
        # D-07 — keep the F12 overlay anchored on resize, and refresh
        # the monitor/crop rows (widget/server px stay last-known until
        # the next mouseMoveEvent).
        if self._coord_overlay is not None:
            self._coord_overlay.reposition()
            if self._coord_overlay.isVisible():
                cx = self.width() / 2
                cy = self.height() / 2
                mon = self._current_monitor_under_widget(cx, cy)
                crop = getattr(self, "_active_crop_rect", None)
                self._coord_overlay.update_coords(
                    self._coord_overlay._widget_x,
                    self._coord_overlay._widget_y,
                    self._coord_overlay._server_x,
                    self._coord_overlay._server_y,
                    self._current_dpr,
                    self._current_screen_name(),
                    monitor=mon,
                    crop=crop,
                )
        super().resizeEvent(event)

    # --- Tablet/Pen Events (priority over mouse) ---

    def tabletEvent(self, event: QTabletEvent):
        """Handle Wacom/stylus tablet events with full pressure data."""
        # Only handle real pen/eraser devices — macOS trackpads generate
        # tablet events that would block normal mouse input
        pointer_type = event.pointerType()
        # Diagnostic: log the first few tablet events to confirm delivery
        self._tablet_event_count = getattr(self, "_tablet_event_count", 0) + 1
        if self._tablet_event_count <= 5 or self._tablet_event_count % 60 == 0:
            logger.info("tabletEvent #%d: type=%s pointerType=%s pressure=%.3f "
                        "buttons=0x%x accepted=%s",
                        self._tablet_event_count, event.type(), pointer_type,
                        event.pressure(), int(event.buttons()),
                        pointer_type in (QTabletEvent.PointerType.Pen,
                                         QTabletEvent.PointerType.Eraser))
        if pointer_type not in (QTabletEvent.PointerType.Pen,
                                QTabletEvent.PointerType.Eraser):
            event.ignore()
            return

        self._pen_active = True
        event.accept()

        pos = event.position()
        nx, ny, sx, sy = self._widget_to_remote(pos.x(), pos.y())

        # Determine pen type
        if pointer_type == QTabletEvent.PointerType.Eraser:
            pen_type = "eraser"
        else:
            pen_type = "pen"

        # Determine if pen is pressed (tip touching)
        pressed = event.pressure() > 0.0
        hovering = not pressed

        # Barrel button detection
        button = 0
        if event.buttons() & Qt.LeftButton:
            button = 1  # tip
        if event.buttons() & Qt.MiddleButton:
            button = 3  # barrel

        event_type = event.type()
        if event_type == QTabletEvent.TabletPress:
            pressed = True
            hovering = False
            button = 1
        elif event_type == QTabletEvent.TabletRelease:
            pressed = False
            hovering = True
            button = 0

        # Phase 3 D-05 — pen_data dict carries both the legacy
        # normalized floats AND the new server physical-pixel ints so
        # server/mac_input_injector.py + server/input_injector.py can
        # pick up the integer fields (preferred) with the floats as
        # Phase 1/2 wire compat fallback.
        pen_data = {
            "x": nx,
            "y": ny,
            "server_x": sx,
            "server_y": sy,
            "pressure": event.pressure(),
            "tilt_x": event.xTilt(),
            "tilt_y": event.yTilt(),
            "rotation": event.rotation(),
            "button": button,
            "pressed": pressed,
            "hovering": hovering,
            "pen_type": pen_type,
        }

        self.pen_event.emit(pen_data)

        # Phase 2 D-19 — track proximity for focusInEvent re-synth. Qt's
        # TabletEnterProximity / TabletLeaveProximity event types are the
        # authoritative trigger; other tablet-event types (move / press /
        # release) all imply in-proximity. Cache the pen type so the re-
        # synth knows whether to route back as "pen" or "eraser".
        if event_type == QTabletEvent.TabletLeaveProximity:
            self._pen_was_in_proximity = False
        else:
            self._pen_was_in_proximity = True
            self._last_pen_type = pen_type

        # Mark pen inactive after release + leave
        if event_type == QTabletEvent.TabletRelease:
            self._pen_active = False

    # --- Mouse Events ---

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._pen_active:
            return  # Tablet is handling this
        pos = event.position()
        nx, ny, sx, sy = self._widget_to_remote(pos.x(), pos.y())
        # Phase 3 D-05 — signal arity extended with server physical px.
        self.mouse_moved.emit(nx, ny, sx, sy)
        # D-07 — refresh F12 overlay on every mouse move when it's visible.
        if self._coord_overlay is not None and self._coord_overlay.isVisible():
            mon = self._current_monitor_under_widget(pos.x(), pos.y())
            crop = getattr(self, "_active_crop_rect", None)
            self._coord_overlay.update_coords(
                pos.x(), pos.y(),
                sx, sy,
                self._current_dpr,
                self._current_screen_name(),
                monitor=mon,
                crop=crop,
            )

    def mousePressEvent(self, event: QMouseEvent):
        if self._pen_active:
            return
        pos = event.position()
        nx, ny, sx, sy = self._widget_to_remote(pos.x(), pos.y())
        button = self._qt_button_to_int(event.button())

        # macOS Control+click hijack: macOS converts Ctrl+LeftClick into
        # a RightButton event before Qt sees it. Detect that and swap
        # it back to LeftButton so Flame's Ctrl+drag gestures work.
        # The Control keydown itself was already sent by keyPressEvent,
        # so the Linux server has Ctrl held when this click arrives.
        import sys
        if (sys.platform == "darwin"
                and event.button() == Qt.RightButton
                and (event.modifiers() & Qt.ControlModifier)):
            button = 1  # LeftButton on the wire
            self._mac_ctrl_click_swap = True

        self.mouse_button_changed.emit(button, True, nx, ny, sx, sy)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._pen_active:
            return
        pos = event.position()
        nx, ny, sx, sy = self._widget_to_remote(pos.x(), pos.y())
        button = self._qt_button_to_int(event.button())

        # Mirror the press-time swap: if the active drag was a macOS
        # Control-click that we rewrote to LeftButton, the matching
        # release must also be LeftButton or the server will be left
        # thinking a phantom button is still held.
        if self._mac_ctrl_click_swap and event.button() == Qt.RightButton:
            button = 1
            self._mac_ctrl_click_swap = False

        self.mouse_button_changed.emit(button, False, nx, ny, sx, sy)

    def wheelEvent(self, event: QWheelEvent):
        pos = event.position()
        nx, ny, sx, sy = self._widget_to_remote(pos.x(), pos.y())
        delta = event.angleDelta()
        # Convert to discrete scroll steps (120 units = 1 step)
        dx = delta.x() // 120
        dy = delta.y() // 120
        self.mouse_scrolled.emit(dx, dy, nx, ny, sx, sy)

    # --- Keyboard Events ---

    def keyPressEvent(self, event: QKeyEvent):
        if event.isAutoRepeat():
            return

        # Phase 3 D-07 / Pitfall 8 — F12 dev overlay toggle.
        #
        # Handler installation is gated on TERAGUCHI_DEBUG=1 at
        # __init__ time (Pitfall 8: gate HANDLER installation, not
        # visibility). Release builds leave ``_coord_overlay = None``;
        # this branch never fires and F12 falls through to the existing
        # paste-detect + key_changed.emit pipeline unchanged.
        if (event.key() == Qt.Key_F12
                and self._coord_overlay is not None):
            new_visible = not self._coord_overlay.isVisible()
            self._coord_overlay.setVisible(new_visible)
            if new_visible:
                self._coord_overlay.reposition()
                self._coord_overlay.raise_()
            event.accept()
            return

        key = self._remap_key(event.key())
        modifiers = self._qt_modifiers_to_int(event.modifiers())
        logger.debug("Key press: key=0x%x mod=0x%x", key, modifiers)

        # Detect paste: Ctrl+V or Cmd+V → ensure clipboard is synced
        # to server. Phase 2 WR-08: MODIFIER_BIT_CTRL is the wire-format
        # bit position (defined in common/keymap.py); on darwin
        # _qt_modifiers_to_int folds Cmd into the same bit so Cmd+V on
        # Mac correctly fires paste here.
        if key == Qt.Key_V and (modifiers & MODIFIER_BIT_CTRL):
            self.paste_requested.emit()

        self.key_changed.emit(key, key, True, modifiers)
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent):
        if event.isAutoRepeat():
            return
        key = self._remap_key(event.key())
        modifiers = self._qt_modifiers_to_int(event.modifiers())
        self.key_changed.emit(key, key, False, modifiers)
        event.accept()

    @staticmethod
    def _remap_key(key: int) -> int:
        """On macOS, both Command and Control → Control_L on Linux."""
        import sys
        if sys.platform == "darwin":
            if key == Qt.Key_Meta:
                return Qt.Key_Control   # Command → Control_L
            # Physical Control already maps to Qt.Key_Control — no change needed
        return key

    # --- Phase 2 D-11 / D-15: focus-out + IME passthrough ---

    def focusOutEvent(self, event):
        """Phase 2 D-11 trigger #1: viewer loses focus (e.g. Cmd-Tab away).

        Fires the reset_modifiers_requested signal with reason='focus_out'
        BEFORE chaining to super() so the signal is emitted even if a
        parent handler wants to short-circuit the rest of the focus-out
        propagation. The signal is connected up in client/session.py
        and ultimately turned into a KeyResetModifiersMsg on the wire.

        Fixes the canonical "Ctrl stuck after Cmd-Tab" PCoIP-class bug.
        """
        self.reset_modifiers_requested.emit("focus_out")
        super().focusOutEvent(event)

    def focusInEvent(self, event):
        """Phase 2 D-19: re-synth pen proximity when focus returns.

        Only fires the proximity re-synth if the pen WAS in-proximity
        before focus was lost (``_pen_was_in_proximity`` bookkeeping
        from ``tabletEvent``). This avoids spamming the wire when the
        user is plain mouse-only — a fresh focus on the viewer where
        the pen never approached the tablet doesn't need a synthetic
        proximity event (the server PenFSM is idempotent either way,
        but the wire traffic + telemetry would be noise).

        The synthesized event flows out through ``pen_proximity`` →
        client/protocol.py → PenProximityMsg on the wire → server
        PenFSM.send("enter_proximity"). PenFSM is idempotent on
        in_proximity → in_proximity transitions per D-19, so duplicate
        emissions across rapid focus-cycle storms are safe.
        """
        if self._pen_was_in_proximity:
            self._emit_pen_proximity(in_proximity=True,
                                     pen_type=self._last_pen_type)
        super().focusInEvent(event)

    def showEvent(self, event):
        """Phase 2 D-19 + Phase 3 D-06: proximity re-synth + screenChanged wiring.

        D-19: re-synth pen proximity when widget is shown — catches
        lockscreen wake, minimize/restore, virtual-desktop switches,
        and the initial show after connect. Idempotent server-side.

        D-06: the native QWindow is not created until showEvent, so
        this is the earliest the ``screenChanged`` signal can be
        connected. Also caches the current screen's DPR for
        ``_widget_to_remote`` + F12 overlay use.
        """
        self._emit_pen_proximity(in_proximity=True,
                                 pen_type=self._last_pen_type)
        # Phase 3 D-06 — refresh DPR cache + wire screenChanged.
        self._current_dpr = self._current_screen_dpr()
        self._connect_screen_changed()
        super().showEvent(event)

    def _emit_pen_proximity(self, in_proximity: bool, pen_type: str) -> None:
        """Phase 2 D-19: package + emit a pen_proximity dict.

        Centralized so future callers (CLI panic handler, reconnect
        hook) can fire the same shape without duplicating the dict
        construction. Server-side PenFSM idempotency contract lives in
        common/session_fsm.py::PenFSM.
        """
        self.pen_proximity.emit({
            "in_proximity": bool(in_proximity),
            "pen_type": pen_type or "pen",
        })

    def inputMethodEvent(self, event: QInputMethodEvent):
        """Phase 2 D-15: forward IME commit strings as TextCommit, not keys.

        Empty commit strings (pre-edit composition mid-flow) are
        intentionally suppressed — synthesizing them as keycodes is the
        anti-pattern that mangles dead-key composition across US/UK/DE/JP
        layouts (REQ-INPUT-05). Only the final composed string crosses
        the wire.
        """
        commit = event.commitString()
        if commit:
            self.text_commit.emit(commit)
        super().inputMethodEvent(event)
        event.accept()

    # --- Helpers ---

    @staticmethod
    def _qt_button_to_int(button) -> int:
        if button == Qt.LeftButton:
            return 1
        elif button == Qt.MiddleButton:
            return 2
        elif button == Qt.RightButton:
            return 3
        return 1

    @staticmethod
    def _qt_modifiers_to_int(mods) -> int:
        # Phase 2 WR-08: bit positions imported from common.keymap so the
        # numbers stay in sync with downstream consumers (server input
        # injectors and the keyPressEvent paste-detection above).
        result = 0
        if mods & Qt.ShiftModifier:
            result |= MODIFIER_BIT_SHIFT
        if mods & Qt.AltModifier:
            result |= MODIFIER_BIT_ALT
        if mods & Qt.KeypadModifier:
            # MODIFIER_BIT_KEYPAD = numpad-origin key; server disambiguates KP_*
            result |= MODIFIER_BIT_KEYPAD
        import sys
        if sys.platform == "darwin":
            # macOS: both Command and Control → Ctrl on Linux
            # Command+V = paste, Control+C = SIGINT — both need Ctrl
            if mods & (Qt.ControlModifier | Qt.MetaModifier):
                result |= MODIFIER_BIT_CTRL
        else:
            if mods & Qt.ControlModifier:
                result |= MODIFIER_BIT_CTRL
            if mods & Qt.MetaModifier:
                result |= MODIFIER_BIT_META
        return result

    # --- Drag and Drop ---

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path:
                    paths.append(path)
            if paths:
                logger.info("Files dropped: %s", paths)
                self.files_dropped.emit(paths)
            event.acceptProposedAction()

    def sizeHint(self) -> QSize:
        return QSize(self._remote_width, self._remote_height)

    def minimumSizeHint(self) -> QSize:
        return QSize(640, 480)
