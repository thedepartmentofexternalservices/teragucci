"""
Bookmark manager for saved connections.

Stores connection profiles (host, port, credentials, quality settings)
in a local JSON file with password encryption using a machine-derived key.
"""

import base64
import hashlib
import json
import logging
import os
import platform
import time
import uuid
from pathlib import Path
from typing import List, Optional

from common.messages import ConnectionProfile

logger = logging.getLogger(__name__)


def _get_config_dir() -> Path:
    """Get platform-appropriate config directory."""
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "Teraguchi"
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home())) / "Teraguchi"
    else:
        base = Path.home() / ".config" / "teraguchi"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _get_machine_key() -> bytes:
    """
    Derive a machine-specific encryption key for credential storage.

    Uses a combination of machine-specific identifiers to create a key
    that is consistent on the same machine but different across machines.
    This is NOT high-security encryption — it just prevents casual
    reading of saved passwords.
    """
    identifiers = [
        platform.node(),        # Hostname
        platform.machine(),     # CPU architecture
        str(Path.home()),       # Home directory path
    ]

    # Try to get a more stable machine ID
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path) as f:
                identifiers.append(f.read().strip())
                break
        except (FileNotFoundError, PermissionError):
            pass

    combined = ":".join(identifiers).encode()
    return hashlib.sha256(combined).digest()


def _encrypt_password(password: str) -> str:
    """Encrypt a password for storage using XOR with machine key."""
    if not password:
        return ""
    key = _get_machine_key()
    pwd_bytes = password.encode("utf-8")
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(pwd_bytes))
    return base64.b64encode(encrypted).decode("ascii")


def _decrypt_password(encrypted: str) -> str:
    """Decrypt a stored password."""
    if not encrypted:
        return ""
    try:
        key = _get_machine_key()
        enc_bytes = base64.b64decode(encrypted)
        decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(enc_bytes))
        return decrypted.decode("utf-8")
    except Exception:
        return ""


