"""
Screen capture module for macOS using ScreenCaptureKit.

ScreenCaptureKit (SCK) is Apple's modern (macOS 12.3+) framework for
screen capture. It replaces the deprecated CGDisplayStream / legacy
CGWindowList* APIs and is the same pipeline Apple's own screen-sharing
stack uses.

Design notes
------------
* SCK is push-based: you create an ``SCStream`` with a filter + config,
  attach an ``SCStreamOutput`` delegate, and frames arrive asynchronously
  on an SCK dispatch queue. Our existing server encoder pipeline is
  pull-based (``capture_raw_bgra()`` blocks until a fresh frame), so this
  module wraps the push stream behind a ``threading.Lock``-guarded
  latest-frame buffer. The encoder always gets the most recent frame,
  which matches the Linux ``mss``/NvFBC behavior.

* Pixel format is forced to ``kCVPixelFormatType_32BGRA`` (FourCC
  ``'BGRA'`` = ``0x42475241``) so the downstream FFmpeg rawvideo path
  sees the same layout as the Linux capture.

* IOSurface-backed CVPixelBuffers often have a ``bytes_per_row`` that
  is larger than ``width * 4`` (page-aligned stride). We strip the
  padding row-by-row before handing the bytes to the encoder; the
  encoder expects tight ``width * height * 4`` buffers.

* SCK frame delivery uses the main thread for the completion handlers,
  so we drive SCShareableContent + startCapture via ``NSCondition`` to
  turn their async callbacks into blocking calls during ``__init__`` and
  ``close()``. The steady-state capture path does NOT block on SCK —
  it just reads the last-captured frame out of the buffer under a lock.

* Local cursor rendering via XFixes (as on Linux) is not portable to
  macOS — there is no clean cross-process shape API. The cursor is left
  baked into the captured frame by setting ``showsCursor=True`` on the
  stream config. The Mac server therefore ships cursor pixels inside
  the video like a classic VNC/PCoIP server. This is a known limitation
  vs. the Linux server and is documented in the Mac port design doc.

* Permissions: SCK requires "Screen & System Audio Recording" TCC
  authorization. The first time this module is instantiated the OS
  will pop a permission prompt; until the user grants it, SCShareable
  Content will return an empty display list and we raise
  ``MacScreenCaptureError`` with a pointer to System Settings.
"""

import ctypes
import io
import logging
import struct
import threading
import time
from typing import Optional, List

import numpy as np
from PIL import Image

from common.messages import MonitorInfo

logger = logging.getLogger(__name__)

# PyObjC imports are deferred to import-time but guarded so that simply
# importing server modules on a non-Mac host does not blow up. The
# dispatcher in screen_capture.py checks ``IS_MACOS`` before pulling this
# module in.
try:
    import objc  # noqa: F401
    from Foundation import (
        NSObject,
        NSCondition,
        NSRunLoop,
        NSDefaultRunLoopMode,
        NSDate,
    )
    import ScreenCaptureKit as SCK  # noqa: F401
    from ScreenCaptureKit import (
        SCShareableContent,
        SCContentFilter,
        SCStreamConfiguration,
        SCStream,
    )
    from Quartz import (
        CVPixelBufferLockBaseAddress,
        CVPixelBufferUnlockBaseAddress,
        CVPixelBufferGetBaseAddress,
        CVPixelBufferGetWidth,
        CVPixelBufferGetHeight,
        CVPixelBufferGetBytesPerRow,
        kCVPixelBufferLock_ReadOnly,
    )
    # CMSampleBufferGetImageBuffer + CMTimeMake live in the CoreMedia
    # framework binding, not Quartz.
    from CoreMedia import (
        CMSampleBufferGetImageBuffer,
        CMTimeMake,
    )
    # CoreVideo P010 pixel format constant — Phase 2 D-01 cp.1 / VIDEO-08.
    # macOS 10-bit 4:2:0 capture surface; required for end-to-end 10-bit.
    # Older PyObjC bundles expose this via Quartz; newer ones via CoreVideo.
    try:
        from CoreVideo import (
            kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange as _CV_P010,
        )
    except ImportError:
        from Quartz import (  # type: ignore[no-redef]
            kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange as _CV_P010,
        )
    _HAS_SCK = True
except Exception as _e:
    _HAS_SCK = False
    _import_error = _e

# kCVPixelFormatType_32BGRA FourCC = 'BGRA' = 0x42475241
_BGRA_FOURCC = 0x42475241

# SCStreamOutputType: we only care about .screen (= 0), not .audio (= 1)
# or .microphone (= 2).
_SC_STREAM_OUTPUT_TYPE_SCREEN = 0

DEFAULT_JPEG_QUALITY = 60
CHANGE_THRESHOLD = 10
BLOCK_SIZE = 32


class MacScreenCaptureError(RuntimeError):
    """Raised when ScreenCaptureKit is unavailable or unauthorized."""


