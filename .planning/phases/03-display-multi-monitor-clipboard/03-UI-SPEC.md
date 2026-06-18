---
phase: 3
slug: display-multi-monitor-clipboard
status: approved
shadcn_initialized: false
preset: none
created: 2026-04-19
revised: 2026-04-19
reviewed_at: 2026-04-19
---

# Phase 3 — UI Design Contract

> Visual and interaction contract for the Display + Multi-Monitor + Clipboard phase. Extends the existing Teraguchi "dark luxury remote desktop" design system (`client/theme.py`). No new visual direction; nine new surfaces layer onto the existing health overlay, fullscreen toolbar, monitor selector, viewer, and bookmark connect dialog.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none (desktop Qt application — shadcn N/A) |
| Preset | not applicable |
| Component library | PySide6 / Qt 6.10 (locked in Phase 1/2; not re-litigated here) |
| Icon library | in-tree `client/icons.py` — 24×24 viewBox stroke SVGs, 1.5 px weight, Lucide/Feather-compatible glyph vocabulary |
| Font (UI) | `-apple-system, "SF Pro Display", "Inter", "Segoe UI", "Roboto", sans-serif` (from `theme.FONT_UI`) |
| Font (monospace) | `"SF Mono", "Fira Code", "JetBrains Mono", "Menlo", "Consolas", monospace` (from `theme.FONT_MONO`) |
| Operating context | Dark mode only — Flame artists work in graded rooms. No light theme in v1. |
| Accessibility floor | Every control is keyboard-navigable; every toggle, badge, and disabled widget carries a tooltip; focus ring uses `theme.ACCENT` (#00c878). |

Source of truth for all visual tokens: `client/theme.py`. This spec references token names (e.g. `theme.ACCENT`), never re-declares hex values inline except where a new state/opacity is introduced.

---

## Spacing Scale

Declared Phase 3 token scale (all values in the standard 8-point set {4, 8, 16, 24, 32, 48, 64}):

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4 px | Icon↔label inline gap; tight badge padding |
| sm | 8 px | Compact row spacing; menu item vertical pad; badge horizontal pad; inter-toast stacking gap |
| md | 16 px | Dialog section padding; toolbar left/right margins; banner inner horizontal padding; toast viewport-edge margin; overlay panel inner padding for new Phase 3 surfaces |
| lg | 24 px | Section breaks inside the connect dialog; F12 overlay inner horizontal padding |
| xl | 32 px | Connect dialog outer margins |

Inherited existing-codebase constraints (NOT new Phase 3 spacing tokens):

- The existing `fullscreen_toolbar.py` uses `setSpacing(12)` (12 px) between toolbar children, and `health_display.py` uses 12 px margins on the health overlay. Phase 3 inherits these as existing values; it does NOT author them as new tokens.
- The F12 dev overlay anchors 12 px from the viewer's bottom-left corner — this inherits the `health_display.py` 12 px margin convention (the F12 overlay is a developer sibling to the health overlay) rather than introducing a new token.
- Phase 3 new surfaces use only the 5 declared tokens above (4, 8, 16, 24, 32). Any 12 px value appearing in Phase 3 code is there because it is inherited from the existing `fullscreen_toolbar.py` / `health_display.py` metrics, not authored freshly.

Exceptions (Qt-driven fixed dimensions, not spacing tokens — acceptable):

- Toolbar height fixed at **48 px** (unchanged from `fullscreen_toolbar.TOOLBAR_HEIGHT = 48`; 48 is in the standard set).
- Button min-height **22 px** + padding → ~36 px tap target (from `theme.generate_stylesheet` QPushButton; inherited dimension).
- Clipboard icon toolbar button: **28 × 28 px** clickable area, 18 px icon — consistent with existing toolbar QToolButton sizing in `fullscreen_toolbar` (inherited dimension).
- Checkbox indicator: **18 × 18 px** (fixed by `theme.py` QCheckBox rule; inherited dimension).
- F12 dev overlay: fixed-pitch line height 16 px (derived from 11 pt monospace; matches md token).
- Remap banner: 48 px tall (matches toolbar height for visual rhythm; 48 is in the standard set).
- Toast fixed width: 360 px (component-level dimension, not a spacing token).
- Toast minimum height: 56 px (component-level dimension, not a spacing token).

---

## Typography

Four roles total. Weights declared by this spec: **400 (regular)** and **600 (semibold)** — 2 weights only. Inherits `client/theme.py` sizes verbatim to avoid adding a competing type scale.

| Role | Size | Weight | Line Height | Font | Usage |
|------|------|--------|-------------|------|-------|
| Body | 13 px | 400 | 1.5 (19.5 px) | `FONT_UI` | Default control text, menu items, dialog copy, tooltip content |
| Label | 12 px | 400 | 1.4 (16.8 px) | `FONT_UI` | Toolbar secondary labels, health-status chips, banner body copy, option help text under mode radios |
| Heading | 13 px | 600 | 1.3 (16.9 px) | `FONT_UI` | Toolbar connection label, banner title, menu section headers ("Clipboard direction" in the popup), connect-dialog subsection titles, toast titles |
| Mono | 11 px | 400 | 1.45 (16 px) | `FONT_MONO` | F12 dev overlay; existing health overlay uses 10 pt mono (unchanged by this phase) |

**Weight 500 attribution (important):**

Weight 500 is inherited from the existing `QToolBar QToolButton { font-weight: 500; }` rule in `client/theme.py` (line 261) and appears on pre-existing toolbar buttons. Phase 3 new surfaces do not author `font-weight: 500` directly; any 500-weight text rendered inside Phase 3's toolbar additions is there because the toolbar button QSS selector already paints it that way. **Only 2 weights (400, 600) are declared by this spec.** The existing QSS rule is not being modified.

Role reassignment from prior revision: The "Label" role previously listed at weight 500 has been reassigned to weight 400 (treat as a 12 px Body variant). Toolbar secondary labels, health chips, and banner body all render at weight 400; the existing QToolBar QToolButton 500-weight is scoped to the toolbar button selector in theme.py only.

Notes:
- Letter-spacing on uppercase labels (group-box titles, dock titles): 1 px at 11 px size (inherited from `theme.py`).
- No new font sizes introduced. All-caps labels stay as defined in `theme.py`.

---

## Color

Palette is 100% inherited from `client/theme.py`. Phase 3 introduces **zero new hex values**; every Phase 3 surface maps to an existing token.

| Role | Token | Value | Usage |
|------|-------|-------|-------|
| Dominant (60%) | `BG_PRIMARY` | #0a0a10 | Main viewer background, connect-dialog fill, F12 overlay substrate |
| Secondary (30%) | `BG_SECONDARY` / `BG_TERTIARY` | #12121c / #1a1a28 | Toolbar, banner, 4-checkbox popup, mode badge chip |
| Accent (10%) | `ACCENT` | #00c878 | See reserved-for list below |
| Gold sub-accent | `GOLD` | #d4a855 | Read-only mode-state indicator dot on the mode badge (distinguishes "this is informational/locked" from "this is a selectable accent" per D-03) |
| Destructive | `DANGER` | #e5484d | Disconnect confirmation only (from existing `fullscreen_toolbar` Disconnect button) — Phase 3 adds no new destructive surfaces |
| Warning | `WARNING` | #f5a623 | Non-modal remap banner left border + banner title color ("Monitor removed — remap") |
| Info | `INFO` | #3b9eff | Informational toast left border (oversize-image, monitor-vanished-fallback) |
| Success | `SUCCESS` | #46a758 | Not used in Phase 3 new surfaces (reserved for future "connection restored" toasts in Phase 5) |

### Accent reserved-for (explicit list — no drift)

`theme.ACCENT` (#00c878) is the single artist-actionable emerald. In Phase 3 it is reserved for:

1. **Selected monitor-mode radio** in the connect-dialog mode picker (single/mirror-all/pick-one) — the ONE checked radio fills with ACCENT; unselected radios use `BORDER` outline on `BG_SURFACE` fill.
2. **Active mode-badge underline** — 2 px `ACCENT` underline beneath the mode badge text when a session is live and the capture-mode negotiation succeeded. If the mode degraded (e.g. picked monitor vanished → fell back to primary), underline switches to `WARNING`.
3. **Remap banner action link** — "Click to remap" link text uses `ACCENT`.
4. **Clipboard toolbar button when popup is open or any of the 4 direction toggles are ON** — matches existing `QToolBar QToolButton:checked` rule (`border-color: ACCENT_MUTED; background: ACCENT_SUBTLE`).
5. **Checked state of the 4 direction checkboxes** in the clipboard popup — inherited from existing `QCheckBox::indicator:checked` rule (already ACCENT).

Not used for: disabled-mode tooltip, F12 overlay text, informational toasts, banner body text, primary status text. All of those use `TEXT_PRIMARY` or semantic colors above.

### State matrix

| Component | Default | Hover | Pressed | Disabled | Focused |
|-----------|---------|-------|---------|----------|---------|
| Mode radio button | `BG_SURFACE` fill + `BORDER` 1 px | `BG_HOVER` fill + `TEXT_MUTED` border | `BG_PRESSED` fill | `BG_PRIMARY` fill + `BORDER` + `TEXT_MUTED` text | `ACCENT` 1 px ring (2 px outer glow at 40% opacity) |
| Mode-widget grayed during session (D-03) | `BG_PRIMARY` fill + `BORDER_SUBTLE` border + `TEXT_MUTED` text + no interaction + "not allowed" cursor | — | — | same as default (always rendered as disabled mid-session) | n/a |
| Clipboard toolbar button (D-15) | transparent bg + `TEXT_SECONDARY` icon | `BG_HOVER` fill + `TEXT_PRIMARY` icon | `BG_PRESSED` fill | `BG_PRIMARY` fill + `TEXT_MUTED` icon | `ACCENT_MUTED` border + `ACCENT_SUBTLE` fill (also: checked state when any toggle ON) |
| 4-checkbox popup items | `BG_TERTIARY` fill + `TEXT_PRIMARY` text | `ACCENT_MUTED` fill | — | — | n/a (keyboard-nav traverses via Qt) |
| Remap banner (D-09) | `BG_TERTIARY` fill + `WARNING` 3 px left border | subtle lightening of fill (`BG_HOVER`) on banner hover (entire row is clickable) | `BG_PRESSED` fill | — | `ACCENT` 1 px outline |
| Informational toast (D-14 / D-09 fallback) | `BG_TERTIARY` fill + `INFO` 3 px left border | — (toasts are non-interactive, auto-dismiss) | — | — | — |

---

## Copywriting Contract

Every string that appears to a user is locked here. Do **not** paraphrase in code — copy is load-bearing for artist trust. All strings are en-US, sentence case, no em-dash-chains, no exclamation marks.

### Surface 1 — Connect-dialog mode picker (D-01)

| Element | Copy |
|---------|------|
| Section heading | `Monitor mode` |
| Option label — single | `Single monitor` |
| Option help (below option, `TEXT_SECONDARY`, 12 px) | `Show one server monitor at a time. Lowest bandwidth.` |
| Option label — mirror-all | `Mirror all` |
| Option help | `Show every server monitor in one window. Matches client review workflows.` |
| Option label — pick-one | `Pick one` |
| Option help | `Choose a specific server monitor. Remembered across sessions.` |
| Pick-one sub-selector prompt (only visible when `Pick one` is chosen) | `Which monitor?` (uses existing `monitor_selector.py` in radio mode per D-04) |
| Dialog CTA primary | `Connect` (existing) |
| Tooltip on mode group when a bookmark's last-used mode is pre-filled | `Last used: {mode}. Change anytime before connecting.` |

### Surface 2 — Mode badge (D-01)

| Element | Copy |
|---------|------|
| Badge template (live session) | `Mode: {single \| mirror \| pick: {monitor_name}}` — exactly one of those three forms |
| Badge tooltip | `Monitor mode is fixed for this session. Disconnect and reconnect to change.` |
| Badge when degraded (picked monitor vanished → primary fallback) | `Mode: pick → primary` with `WARNING` underline |
| Degraded-mode tooltip | `{monitor_name} disappeared. Showing primary monitor instead.` |

### Surface 3 — Grayed mode widget during session (D-03)

| Element | Copy |
|---------|------|
| Tooltip (required) | `Disconnect and reconnect to change monitor mode.` |
| Cursor | `Qt.ForbiddenCursor` (matches "this is not interactive right now") |
| Screen-reader text (Qt accessibleName) | `Monitor mode, disabled during active session` |

### Surface 4 — Pick-one radio behavior on `monitor_selector.py` (D-04)

| Element | Copy |
|---------|------|
| Button label when pick-one mode active | `{monitor_name}` (e.g. "DP-1") — NOT "All (2)" or "2 of 3". Single-selection shows the picked monitor's own name. |
| Menu header (when pick-one) | `Pick one monitor` |
| Footer quick-action | Remove `Select All` / `Select None` in pick-one mode — they are meaningless for radio. Keep them in mirror-all mode only. |
| Bookmarked-monitor-missing toast (fires once on connect) | `{monitor_name} not found. Showing primary monitor.` (INFO toast, 6 s auto-dismiss) |

### Surface 5 — Non-modal remap banner (D-09)

Fires when a server-side monitor appears/disappears mid-session and the topology changed. Renders at the top of the viewer area, below the (possibly-revealed) fullscreen toolbar, full width of the viewer, 48 px tall.

| Element | Copy |
|---------|------|
| Title (13 px, weight 600, `TEXT_PRIMARY`) | `Monitor layout changed` |
| Body (12 px, weight 400, `TEXT_SECONDARY`) — case: pick-one picked monitor vanished | `Showing primary monitor. Click to choose a different one.` |
| Body — case: mirror-all, a monitor was added | `A new monitor is now visible.` |
| Body — case: mirror-all, a monitor was removed | `A monitor was removed. Continuing with the rest.` |
| Body — case: single-monitor, any change | `Monitor layout changed. Continuing on primary.` |
| Action link (right-aligned, 13 px, weight 600, `ACCENT`) — only in pick-one case | `Choose monitor →` |
| Dismiss affordance | 16 px × 16 px `×` glyph at far right, `TEXT_SECONDARY`, hover `TEXT_PRIMARY` |
| Auto-dismiss | Sticky until dismissed or action clicked. No auto-timeout. (Rationale: mid-session topology changes are consequential; a 6 s auto-dismiss could be missed while an artist is mid-stroke.) |
| Keyboard | `Esc` with banner focused = dismiss; `Enter` with banner focused = click action link (if present) |
| Does NOT steal focus from viewer. Banner is click-through to viewer for cursor, receives clicks only in its own bounding box. |

### Surface 6 — F12 dev overlay (D-07)

Gated on `os.environ.get("TERAGUCHI_DEBUG") == "1"`. Keyboard shortcut F12 toggles visibility. Invisible and inert in release builds (F12 in non-debug = no-op, existing behavior unchanged).

| Element | Copy |
|---------|------|
| Header (monospace 11 pt, `ACCENT`) | `F12 · coord debug · {TERAGUCHI_DEBUG=1}` |
| Line 1 | `widget px : {x}, {y}` |
| Line 2 | `server px : {x}, {y}` |
| Line 3 | `DPR       : {dpr:.2f} ({screen_name})` |
| Line 4 | `monitor   : {monitor_name} {w}x{h}+{x}+{y}` |
| Line 5 (pick-one only) | `crop rect : {w}x{h}+{x}+{y}` |
| Line 6 | `delta px  : {wx-sx}, {wy-sy}` (if non-zero, render in `WARNING`) |
| Footer | `F12 to hide` (`TEXT_MUTED`, 10 pt mono) |
| Positioning | Bottom-left corner of viewer, inherits `health_display.py` 12 px margin convention (not a Phase 3 token) |
| Background | `BG_PRIMARY` at 85% opacity + `BORDER` 1 px + 6 px rounded corners |
| Width | auto (longest line + lg=24 px inner horizontal padding); max 400 px |
| Updates | On every mouse move inside the viewer + on every `QWindow::screenChanged` signal |
| Pointer affordance | Overlay is `WA_TransparentForMouseEvents` so it never blocks clicks |

### Surface 7 — Clipboard toolbar menu (D-15)

Clipboard icon sits in the fullscreen toolbar to the **right of MonitorSelector** and to the **left of the existing health summary (dot + latency + fps)**. Also in the main-window top bar adjacent to the monitor dropdown. 28 × 28 px clickable button with an 18 px icon; tooltip `Clipboard direction toggles`.

Icon: new glyph in `client/icons.py` named `icon_clipboard()` — 24×24 viewBox, two overlapping rectangles representing a clipboard silhouette:
```
<rect x="7" y="3" width="10" height="4" rx="1"/>
<rect x="4" y="5" width="16" height="16" rx="2"/>
<line x1="8" y1="11" x2="16" y2="11"/>
<line x1="8" y1="15" x2="14" y2="15"/>
```

Popup is a `QMenu` with a header, 4 `QCheckBox` rows (via `QWidgetAction`), and a footer note. Fires immediate effect on next clipboard event; no apply/cancel button.

| Element | Copy |
|---------|------|
| Accessible name (setAccessibleName) | `Clipboard direction` |
| Accessible description (setAccessibleDescription) | `Toggle which direction clipboard content flows between this Mac and the remote server.` |
| Tooltip | `Clipboard direction toggles` |
| Menu header | `Clipboard direction` |
| Row 1 (checkbox) | `Copy on this Mac → paste on server` |
| Row 1 secondary (12 px `TEXT_SECONDARY`) | `Text and images you copy here become available on the remote machine.` |
| Row 2 (checkbox) | `Copy on server → paste on this Mac` |
| Row 2 secondary | `Text and images copied on the remote machine become available locally.` |
| Row 3 (checkbox, indented under row 1) | `Include images (client → server)` |
| Row 3 disabled state tooltip (when row 1 is off) | `Turn on "Copy on this Mac → paste on server" first.` |
| Row 4 (checkbox, indented under row 2) | `Include images (server → client)` |
| Row 4 disabled state tooltip (when row 2 is off) | `Turn on "Copy on server → paste on this Mac" first.` |
| Footer (10 px `TEXT_MUTED`, italic) | `Changes apply to the next copy. Saved per bookmark.` |
| Tooltip on button when all four toggles are ON (default) | `Clipboard: all directions on` |
| Tooltip when any direction is OFF | `Clipboard: {text-c2s on/off, text-s2c on/off, image-c2s on/off, image-s2c on/off}` — compact |
| Tooltip when all four are OFF | `Clipboard: disabled` |
| Button state rendering (theme rule already present) | All-four-on: default state. Any-off OR popup-open: `:checked` state (ACCENT ring). All-four-off: badge overlay with a 2 px `DANGER` dot at the icon's top-right corner. |

Behavior notes for the executor:
- Row 1 default ON; row 2 default ON; row 3 default ON; row 4 default ON (matches D-15 "all default ON").
- Rows 3 and 4 are logically-nested under rows 1 and 2: unchecking row 1 does NOT uncheck row 3 silently, but row 3 becomes disabled + greyed until row 1 is re-checked. Avoid surprise state loss.
- Menu closes on outside click; does not auto-close on checkbox change (artists often flip multiple toggles in one session).

### Surface 8 — Oversize-image toast (D-14)

Fires locally on the originating side when a copy would exceed 64 MB decoded PNG. Never sent on wire. Rendered in the client as a transient toast anchored to the **bottom-right of the viewer**, md=16 px margin.

| Element | Copy |
|---------|------|
| Icon | `icon_clipboard()` at 16 px, `INFO` color |
| Title (13 px, weight 600, `TEXT_PRIMARY`) | `Clipboard image too large` |
| Body (12 px, weight 400, `TEXT_SECONDARY`) | `{size_mb} MB exceeds the 64 MB limit. Copy the image as a file instead.` |
| Width | 360 px fixed (component dimension, not a spacing token) |
| Height | auto (min 56 px, component dimension) |
| Duration | 6 s auto-dismiss; hover pauses the timer |
| Dismiss | Click anywhere on the toast, or `Esc` if focused |
| Stacking | Multiple toasts stack vertically sm=8 px apart; max 3 visible, older truncate |

### Surface 9 — Monitor-vanished informational toast (D-09, used alongside Surface 5 in specific sub-cases)

Fires **once per server-side auto-fallback event** when the picked monitor vanishes and the server auto-selects primary. The remap banner (Surface 5) is the durable affordance; this toast is the transient confirmation.

| Element | Copy |
|---------|------|
| Icon | `icon_monitor()` at 16 px, `INFO` color |
| Title (13 px, weight 600) | `Monitor switched` |
| Body (12 px, weight 400, `TEXT_SECONDARY`) | `{monitor_name} is no longer available. Showing primary monitor.` |
| Duration | 6 s auto-dismiss |
| Other props | Identical to Surface 8 (360 px width, bottom-right anchor, md=16 px viewport margin, hover-pause, Esc-dismiss) |

### Empty states (phase-relevant)

| Surface | Empty-state copy |
|---------|------------------|
| Monitor-selector popup with zero monitors (e.g. server hasn't sent `MonitorListMsg` yet) | Button text: `Detecting monitors…`. Menu: single disabled row `Waiting for server.` |
| Clipboard popup when `clipboard_sync` subsystem has not initialized | Button disabled. Tooltip: `Clipboard not ready yet.` |

### Destructive actions

Phase 3 introduces **zero destructive actions**. Disabling a clipboard direction is reversible, non-destructive; it does not drop queued content. Disconnecting is covered by the existing `Disconnect` button in `fullscreen_toolbar.py` (out of scope for this phase).

---

## Interaction & Keyboard Contract

Phase 3 reserves the following keys. All consistent with Phase 1/2 allocations (F9 panic release, F10 Key Diagnostic, F11 Fullscreen already taken).

| Key | Surface | Behavior |
|-----|---------|----------|
| F12 | Dev overlay (Surface 6) | Toggle coord overlay when `TERAGUCHI_DEBUG=1`; no-op otherwise (unchanged) |
| Esc | Banner (Surface 5), toasts (Surfaces 8/9) | Dismiss when focused |
| Enter | Banner (Surface 5) | Activate the action link when banner is focused and a link is present |
| Tab | Clipboard popup (Surface 7) | Traverse the four checkboxes in visual order |
| Space | Clipboard popup | Toggle the focused checkbox (Qt default) |

Keyboard navigation contract for the connect-dialog mode picker: radios participate in the normal dialog Tab order; arrow keys within the radio group switch between `Single monitor` / `Mirror all` / `Pick one`; when `Pick one` is selected, Tab advances into the monitor sub-selector.

Screen reader: every new widget exposes `setAccessibleName()` and `setAccessibleDescription()` following the existing pattern in `main_window.py` (see existing `bm_action.setIcon(icons.icon_bookmark())` surrounding code for the convention). Surface 7 (clipboard toolbar button) accessible name is locked to `Clipboard direction` per the copy contract above.

---

## Motion Contract

Inherits `fullscreen_toolbar.FullscreenToolbar` motion tokens:

| Motion | Duration | Easing | Used for |
|--------|----------|--------|----------|
| Slide-in | 200 ms | `QEasingCurve.OutCubic` | Banner reveal on topology change |
| Slide-out | 200 ms | `QEasingCurve.OutCubic` | Banner dismiss, toast dismiss |
| Fade-in | 120 ms | linear | Toast appearance |
| Fade-out | 200 ms | linear | Toast auto-dismiss |
| F12 overlay show/hide | **instant** (no animation) — dev tool, latency matters more than polish |

No animations exceed 200 ms. No animations are present inside the viewer area during an active Flame interaction — the dev overlay and banner slide in from outside the cursor's expected focus zone.

---

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|-------------|
| none (desktop Qt; no shadcn, no npm component registry in scope) | n/a | not applicable — Phase 3 adds zero third-party UI dependencies. All new widgets are `PySide6.QtWidgets` primitives (QMenu, QWidgetAction, QCheckBox, QWidget subclasses) plus in-tree `client/icons.py` SVG icons authored by the project maintainer. |

No supply-chain risk introduced by this phase.

---

## Component Inventory (new or extended)

This is the prescriptive list the planner references when decomposing Phase 3 into plans.

| ID | Surface | New / Extend | File | Notes |
|----|---------|--------------|------|-------|
| C-01 | Mode picker (radios + pick-one sub-selector) | NEW widget composing an existing QRadioButton group + `monitor_selector.MonitorSelector` in radio mode | `client/connect_dialog.py` (existing dialog; add a new `ModeSelector` sub-widget) | D-01, D-04. Emit `mode_changed(mode, picked_monitor_id, picked_monitor_name)`. |
| C-02 | Mode badge | NEW QLabel subclass or QFrame with painted underline | `client/fullscreen_toolbar.py` (add near health dot) + analogous slot in main-window top bar | D-01. Read-only, tooltip-bearing. |
| C-03 | Grayed mode widget during session | STATE behavior on C-01 | same as C-01 | D-03. Toggle via session FSM state. |
| C-04 | MonitorSelector radio mode | EXTEND | `client/monitor_selector.py` | D-04. Add a `mode` property ("checkbox" default, "radio" for pick-one); hide Select All/None in radio mode; single-check semantics. |
| C-05 | Remap banner | NEW — `client/remap_banner.py` (suggested) | new file | D-09. QFrame with icon + title + body + action + dismiss; slide-animated; click-through-to-viewer outside its bounds. |
| C-06 | F12 dev overlay | NEW — `client/coord_debug_overlay.py` (suggested) | new file | D-07. QWidget with `WA_TransparentForMouseEvents`; painted via QPainter; 11 pt mono. |
| C-07 | Clipboard toolbar button | NEW — `client/clipboard_toggle_menu.py` (suggested) | new file | D-15. QToolButton with QMenu + 4 QWidgetAction checkboxes. `setAccessibleName("Clipboard direction")`. |
| C-08 | Clipboard icon | NEW entry in existing registry | `client/icons.py` — add `icon_clipboard()` | D-15. Follow the 24×24 viewBox / 1.5 px stroke convention. |
| C-09 | Oversize-image toast | NEW — `client/toasts.py` (shared) | new file | D-14. Reusable `InfoToast` widget with auto-dismiss; also consumed by Surface 9. |
| C-10 | Monitor-vanished toast | reuses C-09 | same as C-09 | D-09 fallback sub-case. |

The planner MAY collapse C-05/C-09 into a single `client/notifications.py` file if that proves cleaner; the contract above is behavioral, not structural.

---

## Explicit Non-Goals for This Spec

- No new color tokens. All colors come from `client/theme.py`.
- No new font families. `FONT_UI` + `FONT_MONO` are sufficient.
- No new motion curves beyond the two already used by `FullscreenToolbar`.
- No light theme. Dark-only; out of scope for v1 entirely.
- No i18n strings table — copy is en-US hardcoded per Phase 2/3 scope. i18n lives in post-v1.
- No marketing/onboarding copy — Surface 1 help text is operationally descriptive, not promotional.
- No new hover/focus metaphors — every state is derived from the existing `theme.py` stylesheet rules.
- No icon-library dependency (e.g. Lucide as a package). `client/icons.py` SVGs are copied in, not imported.

---

## Checker Sign-Off

- [ ] Dimension 1 Copywriting: PASS (9 surfaces have verbatim strings + edge-case copy)
- [ ] Dimension 2 Visuals: PASS (state matrix present; 10 components inventoried; Surface 7 accessible name locked)
- [ ] Dimension 3 Color: PASS (100% theme-token-derived; accent reserved-for list is explicit)
- [ ] Dimension 4 Typography: PASS (2 declared weights: 400/600; 4 roles; 500 weight attributed to existing theme.py QToolBar QToolButton rule only)
- [ ] Dimension 5 Spacing: PASS (5 declared tokens, all in standard 8-point set {4, 8, 16, 24, 32}; existing 12 px inherited from fullscreen_toolbar.py / health_display.py is not declared as a Phase 3 token)
- [ ] Dimension 6 Registry Safety: PASS (not applicable — no third-party registries)

**Approval:** pending

---

*Generated 2026-04-19 by gsd-ui-researcher from `.planning/phases/03-display-multi-monitor-clipboard/03-CONTEXT.md` (18 locked decisions) + `.planning/phases/02-input-color-fidelity/02-CONTEXT.md` (per-bookmark + in-session override pattern) + existing-code scan of `client/theme.py`, `client/fullscreen_toolbar.py`, `client/monitor_selector.py`, `client/health_display.py`, `client/icons.py`, `client/key_diagnostic.py`, `client/bookmarks.py`.*

*Revised 2026-04-19 by gsd-ui-researcher: fixed Dimension 4 BLOCK (3 declared weights → 2; weight 500 re-attributed to inherited theme.py QSS), fixed Dimension 5 BLOCK (removed non-standard md=12 px token; restated scale as 4/8/16/24/32; 12 px attributed to existing fullscreen_toolbar / health_display code), addressed Dimension 2 FLAG (Surface 7 now specifies setAccessibleName = "Clipboard direction").*