class BookmarkManager:
    """
    Manages saved connection profiles (bookmarks).

    Features:
    - Save/load connection profiles with encrypted credentials
    - Import/export bookmarks
    - Organize with color labels
    - Track last-connected times
    - Search/filter bookmarks
    """

    def __init__(self):
        self._config_dir = _get_config_dir()
        self._bookmarks_file = self._config_dir / "bookmarks.json"
        self._profiles: dict = {}  # id -> ConnectionProfile
        self._load()

    def _load(self):
        """Load bookmarks from disk."""
        if self._bookmarks_file.exists():
            try:
                with open(self._bookmarks_file) as f:
                    data = json.load(f)
                migrated = False
                for bid, pdata in data.items():
                    self._profiles[bid] = ConnectionProfile.from_dict(pdata)
                    # Phase 2 D-10 migration — pre-Phase-2 bookmarks did not
                    # carry destination_kind / swap_cmd_ctrl. The dataclass
                    # default lands swap_cmd_ctrl=True (correct for Linux),
                    # but if the saved JSON omits the field entirely AND the
                    # destination is a Mac, the default is wrong. We can't
                    # actually tell Linux from Mac just from the host string
                    # so we honor the saved file and default to Linux/swap-on
                    # — which matches the v1 plan where the Flame production
                    # server is Rocky Linux. The user can flip the per-bookmark
                    # checkbox in the editor if they want Mac-server behavior.
                    if "swap_cmd_ctrl" not in pdata or "destination_kind" not in pdata:
                        migrated = True
                        # Re-apply defaults consistent with the destination kind.
                        prof = self._profiles[bid]
                        if prof.destination_kind == "mac":
                            prof.swap_cmd_ctrl = pdata.get("swap_cmd_ctrl", False)
                        else:
                            prof.swap_cmd_ctrl = pdata.get("swap_cmd_ctrl", True)

                    # Phase 3 D-01 / D-15 migration — pre-Phase-3 bookmarks
                    # lack the new display + clipboard fields. Dataclass
                    # defaults already pick the safe values (mirror_all +
                    # all clipboard toggles ON per D-16), but we mark the
                    # bookmark as migrated so _save() persists the new
                    # shape on this load — that prevents a subtle "user
                    # enables a toggle but it never persists because the
                    # field was never in the JSON" bug (Pitfall 9).
                    phase3_fields = (
                        "monitor_mode", "picked_monitor_id",
                        "picked_monitor_name",
                        "clipboard_text_c2s", "clipboard_text_s2c",
                        "clipboard_image_c2s", "clipboard_image_s2c",
                    )
                    if any(f not in pdata for f in phase3_fields):
                        migrated = True

                    # Phase 3 T-03-05 (STRIDE) — hand-edited JSON could
                    # drop a tampered ``monitor_mode`` value through
                    # ``ConnectionProfile.from_dict``; the dataclass
                    # has no enum enforcement. Whitelist-check here
                    # and revert to mirror_all with a logged warning.
                    prof = self._profiles[bid]
                    if prof.monitor_mode not in ("single", "mirror_all",
                                                 "pick_one"):
                        logger.warning(
                            "bookmark.invalid_monitor_mode bid=%s mode=%r "
                            "→ mirror_all", bid, prof.monitor_mode)
                        prof.monitor_mode = "mirror_all"
                        migrated = True
                logger.info("Loaded %d bookmarks", len(self._profiles))
                if migrated:
                    self._save()
            except Exception as e:
                logger.error("Failed to load bookmarks: %s", e)

    @staticmethod
    def default_swap_for_destination(destination_kind: str) -> bool:
        """Phase 2 D-10 default-swap policy for new bookmarks.

        Mac client → Linux server: Cmd↔Ctrl swap ON by default so Flame
        on Rocky sees Ctrl+S where the artist pressed Cmd+S.

        Mac client → Mac server: swap OFF by default — no translation
        needed because the destination interprets Cmd natively.
        """
        return destination_kind != "mac"

    def _save(self):
        """Save bookmarks to disk atomically.

        Phase 2 WR-01: a crash mid-write previously left a truncated
        ``bookmarks.json`` which `_load()` then silently tossed (the bare
        ``except Exception`` returns empty), wiping every saved bookmark
        with no warning. Use the standard write-tmp + fsync + os.replace
        pattern so the readable file is always fully-written.

        Note: this does not address multi-process write coordination
        (two simultaneous client instances on the same home directory
        would still last-writer-wins). Per-bookmark file or fcntl.flock
        is the next step if that surfaces; for now the single-client
        case is the documented v1 path.
        """
        try:
            data = {bid: p.to_dict() for bid, p in self._profiles.items()}
            tmp = self._bookmarks_file.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._bookmarks_file)
        except Exception as e:
            logger.error("Failed to save bookmarks: %s", e)

    def add(self, name: str, host: str, port: int = 443,
            username: str = "", password: str = "",
            use_tls: bool = False, **kwargs) -> str:
        """
        Add a new bookmark.

        Returns the bookmark ID.
        """
        bid = str(uuid.uuid4())[:8]
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        # Phase 2 D-10 — if the caller specified destination_kind but did
        # NOT explicitly pass swap_cmd_ctrl, set the swap default from the
        # destination policy. Lets the connection dialog stay simple
        # (just pick Linux vs Mac, swap follows automatically) while still
        # letting power users pass an explicit override.
        if "destination_kind" in kwargs and "swap_cmd_ctrl" not in kwargs:
            kwargs["swap_cmd_ctrl"] = self.default_swap_for_destination(
                kwargs["destination_kind"]
            )

        profile = ConnectionProfile(
            name=name,
            host=host,
            port=port,
            username=username,
            password_encrypted=_encrypt_password(password),
            use_tls=use_tls,
            created=now,
            **kwargs,
        )
        self._profiles[bid] = profile
        self._save()
        logger.info("Bookmark added: %s (%s:%d, swap_cmd_ctrl=%s)",
                    name, host, port, profile.swap_cmd_ctrl)
        return bid

    def update(self, bookmark_id: str, **kwargs):
        """Update an existing bookmark's fields."""
        profile = self._profiles.get(bookmark_id)
        if not profile:
            return

        # Handle password separately (needs encryption)
        if "password" in kwargs:
            kwargs["password_encrypted"] = _encrypt_password(kwargs.pop("password"))

        for key, value in kwargs.items():
            if hasattr(profile, key):
                setattr(profile, key, value)

        self._save()

    def remove(self, bookmark_id: str):
        """Remove a bookmark."""
        if bookmark_id in self._profiles:
            name = self._profiles[bookmark_id].name
            del self._profiles[bookmark_id]
            self._save()
            logger.info("Bookmark removed: %s", name)

    def get(self, bookmark_id: str) -> Optional[ConnectionProfile]:
        """Get a bookmark by ID."""
        return self._profiles.get(bookmark_id)

    def get_password(self, bookmark_id: str) -> str:
        """Get the decrypted password for a bookmark."""
        profile = self._profiles.get(bookmark_id)
        if profile:
            return _decrypt_password(profile.password_encrypted)
        return ""

    def list_all(self) -> List[tuple]:
        """Return all bookmarks as (id, profile) tuples, sorted by name."""
        items = list(self._profiles.items())
        items.sort(key=lambda x: x[1].name.lower())
        return items

    def search(self, query: str) -> List[tuple]:
        """Search bookmarks by name, host, or notes."""
        query = query.lower()
        results = []
        for bid, profile in self._profiles.items():
            searchable = f"{profile.name} {profile.host} {profile.notes}".lower()
            if query in searchable:
                results.append((bid, profile))
        results.sort(key=lambda x: x[1].name.lower())
        return results

    def mark_connected(self, bookmark_id: str):
        """Update last-connected timestamp."""
        profile = self._profiles.get(bookmark_id)
        if profile:
            profile.last_connected = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save()

    def export_bookmarks(self, filepath: str, include_passwords: bool = False):
        """Export bookmarks to a JSON file."""
        data = {}
        for bid, profile in self._profiles.items():
            d = profile.to_dict()
            if not include_passwords:
                d["password_encrypted"] = ""
            data[bid] = d

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Exported %d bookmarks to %s", len(data), filepath)

    def import_bookmarks(self, filepath: str):
        """Import bookmarks from a JSON file."""
        with open(filepath) as f:
            data = json.load(f)

        count = 0
        for bid, pdata in data.items():
            if bid not in self._profiles:
                self._profiles[bid] = ConnectionProfile.from_dict(pdata)
                count += 1
        self._save()
        logger.info("Imported %d new bookmarks from %s", count, filepath)

    @property
    def count(self) -> int:
        return len(self._profiles)