def _check_sck_available():
    if not _HAS_SCK:
        raise MacScreenCaptureError(
            f"ScreenCaptureKit / PyObjC not available: {_import_error}. "
            "Run: pip install pyobjc-framework-ScreenCaptureKit "
            "pyobjc-framework-Quartz pyobjc-framework-Cocoa"
        )


_CV_BASE_EXTRACTOR_LOGGED = False

# ────────────────────────────────────────────────────────────────
# CoreVideo direct-ctypes fast path
# ────────────────────────────────────────────────────────────────
# pyobjc wraps ``CVPixelBufferGetBaseAddress`` as an ``objc.varlist``
# whose slice decoding is — depending on pyobjc version — either
# ``list[int]`` or ``list[bytes]`` of one-byte elements. At 3840x2160
# BGRA that's ~33 million Python allocations per frame, which costs
# literal seconds on even a fast Mac. We bypass the pyobjc wrapper
# entirely by dlopen()'ing CoreVideo.framework via ctypes and pulling
# the raw ``void *`` pointer back, then ``ctypes.string_at`` for a
# single memcpy into a Python bytes object.
try:
    _CV = ctypes.CDLL(
        "/System/Library/Frameworks/CoreVideo.framework/CoreVideo"
    )
    _CV.CVPixelBufferGetBaseAddress.argtypes = [ctypes.c_void_p]
    _CV.CVPixelBufferGetBaseAddress.restype = ctypes.c_void_p
    _HAS_CV_CTYPES = True
except OSError:
    _HAS_CV_CTYPES = False


def _extract_cv_base_bytes(base, length: int) -> bytes:
    """Pull ``length`` bytes out of a CVPixelBuffer base-address return.

    Modern pyobjc (>= 10) wraps ``CVPixelBufferGetBaseAddress``'s
    ``void *`` return as an ``objc.varlist`` whose slice decoding is
    unpredictable between versions — sometimes ``varlist[:N]`` gives a
    list of ints, sometimes a list of 1-byte ``bytes`` objects, and
    int-casting may or may not yield the raw address. We try several
    extraction strategies in order of performance and stick with the
    first one that works, logging the winning path once so the choice
    is visible in the server log.
    """
    global _CV_BASE_EXTRACTOR_LOGGED

    # 1. numpy buffer protocol — zero-copy fast path. Works if pyobjc
    #    exposes the underlying bytes via the Python buffer protocol.
    try:
        arr = np.frombuffer(base, dtype=np.uint8, count=length)
        out = arr.tobytes()
        if not _CV_BASE_EXTRACTOR_LOGGED:
            logger.info("CVPixelBuffer extraction path: numpy.frombuffer")
            _CV_BASE_EXTRACTOR_LOGGED = True
        return out
    except (TypeError, ValueError):
        pass

    # 2. __c_void_p__ protocol — pyobjc's documented hook for getting a
    #    ctypes.c_void_p out of a pointer-wrapping object.
    try:
        cvp = base.__c_void_p__()  # type: ignore[attr-defined]
        addr = cvp.value
        if addr:
            out = ctypes.string_at(addr, length)
            if not _CV_BASE_EXTRACTOR_LOGGED:
                logger.info("CVPixelBuffer extraction path: __c_void_p__")
                _CV_BASE_EXTRACTOR_LOGGED = True
            return out
    except (AttributeError, TypeError):
        pass

    # 3. Legacy pyobjc: void* returns were auto-converted to int.
    try:
        addr = int(base)
        out = ctypes.string_at(addr, length)
        if not _CV_BASE_EXTRACTOR_LOGGED:
            logger.info("CVPixelBuffer extraction path: int(base)")
            _CV_BASE_EXTRACTOR_LOGGED = True
        return out
    except (TypeError, ValueError):
        pass

    # 4. Slice the varlist. pyobjc may hand back:
    #      * ``bytes`` directly — best, just return it
    #      * ``list[int]`` — wrap in bytes()
    #      * ``list[bytes]`` of 1-byte elements — b''.join them
    #    All three are SLOW relative to a pointer memcpy, but at least
    #    the pipeline keeps running. First-time use logs a warning so
    #    we know to go hunt for a faster path.
    try:
        chunk = base[:length]
    except Exception as e:
        raise RuntimeError(
            f"Could not slice CVPixelBuffer base "
            f"(type={type(base).__name__}): {e}"
        )

    if isinstance(chunk, (bytes, bytearray)):
        if not _CV_BASE_EXTRACTOR_LOGGED:
            logger.info("CVPixelBuffer extraction path: slice -> bytes")
            _CV_BASE_EXTRACTOR_LOGGED = True
        return bytes(chunk)

    if isinstance(chunk, memoryview):
        if not _CV_BASE_EXTRACTOR_LOGGED:
            logger.info("CVPixelBuffer extraction path: slice -> memoryview")
            _CV_BASE_EXTRACTOR_LOGGED = True
        return chunk.tobytes()

    if isinstance(chunk, (list, tuple)) and chunk:
        first = chunk[0]
        if isinstance(first, int):
            if not _CV_BASE_EXTRACTOR_LOGGED:
                logger.warning(
                    "CVPixelBuffer extraction path: slice -> list[int] "
                    "(SLOW; element=%s)", type(first).__name__,
                )
                _CV_BASE_EXTRACTOR_LOGGED = True
            return bytes(chunk)
        if isinstance(first, (bytes, bytearray)):
            if not _CV_BASE_EXTRACTOR_LOGGED:
                logger.warning(
                    "CVPixelBuffer extraction path: slice -> list[bytes] "
                    "join (VERY SLOW; element=%s len=%d)",
                    type(first).__name__, len(first),
                )
                _CV_BASE_EXTRACTOR_LOGGED = True
            return b"".join(chunk)

    raise RuntimeError(
        f"Could not extract bytes from CVPixelBuffer base address "
        f"(base type={type(base).__name__}, "
        f"slice type={type(chunk).__name__}, "
        f"element type={type(chunk[0]).__name__ if chunk else 'empty'})"
    )


