"""
Inline SVG icons for Teraguchi — no external asset files needed.

Each icon is a function returning a QIcon from embedded SVG data.
Icons are 24x24 viewbox, stroke-based, 1.5px weight — matching
modern UI icon sets (Lucide, Heroicons, Feather).
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtSvg import QSvgRenderer

_CACHE: dict[str, QIcon] = {}


def _svg_icon(svg_body: str, color: str = "#8b8ba3") -> QIcon:
    """Create a QIcon from an SVG path, with configurable stroke color."""
    key = svg_body + color
    if key in _CACHE:
        return _CACHE[key]

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        f'{svg_body}</svg>'
    ).encode('utf-8')

    icon = QIcon()
    for size in (16, 20, 24, 32):
        pixmap = QPixmap(QSize(size, size))
        pixmap.fill(Qt.transparent)
        renderer = QSvgRenderer(svg)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)

    _CACHE[key] = icon
    return icon


# ── Connection ────────────────────────────────────────

def icon_connect(color="#8b8ba3"):
    """Plus in circle — new connection."""
    return _svg_icon(
        '<circle cx="12" cy="12" r="10"/>'
        '<line x1="12" y1="8" x2="12" y2="16"/>'
        '<line x1="8" y1="12" x2="16" y2="12"/>', color)

def icon_disconnect(color="#8b8ba3"):
    """X in circle — disconnect."""
    return _svg_icon(
        '<circle cx="12" cy="12" r="10"/>'
        '<line x1="15" y1="9" x2="9" y2="15"/>'
        '<line x1="9" y1="9" x2="15" y2="15"/>', color)

def icon_server(color="#8b8ba3"):
    """Server/computer — for bookmarks."""
    return _svg_icon(
        '<rect x="2" y="3" width="20" height="14" rx="2"/>'
        '<line x1="8" y1="21" x2="16" y2="21"/>'
        '<line x1="12" y1="17" x2="12" y2="21"/>', color)

# ── Toolbar ───────────────────────────────────────────

def icon_refresh(color="#8b8ba3"):
    """Circular arrow — refresh."""
    return _svg_icon(
        '<polyline points="23 4 23 10 17 10"/>'
        '<path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>', color)

def icon_fullscreen(color="#8b8ba3"):
    """Maximize — fullscreen."""
    return _svg_icon(
        '<polyline points="15 3 21 3 21 9"/>'
        '<polyline points="9 21 3 21 3 15"/>'
        '<line x1="21" y1="3" x2="14" y2="10"/>'
        '<line x1="3" y1="21" x2="10" y2="14"/>', color)

def icon_minimize(color="#8b8ba3"):
    """Minimize — exit fullscreen."""
    return _svg_icon(
        '<polyline points="4 14 10 14 10 20"/>'
        '<polyline points="20 10 14 10 14 4"/>'
        '<line x1="14" y1="10" x2="21" y2="3"/>'
        '<line x1="3" y1="21" x2="10" y2="14"/>', color)

def icon_health(color="#8b8ba3"):
    """Activity/pulse — health stats."""
    return _svg_icon(
        '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>', color)

def icon_settings(color="#8b8ba3"):
    """Gear/cog — settings."""
    return _svg_icon(
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42'
        'M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>', color)

def icon_keyboard(color="#8b8ba3"):
    """Keyboard — key diagnostic."""
    return _svg_icon(
        '<rect x="2" y="4" width="20" height="16" rx="2"/>'
        '<line x1="6" y1="8" x2="6" y2="8"/>'
        '<line x1="10" y1="8" x2="10" y2="8"/>'
        '<line x1="14" y1="8" x2="14" y2="8"/>'
        '<line x1="18" y1="8" x2="18" y2="8"/>'
        '<line x1="6" y1="12" x2="6" y2="12"/>'
        '<line x1="18" y1="12" x2="18" y2="12"/>'
        '<line x1="8" y1="16" x2="16" y2="16"/>', color)

def icon_monitor(color="#8b8ba3"):
    """Monitor — display/screen."""
    return _svg_icon(
        '<rect x="2" y="3" width="20" height="14" rx="2"/>'
        '<line x1="8" y1="21" x2="16" y2="21"/>'
        '<line x1="12" y1="17" x2="12" y2="21"/>', color)

def icon_clipboard(color="#8b8ba3"):
    """Clipboard silhouette — copy/paste direction toggles (Phase 3 D-15).

    24×24 viewBox + 1.5 px stroke convention. UI-SPEC Surface 7 SVG body
    verbatim; consumed by Plan 06's clipboard toolbar toggle button and
    by Plan 05's oversize-image toast (Surface 8).
    """
    return _svg_icon(
        '<rect x="7" y="3" width="10" height="4" rx="1"/>'
        '<rect x="4" y="5" width="16" height="16" rx="2"/>'
        '<line x1="8" y1="11" x2="16" y2="11"/>'
        '<line x1="8" y1="15" x2="14" y2="15"/>', color)

# ── Status ────────────────────────────────────────────

def icon_power(color="#8b8ba3"):
    """Power symbol."""
    return _svg_icon(
        '<path d="M18.36 6.64a9 9 0 1 1-12.73 0"/>'
        '<line x1="12" y1="2" x2="12" y2="12"/>', color)

def icon_bookmark(color="#8b8ba3"):
    """Bookmark/star."""
    return _svg_icon(
        '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 '
        '12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>', color)

def icon_import(color="#8b8ba3"):
    """Download — import."""
    return _svg_icon(
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="7 10 12 15 17 10"/>'
        '<line x1="12" y1="15" x2="12" y2="3"/>', color)

def icon_export(color="#8b8ba3"):
    """Upload — export."""
    return _svg_icon(
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="17 8 12 3 7 8"/>'
        '<line x1="12" y1="3" x2="12" y2="15"/>', color)

def icon_search(color="#8b8ba3"):
    """Magnifying glass — search."""
    return _svg_icon(
        '<circle cx="11" cy="11" r="8"/>'
        '<line x1="21" y1="21" x2="16.65" y2="16.65"/>', color)

def icon_trash(color="#8b8ba3"):
    """Trash can — delete."""
    return _svg_icon(
        '<polyline points="3 6 5 6 21 6"/>'
        '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
        '<path d="M10 11v6M14 11v6"/>'
        '<path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>', color)

def icon_edit(color="#8b8ba3"):
    """Pencil — edit."""
    return _svg_icon(
        '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>'
        '<path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>', color)


def icon_upload(color="#8b8ba3"):
    """Upload — send file."""
    return _svg_icon(
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="17 8 12 3 7 8"/>'
        '<line x1="12" y1="3" x2="12" y2="15"/>', color)


def icon_usb(color="#8b8ba3"):
    """USB connector — device passthrough."""
    return _svg_icon(
        '<circle cx="10" cy="7" r="1"/>'
        '<circle cx="4" cy="20" r="1"/>'
        '<path d="M4.7 19.3 19 5"/>'
        '<path d="m21 3-3 1 2 2Z"/>'
        '<path d="M9.26 7.68 5 12l2 2"/>'
        '<path d="m10 14 5 5"/>'
        '<circle cx="16" cy="20" r="1"/>', color)
