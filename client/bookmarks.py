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
                for bid, pdata in data.items():
                    self._profiles[bid] = ConnectionProfile.from_dict(pdata)
                logger.info("Loaded %d bookmarks", len(self._profiles))
            except Exception as e:
                logger.error("Failed to load bookmarks: %s", e)

    def _save(self):
        """Save bookmarks to disk."""
        try:
            data = {bid: p.to_dict() for bid, p in self._profiles.items()}
            with open(self._bookmarks_file, "w") as f:
                json.dump(data, f, indent=2)
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
        logger.info("Bookmark added: %s (%s:%d)", name, host, port)
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


class UISettings:
    """Persist lightweight UI state (dock visibility, window layout) to ui_settings.json.

    All methods are class-level; no instantiation needed.

    Usage:
        data = UISettings.load()
        data["bookmarks_visible"] = False
        UISettings.save(data)
    """

    _FILE = "ui_settings.json"

    @classmethod
    def _path(cls) -> Path:
        return _get_config_dir() / cls._FILE

    @classmethod
    def load(cls) -> dict:
        try:
            with open(cls._path()) as f:
                return json.load(f)
        except Exception:
            return {}

    @classmethod
    def save(cls, data: dict) -> None:
        try:
            with open(cls._path(), "w") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.warning("UISettings: failed to save: %s", exc)