if _HAS_SCK:

    class _StreamOutputHandler(NSObject):
        """SCStreamOutput delegate.

        SCK calls ``stream:didOutputSampleBuffer:ofType:`` on every frame.
        We latch the most recent frame's BGRA bytes into ``_latest_bgra``
        under ``_lock``. The capture class reads this buffer on demand.
        """

        def initWithCapture_(self, capture):
            self = objc.super(_StreamOutputHandler, self).init()
            if self is None:
                return None
            self._capture = capture
            return self

        # objc selector: stream:didOutputSampleBuffer:ofType:
        def stream_didOutputSampleBuffer_ofType_(
            self, stream, sample_buffer, output_type
        ):
            if output_type != _SC_STREAM_OUTPUT_TYPE_SCREEN:
                return
            try:
                pixel_buffer = CMSampleBufferGetImageBuffer(sample_buffer)
                if pixel_buffer is None:
                    return

                # Lock read-only so SCK can keep the surface mapped on
                # another buffer for the next frame.
                lock_result = CVPixelBufferLockBaseAddress(
                    pixel_buffer, kCVPixelBufferLock_ReadOnly
                )
                if lock_result != 0:
                    return
                try:
                    width = CVPixelBufferGetWidth(pixel_buffer)
                    height = CVPixelBufferGetHeight(pixel_buffer)
                    bpr = CVPixelBufferGetBytesPerRow(pixel_buffer)
                    if width == 0 or height == 0:
                        return

                    total = height * bpr
                    row_stride = width * 4

                    # Fast path: call CoreVideo directly via ctypes so
                    # the base address comes back as a real ``void *``
                    # we can ``string_at`` in a single memcpy. This is
                    # 100x+ faster than pyobjc's varlist wrapper which
                    # materialises every byte as its own Python object.
                    raw_full = None
                    global _CV_BASE_EXTRACTOR_LOGGED
                    if _HAS_CV_CTYPES:
                        try:
                            pb_id = objc.pyobjc_id(pixel_buffer)
                            base_ptr = _CV.CVPixelBufferGetBaseAddress(pb_id)
                            if base_ptr:
                                raw_full = ctypes.string_at(base_ptr, total)
                                if not _CV_BASE_EXTRACTOR_LOGGED:
                                    logger.info(
                                        "CVPixelBuffer extraction path: "
                                        "ctypes CoreVideo memcpy (fast)"
                                    )
                                    _CV_BASE_EXTRACTOR_LOGGED = True
                        except Exception as e:
                            if not _CV_BASE_EXTRACTOR_LOGGED:
                                logger.warning(
                                    "ctypes CoreVideo fast path failed: "
                                    "%s — falling back to pyobjc varlist",
                                    e,
                                )

                    if raw_full is None:
                        base = CVPixelBufferGetBaseAddress(pixel_buffer)
                        if base is None:
                            return
                        raw_full = _extract_cv_base_bytes(base, total)

                    if bpr == row_stride:
                        raw = raw_full
                    else:
                        # Strip row padding. Build a tight buffer the
                        # encoder can consume directly.
                        out = bytearray(height * row_stride)
                        for y in range(height):
                            start = y * bpr
                            out[y * row_stride: (y + 1) * row_stride] = (
                                raw_full[start: start + row_stride]
                            )
                        raw = bytes(out)

                    with self._capture._lock:
                        self._capture._latest_bgra = raw
                        self._capture._latest_size = (width, height)
                        self._capture._frame_count += 1
                        self._capture._last_frame_time = time.monotonic()
                finally:
                    CVPixelBufferUnlockBaseAddress(
                        pixel_buffer, kCVPixelBufferLock_ReadOnly
                    )
            except Exception as e:
                # Never let an exception cross back into Objective-C —
                # it will crash the SCK dispatch queue.
                logger.error("SCK frame handler error: %s", e, exc_info=True)


    class _DisplayChangeDelegate(NSObject):
        """Phase 3 D-11 — push callback for display configuration changes.

        Subscribes via ``NSWorkspace.didChangeScreenParametersNotification``
        (the canonical pattern per Apple ScreenCaptureKit programming
        guide; ``SCStreamDelegate`` proper does not expose a screen-list
        change selector). Sets ``_hotplug_pending = True`` on the
        capture under ``_lock`` so ``MonitorHotplug.run()`` picks it up
        on the next iteration without waiting for the 1s poll.

        Every callback is wrapped in try/except — exceptions MUST NOT
        cross back into Objective-C or the dispatch queue crashes
        (mirrors ``_StreamOutputHandler.stream_didOutputSampleBuffer_ofType_``
        discipline at L212).
        """

        def initWithCapture_(self, capture):
            self = objc.super(_DisplayChangeDelegate, self).init()
            if self is None:
                return None
            self._capture = capture
            return self

        # objc selector: screenParametersChanged:
        def screenParametersChanged_(self, notification):
            try:
                lock = getattr(self._capture, "_lock", None)
                if lock is None:
                    return
                with lock:
                    self._capture._hotplug_pending = True
                logger.info("SCK display change pushed via NSWorkspace")
            except Exception as e:
                # Never let an exception cross back into Objective-C —
                # mirrors the _StreamOutputHandler discipline.
                logger.error(
                    "SCK display-change handler error: %s", e, exc_info=True,
                )


