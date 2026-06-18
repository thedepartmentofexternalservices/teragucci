"""Client application bootstrap — QApplication + CLI + main().

Extracted from client/main.py (STAB-05 / D-12). Entry point wiring
(argparse, logging config, QApplication + Fusion style, macOS bundle
tweaks) lives here. MainWindow construction and show() is still
performed here, but the class itself has moved to client/main_window.py.
"""

from __future__ import annotations

import argparse
import sys

from common.logging import configure as _configure_logging

# PySide6 + MainWindow are deferred into main() below so the module can
# load in environments without PySide6 (e.g. server-only CI runners,
# diagnostic-bundle-only invocations). The test
# ``test_client_main_accepts_diag_bundle_flag`` monkeypatches
# ``client.app.QApplication`` to guarantee the bundle path never spawns
# a Qt runtime — so we expose the symbol as a module-level attribute
# only after import succeeds. Callers of ``main()`` that need the full
# UI get the normal import path.
QApplication = None   # type: ignore[assignment] — filled by main()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Teraguchi Remote Desktop Client")
    parser.add_argument("--host", default="", help="Server hostname or IP")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--username", "-u", default="")
    parser.add_argument("--password", "-p", default="")
    parser.add_argument(
        "--broker", action="store_true",
        help="Connect via broker instead of direct",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    # OBS-05 / Plan 01-15: diagnostic bundle export. Presence-only flag
    # with optional path — same semantics as server/main.py.
    parser.add_argument(
        "--diag-bundle",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="Export a diagnostic bundle zip and exit (OBS-05). "
             "Default path: ~/teraguchi-diag-<timestamp>.zip",
    )
    # Phase 2 D-02: QRhi/Metal video-blit escape hatch. When set, the
    # VideoBlitWidget in client/viewer.py picks ``QRhiWidget.Api.OpenGL``
    # instead of Metal. The same backed-shader pipeline applies (qsb
    # baked GLSL/HLSL/MSL variants from the same source). Useful when
    # Metal misbehaves on a given GPU; the env var
    # ``TERAGUCHI_LEGACY_GL_BLIT=1`` does the same job for environments
    # that pre-set CLI args.
    parser.add_argument(
        "--legacy-gl-blit",
        action="store_true",
        help="Use the OpenGL backend for the QRhi video-blit widget "
             "instead of the platform default (Metal on macOS). "
             "Equivalent to TERAGUCHI_LEGACY_GL_BLIT=1.",
    )
    return parser.parse_args(argv)


def main() -> None:
    """Boot the Teraguchi client."""
    args = _parse_args()

    # OBS-05 short-circuit — runs BEFORE logging config AND BEFORE
    # QApplication so the bundle export never spawns a Qt runtime.
    # Anything past this line is UI-boot territory.
    if args.diag_bundle is not None:
        from common.diagnostic_bundle import build_bundle
        path = build_bundle(args.diag_bundle or "", tier="client")
        print(f"Diagnostic bundle: {path}")
        sys.exit(0)

    # Phase 2 D-02: thread --legacy-gl-blit into the env var that the
    # VideoBlitWidget reads at construction time. Setting the env var
    # before any client.viewer import means the first VideoBlitWidget
    # sees the override even though the parser ran inside main(). The
    # widget also still honors sys.argv directly so subprocess launches
    # that pass --legacy-gl-blit through still get the OpenGL backend.
    import os
    if getattr(args, "legacy_gl_blit", False):
        os.environ["TERAGUCHI_LEGACY_GL_BLIT"] = "1"

    # OBS-01: route all logging (stdlib + structlog) through the canonical
    # processor chain. phase="client" is bound into contextvars so every
    # emit carries it.
    _configure_logging(phase="client", verbose=args.verbose)

    # Deferred imports — PySide6 is only required for the full UI. The
    # diag-bundle short-circuit above never touches it. We export
    # ``QApplication`` back into the module namespace so tests that
    # monkeypatch ``client.app.QApplication`` to fail-loud see their
    # override take effect. ``main()`` reads the NAME (module attribute)
    # rather than the local binding to honor the monkeypatch.
    global QApplication
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication as _QApp
    if QApplication is None:
        QApplication = _QApp
    from client import theme
    from client.main_window import MainWindow

    # macOS: don't swap Control/Meta so physical Control = Control_L on Linux.
    if sys.platform == "darwin":
        QApplication.setAttribute(Qt.AA_MacDontSwapCtrlAndMeta, True)

    app = QApplication(sys.argv)
    app.setApplicationName("Teraguchi")
    app.setApplicationDisplayName("Teraguchi")
    app.setOrganizationName("Teraguchi")
    app.setDesktopFileName("teraguchi")
    app.setStyle("Fusion")

    # macOS: override process name so dock/menu bar shows "Teraguchi".
    if sys.platform == "darwin":
        try:
            from Foundation import NSBundle  # type: ignore
            bundle = NSBundle.mainBundle()
            info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
            if info:
                info["CFBundleName"] = "Teraguchi"
                info["CFBundleDisplayName"] = "Teraguchi"
        except ImportError:
            pass

    app.setStyleSheet(theme.generate_stylesheet())

    window = MainWindow(
        initial_host=args.host,
        initial_port=args.port,
        initial_user=args.username,
        initial_pass=args.password,
        initial_mode="broker" if args.broker else "direct",
    )
    window.resize(1440, 900)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