class MacScreenCapture:
    """macOS screen capture using ScreenCaptureKit.

    Implements the same interface as the Linux ``ScreenCapture`` class so
    the rest of the server pipeline (video_encoder, session_manager) can
    treat Mac and Linux capture identically.
    """

    def __init__(
        self,
        monitor_index: int = 1,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
        fps: int = 60,
        want_10bit: bool = False,
    ):
        _check_sck_available()

        self.monitor_index = monitor_index
        self.jpeg_quality = jpeg_quality
        self._fps = fps
        # Phase 2 D-01 cp.1 / VIDEO-08: 10-bit P010 capture surface.
        # When True, configure SCK with kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange
        # + .hdrLocalDisplay so the capture boundary is honest about bit
        # depth. When False (default), keep the Phase 1 BGRA fast path.
        # The plumbing into ServerHelloMsg / negotiation lands in D-03's
        # capability_probe (separate plan); for now the kwarg is here so
        # Task 2 can wire it without surgery later.
        self._want_10bit = bool(want_10bit)
        # Phase 2 WR-06: tracks whether the SCStream's HDR dynamic-range
        # tag was successfully set on the active stream config. Without
        # the tag, SCK can silently tone-map P010 frames down to 8-bit
        # on XDR displays even though the surface format is P010 — see
        # the runtime_capability_state property below.
        self._hdr_set_ok: bool = False

        self._lock = threading.Lock()
        self._latest_bgra: Optional[bytes] = None
        self._latest_size: tuple = (0, 0)
        self._last_frame: Optional[np.ndarray] = None
        self._frame_count = 0
        self._last_frame_time = 0.0

        self._displays: list = []  # list of SCDisplay handles
        self._selected_display = None
        self._stream: Optional[SCStream] = None
        self._handler: Optional[_StreamOutputHandler] = None

        # Phase 3 D-11 — push-based hot-plug flag. The
        # _DisplayChangeDelegate sets this under _lock when NSWorkspace
        # fires didChangeScreenParametersNotification; the
        # MonitorHotplug async poll in server/monitor_hotplug.py checks
        # the flag on every 1s tick so the handler runs before the 1s
        # poll's detect_hotplug re-enumeration. Poll stays as safety net
        # per D-11 (covers sleep/wake transitions where runloop is
        # suspended and the notification may be missed).
        self._hotplug_pending: bool = False
        self._display_observer = None

        # Width/height are set by _select_display once we know the
        # target display's pixel size.
        self.width = 0
        self.height = 0

        self._enumerate_displays()
        self._select_display(monitor_index)
        self._start_stream()

        # Phase 3 D-11 — install the NSWorkspace observer after the
        # stream is up so we never miss an early configuration change
        # during SCK boot. Gated inside try/except because the
        # observer install isn't load-bearing for capture; losing the
        # push signal just means the 1s poll picks up change
        # eventually (poll is the safety net per D-11).
        try:
            from AppKit import (
                NSWorkspace,
                NSWorkspaceDidChangeScreenParametersNotification,
            )
            self._display_observer = _DisplayChangeDelegate.alloc().initWithCapture_(self)
            nc = NSWorkspace.sharedWorkspace().notificationCenter()
            # The selector on the Obj-C side is "screenParametersChanged:";
            # PyObjC maps that to our Python method
            # ``_DisplayChangeDelegate.screenParametersChanged_`` at
            # L239. Passing the Obj-C form here is the canonical
            # pattern for addObserver:selector:name:object:.
            nc.addObserver_selector_name_object_(
                self._display_observer,
                "screenParametersChanged:",
                NSWorkspaceDidChangeScreenParametersNotification,
                None,
            )
            logger.info("SCK display-change observer installed")
        except Exception as e:
            logger.warning(
                "Failed to install SCK display-change observer: %s "
                "— falling back to poll-only (1s cadence).", e,
            )
            self._display_observer = None

        logger.info(
            "MacScreenCapture initialized: %dx%d via SCK (display %d, %d fps)",
            self.width, self.height, monitor_index, fps,
        )

    # ------------------------------------------------------------------
    # Async → sync helpers
    # ------------------------------------------------------------------

    def _run_sync(self, starter, timeout: float = 5.0):
        """Turn an SCK async-with-completion-handler call into a blocking call.

        ``starter`` is a callable that takes a single-arg ``completion``
        callback. We drive the run loop until the completion callback
        fires or timeout elapses. This is how we block in ``__init__``
        on SCShareableContent and ``startCaptureWithCompletionHandler_``
        without deadlocking the main thread.
        """
        condition = NSCondition.alloc().init()
        result = {"done": False, "value": None, "error": None}

        def completion(value, error):
            condition.lock()
            try:
                result["value"] = value
                result["error"] = error
                result["done"] = True
                condition.signal()
            finally:
                condition.unlock()

        starter(completion)

        # Spin the run loop so the SCK completion handler (which is
        # delivered on the main thread) actually runs.
        deadline = time.monotonic() + timeout
        run_loop = NSRunLoop.currentRunLoop()
        while not result["done"] and time.monotonic() < deadline:
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode,
                NSDate.dateWithTimeIntervalSinceNow_(0.05),
            )
        if not result["done"]:
            raise MacScreenCaptureError(
                f"SCK async call timed out after {timeout:.1f}s"
            )
        if result["error"] is not None:
            raise MacScreenCaptureError(
                f"SCK async call failed: {result['error']}"
            )
        return result["value"]

    # ------------------------------------------------------------------
    # Display enumeration
    # ------------------------------------------------------------------

    def _enumerate_displays(self):
        def starter(completion):
            SCShareableContent.getShareableContentWithCompletionHandler_(
                completion
            )
        content = self._run_sync(starter, timeout=5.0)
        if content is None:
            raise MacScreenCaptureError(
                "SCShareableContent returned None — likely missing Screen "
                "Recording permission. Grant it in System Settings → "
                "Privacy & Security → Screen & System Audio Recording."
            )
        # list(...) detaches from the NSArray whose lifetime is tied to `content`.
        displays = list(content.displays())
        if not displays:
            raise MacScreenCaptureError(
                "No displays reported by SCShareableContent. Either no "
                "monitors attached or Screen Recording permission has "
                "not been granted."
            )
        self._displays = displays
        logger.info("SCK enumerated %d display(s)", len(displays))

    def _select_display(self, index: int):
        """Select which display to capture (1-indexed to match Linux)."""
        if not self._displays:
            raise MacScreenCaptureError("No displays to select")
        # monitor_index=0 (virtual desktop / all monitors) isn't a
        # native SCK concept — fall through to display 1.
        if index <= 0 or index > len(self._displays):
            logger.warning(
                "Requested display %d out of range (1..%d), using display 1",
                index, len(self._displays),
            )
            index = 1
        self.monitor_index = index
        self._selected_display = self._displays[index - 1]
        self.width = int(self._selected_display.width())
        self.height = int(self._selected_display.height())

    # ------------------------------------------------------------------
    # Stream lifecycle
    # ------------------------------------------------------------------

    def _start_stream(self):
        if self._selected_display is None:
            raise MacScreenCaptureError("No display selected")

        content_filter = SCContentFilter.alloc().initWithDisplay_excludingWindows_(
            self._selected_display, []
        )

        config = SCStreamConfiguration.alloc().init()
        config.setWidth_(self.width)
        config.setHeight_(self.height)
        # Phase 2 D-01 cp.1 / VIDEO-08: 10-bit capture when negotiated.
        # Falls back to 32-bit BGRA when 10-bit was not negotiated by the
        # client (Phase 1 baseline path). The .hdrLocalDisplay dynamic
        # range is required to keep the upper 2 bits honest on
        # MBP XDR / Pro Display XDR — without it SCK silently tone-maps
        # to 8-bit even when the surface format is P010.
        if self._want_10bit:
            config.setPixelFormat_(_CV_P010)
            # Phase 2 WR-06: re-arm the HDR-set flag for this start
            # attempt; only flip True after the setter succeeds. reinit()
            # / switch_monitor() reuse this code path, so resetting here
            # keeps runtime_capability_state honest after reconfigure.
            self._hdr_set_ok = False
            try:
                # SCK 14.0+: SCCaptureDynamicRangeHDRLocalDisplay (= 1).
                # Older bundles raise AttributeError; we log + degrade.
                hdr_const = getattr(
                    SCK, "SCCaptureDynamicRangeHDRLocalDisplay", None
                ) or getattr(
                    SCK, "SCCaptureDynamicRangeHdrLocalDisplay", None
                )
                if hdr_const is not None:
                    # Selector: setCaptureDynamicRange:
                    if hasattr(config, "setCaptureDynamicRange_"):
                        config.setCaptureDynamicRange_(hdr_const)
                        self._hdr_set_ok = True
                else:
                    logger.warning(
                        "mac_screen_capture.hdrLocalDisplay_missing_sdk_too_old"
                    )
            except (AttributeError, Exception) as e:
                logger.warning(
                    "mac_screen_capture.hdrLocalDisplay_set_failed: %s", e
                )
        else:
            config.setPixelFormat_(_BGRA_FOURCC)
        config.setMinimumFrameInterval_(CMTimeMake(1, self._fps))
        config.setQueueDepth_(6)  # SCK requires >= 3; 6 gives headroom
        config.setShowsCursor_(True)
        # Disable audio — we have a separate CoreAudio capture path.
        try:
            config.setCapturesAudio_(False)
        except Exception:
            # Older SCK versions may not expose capturesAudio; fine.
            pass

        stream = SCStream.alloc().initWithFilter_configuration_delegate_(
            content_filter, config, None
        )

        handler = _StreamOutputHandler.alloc().initWithCapture_(self)
        ok, err = stream.addStreamOutput_type_sampleHandlerQueue_error_(
            handler, _SC_STREAM_OUTPUT_TYPE_SCREEN, None, None
        )
        if not ok:
            raise MacScreenCaptureError(
                f"SCStream.addStreamOutput failed: {err}"
            )

        self._stream = stream
        self._handler = handler

        def starter(completion):
            stream.startCaptureWithCompletionHandler_(completion)

        # startCapture's completion callback is (error) only — our
        # _run_sync expects (value, error). Wrap it.
        condition = NSCondition.alloc().init()
        state = {"done": False, "error": None}

        def start_done(error):
            condition.lock()
            try:
                state["error"] = error
                state["done"] = True
                condition.signal()
            finally:
                condition.unlock()

        stream.startCaptureWithCompletionHandler_(start_done)

        deadline = time.monotonic() + 5.0
        run_loop = NSRunLoop.currentRunLoop()
        while not state["done"] and time.monotonic() < deadline:
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode,
                NSDate.dateWithTimeIntervalSinceNow_(0.05),
            )
        if not state["done"]:
            raise MacScreenCaptureError("SCStream.startCapture timed out")
        if state["error"] is not None:
            raise MacScreenCaptureError(
                f"SCStream.startCapture failed: {state['error']}"
            )

    def _stop_stream(self):
        if self._stream is None:
            return
        condition = NSCondition.alloc().init()
        state = {"done": False}

        def stop_done(error):
            condition.lock()
            try:
                state["done"] = True
                condition.signal()
            finally:
                condition.unlock()

        try:
            self._stream.stopCaptureWithCompletionHandler_(stop_done)
        except Exception as e:
            logger.warning("SCStream.stopCapture raised: %s", e)
            self._stream = None
            self._handler = None
            return

        deadline = time.monotonic() + 2.0
        run_loop = NSRunLoop.currentRunLoop()
        while not state["done"] and time.monotonic() < deadline:
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode,
                NSDate.dateWithTimeIntervalSinceNow_(0.05),
            )
        self._stream = None
        self._handler = None

    # ------------------------------------------------------------------
    # Public interface — matches ScreenCapture
    # ------------------------------------------------------------------

    def reinit(self, width: int = 0, height: int = 0):
        """Reinitialize after a resolution change / display reconfig."""
        logger.info("MacScreenCapture reinit requested")
        self._stop_stream()
        self._enumerate_displays()
        self._select_display(self.monitor_index)
        self._start_stream()
        with self._lock:
            self._last_frame = None
            self._latest_bgra = None
        logger.info("MacScreenCapture reinit: %dx%d", self.width, self.height)

    def switch_monitor(self, index: int):
        """Switch to a different display."""
        logger.info("Switching to display %d", index)
        self._stop_stream()
        self._enumerate_displays()
        self._select_display(index)
        self._start_stream()
        with self._lock:
            self._last_frame = None
            self._latest_bgra = None

    def list_monitors(self) -> List[MonitorInfo]:
        """Enumerate all displays reported by SCK."""
        out: List[MonitorInfo] = []
        for i, disp in enumerate(self._displays, start=1):
            try:
                w = int(disp.width())
                h = int(disp.height())
                # SCDisplay exposes a frame rect via frame() on newer
                # SCK versions; fall back to (0,0) if missing.
                try:
                    frame = disp.frame()
                    x = int(frame.origin.x)
                    y = int(frame.origin.y)
                except Exception:
                    x, y = 0, 0
                try:
                    disp_id = int(disp.displayID())
                except Exception:
                    disp_id = i
                out.append(MonitorInfo(
                    id=i,
                    name=f"Display {disp_id}",
                    width=w,
                    height=h,
                    x=x,
                    y=y,
                    primary=(i == 1),
                    scale=1.0,
                ))
            except Exception as e:
                logger.warning("Failed to describe display %d: %s", i, e)
        return out

    def detect_hotplug(self) -> bool:
        """Check if SCK reports a different display configuration.

        Phase 3 D-11 — full ``(displayID, width, height, x, y)`` tuple
        signature per display catches reorder + same-size swap +
        reposition that today's shallow count+WxH check silently missed
        (Pitfall 4: SCK delivers stale frame-size after Retina rebuild
        at the same WxH; encoder feeds wrong-sized buffer and produces
        corrupt H.265). Mirrors the Linux D-09 upgrade in
        ``server/screen_capture.py::ScreenCapture.detect_hotplug``.
        """
        try:
            old_sig = []
            for d in self._displays:
                try:
                    disp_id = int(d.displayID())
                except Exception:
                    disp_id = 0
                try:
                    frame = d.frame()
                    ox = int(frame.origin.x)
                    oy = int(frame.origin.y)
                except Exception:
                    ox, oy = 0, 0
                old_sig.append((
                    disp_id, int(d.width()), int(d.height()), ox, oy,
                ))
            self._enumerate_displays()
            new_sig = []
            for d in self._displays:
                try:
                    disp_id = int(d.displayID())
                except Exception:
                    disp_id = 0
                try:
                    frame = d.frame()
                    ox = int(frame.origin.x)
                    oy = int(frame.origin.y)
                except Exception:
                    ox, oy = 0, 0
                new_sig.append((
                    disp_id, int(d.width()), int(d.height()), ox, oy,
                ))
            if old_sig != new_sig:
                logger.info("Display hotplug detected: %s -> %s",
                            old_sig, new_sig)
                return True
        except Exception as e:
            logger.warning("detect_hotplug failed: %s", e)
        return False

    @property
    def screen_size(self) -> tuple:
        return (self.width, self.height)

    @property
    def monitor_count(self) -> int:
        return len(self._displays)

    @property
    def runtime_capability_state(self) -> str:
        """Phase 2 WR-06 — symmetry with server.screen_capture's
        ScreenCapture.runtime_capability_state. Reports whether the SCK
        capture is delivering honest 10-bit. Truth table:

          NOT want_10bit                          -> "not_supported"
          want_10bit + HDR dynamic-range tag set  -> "confirmed"
          want_10bit + HDR tag setter unavailable -> "degraded"

        ``degraded`` covers SCK older than 14.0 (no
        SCCaptureDynamicRangeHDRLocalDisplay constant), older bundles
        missing setCaptureDynamicRange_, or runtime exceptions while
        configuring it. In those cases the surface format is still P010,
        but SCK can silently tone-map to 8-bit on XDR displays — which
        is exactly what the badge needs to communicate.

        Consumed by the server-side build_color_caps helper that
        combines this with the encoder probe to decide the final badge
        value (mirrors the Phase 2 D-03 / VIDEO-09 honest-capability
        reporting on the Linux side).
        """
        if not self._want_10bit:
            return "not_supported"
        if not self._hdr_set_ok:
            return "degraded"
        return "confirmed"

    # --- Raw BGRA capture (for H.264/H.265/AV1 encoder pipeline) ---

    def _wait_for_first_frame(self, timeout: float = 2.0):
        """Block until the first SCK frame arrives.

        Called lazily by ``capture_raw_bgra`` because SCK start is
        asynchronous and the first frame may not be in the buffer yet
        when the encoder kicks off.
        """
        deadline = time.monotonic() + timeout
        run_loop = NSRunLoop.currentRunLoop()
        while time.monotonic() < deadline:
            with self._lock:
                if self._latest_bgra is not None:
                    return
            run_loop.runMode_beforeDate_(
                NSDefaultRunLoopMode,
                NSDate.dateWithTimeIntervalSinceNow_(0.01),
            )

    def capture_raw_bgra(self) -> bytes:
        """Return the most recent frame as raw BGRA bytes.

        If no frame has arrived yet, waits briefly for one. If the wait
        still times out (permission denied, stream stalled), returns a
        black frame of the expected size so the encoder pipeline doesn't
        crash — the session_manager's health monitor will notice the
        frame stall separately.
        """
        with self._lock:
            if self._latest_bgra is not None:
                return self._latest_bgra

        self._wait_for_first_frame(timeout=1.0)

        with self._lock:
            if self._latest_bgra is not None:
                return self._latest_bgra

        logger.warning(
            "No SCK frame available — returning black frame (%dx%d)",
            self.width, self.height,
        )
        return b"\x00" * (self.width * self.height * 4)

    def capture_raw_frame(self) -> np.ndarray:
        """Return the most recent frame as a numpy BGRA array."""
        raw = self.capture_raw_bgra()
        w, h = self.width, self.height
        expected = w * h * 4
        if len(raw) != expected:
            # Stream resized mid-session — fall back to the actual
            # latched dimensions.
            with self._lock:
                lw, lh = self._latest_size
            if lw and lh and len(raw) == lw * lh * 4:
                w, h = lw, lh
            else:
                logger.warning(
                    "BGRA buffer size mismatch: got %d, expected %d",
                    len(raw), expected,
                )
                return np.zeros((h, w, 4), dtype=np.uint8)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 4))
        return arr

    # --- JPEG capture (fallback mode) ---

    def capture_full_frame(self) -> bytes:
        """Capture the entire screen and return JPEG bytes."""
        arr = self.capture_raw_frame()
        # BGRA -> RGB for PIL
        frame = arr[:, :, :3][:, :, ::-1]
        self._last_frame = frame.copy()
        return self._encode_jpeg(frame)

    def capture_dirty_regions(self) -> list:
        """Capture screen and detect dirty rectangles."""
        arr = self.capture_raw_frame()
        frame = arr[:, :, :3][:, :, ::-1]

        if self._last_frame is None:
            self._last_frame = frame.copy()
            jpeg_data = self._encode_jpeg(frame)
            return [(0, 0, self.width, self.height, jpeg_data)]

        if frame.shape != self._last_frame.shape:
            self._last_frame = frame.copy()
            jpeg_data = self._encode_jpeg(frame)
            return [(0, 0, frame.shape[1], frame.shape[0], jpeg_data)]

        diff = np.abs(frame.astype(np.int16) - self._last_frame.astype(np.int16))
        changed = np.max(diff, axis=2) > CHANGE_THRESHOLD

        dirty_regions = self._find_dirty_blocks(changed)
        if not dirty_regions:
            return []

        merged = self._merge_regions(dirty_regions)
        results = []
        for x, y, w, h in merged:
            x2 = min(x + w, self.width)
            y2 = min(y + h, self.height)
            region = frame[y:y2, x:x2]
            jpeg_data = self._encode_jpeg(region)
            results.append((x, y, x2 - x, y2 - y, jpeg_data))

        self._last_frame = frame.copy()
        return results

    def _find_dirty_blocks(self, changed: np.ndarray) -> list:
        blocks = []
        h, w = changed.shape
        for by in range(0, h, BLOCK_SIZE):
            for bx in range(0, w, BLOCK_SIZE):
                block = changed[by:by + BLOCK_SIZE, bx:bx + BLOCK_SIZE]
                if np.any(block):
                    blocks.append((bx, by, BLOCK_SIZE, BLOCK_SIZE))
        return blocks

    def _merge_regions(self, blocks: list) -> list:
        if not blocks:
            return []
        rows = {}
        for x, y, w, h in blocks:
            if y not in rows:
                rows[y] = []
            rows[y].append((x, w))

        merged = []
        for y, spans in rows.items():
            spans.sort()
            current_x, current_w = spans[0]
            for x, w in spans[1:]:
                if x <= current_x + current_w:
                    current_w = max(current_w, x + w - current_x)
                else:
                    merged.append((current_x, y, current_w, BLOCK_SIZE))
                    current_x, current_w = x, w
            merged.append((current_x, y, current_w, BLOCK_SIZE))

        if not merged:
            return merged

        merged.sort(key=lambda r: (r[0], r[1]))
        final = [merged[0]]
        for x, y, w, h in merged[1:]:
            px, py, pw, ph = final[-1]
            if x == px and w == pw and y == py + ph:
                final[-1] = (px, py, pw, ph + h)
            else:
                final.append((x, y, w, h))

        return final

    def _encode_jpeg(self, frame: np.ndarray) -> bytes:
        img = Image.fromarray(frame)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.jpeg_quality, optimize=False)
        return buf.getvalue()

    def invalidate(self):
        """Force next dirty-regions capture to be a full frame."""
        self._last_frame = None

    def close(self):
        logger.info(
            "MacScreenCapture.close (frames delivered: %d)", self._frame_count
        )
        self._stop_stream()
        # Phase 3 D-11 — remove the NSWorkspace display-change observer
        # before tearing down the displays list so the delegate never
        # fires on a half-destroyed capture. Best-effort; losing the
        # unregister is not fatal (observer is weak-ref from AppKit
        # side on macOS 10.11+).
        if getattr(self, "_display_observer", None) is not None:
            try:
                from AppKit import NSWorkspace
                nc = NSWorkspace.sharedWorkspace().notificationCenter()
                nc.removeObserver_(self._display_observer)
            except Exception as e:
                logger.debug(
                    "SCK display-change observer unregister failed: %s", e,
                )
            self._display_observer = None
        self._displays = []
        self._selected_display = None
        with self._lock:
            self._latest_bgra = None
            self._last_frame = None
