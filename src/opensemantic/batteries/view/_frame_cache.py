"""On-disk cache for dataset frames loaded from a wiki.

Holding downloads in memory only means every restart fetches every dataset
again, which is the slowest thing the dashboard does. The frames are written
to disk instead, in the dequantified CSV form the dataset controller reads and
writes, so an entry carries its units, survives a restart and can be read with
any text editor.

An entry is only used when its fingerprint still matches the one the wiki
reports for that page, so a dataset that changed is re-downloaded rather than
served stale.
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger("opensemantic.batteries.view")


def default_cache_dir() -> Path:
    """Where frames are cached unless the caller names a directory."""
    import tempfile

    return Path(tempfile.gettempdir()) / "opensemantic-batteries-frames"


class FrameCache:
    """Dataset frames on disk, keyed by page title and fingerprint."""

    def __init__(self, directory: Optional[Path] = None) -> None:
        self.directory = Path(directory) if directory else default_cache_dir()

    def _paths(self, key: str):
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return (
            self.directory / f"{digest}.csv",
            self.directory / f"{digest}.json",
        )

    def get(self, key: str, fingerprint: str) -> Optional[Any]:
        """The cached frame for ``key``, or ``None``.

        ``None`` when nothing is cached, when the fingerprint moved on, or
        when the entry cannot be read - a damaged cache costs a download, not
        an error.
        """
        data_path, meta_path = self._paths(key)
        if not data_path.exists() or not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("fingerprint") != fingerprint:
                _log.debug("cache %s: fingerprint changed", key)
                return None
            from opensemantic.base import _pint_csv

            payload = data_path.read_bytes()
            # every entry is written with the unit header; without it the file
            # is not one of ours, and pandas would happily parse the damage
            # into a frame of nonsense rather than raise
            if not _pint_csv.has_unit_row(payload):
                _log.warning("cache %s is not a cached frame, re-reading", key)
                return None
            frame = _pint_csv.read_csv(payload)
            _log.info("cache %s: %d rows from disk", key, len(frame))
            return frame
        except Exception as exc:  # noqa: BLE001 - a bad entry is not an error
            _log.warning("cache %s unreadable, re-reading: %s", key, exc)
            return None

    def put(self, key: str, fingerprint: str, frame: Any) -> None:
        """Write a frame to the cache, ignoring a cache that cannot be written."""
        if frame is None:
            return
        data_path, meta_path = self._paths(key)
        try:
            from opensemantic.base import _pint_csv

            self.directory.mkdir(parents=True, exist_ok=True)
            data_path.write_bytes(_pint_csv.to_csv(frame))
            meta_path.write_text(
                json.dumps({"key": key, "fingerprint": fingerprint}),
                encoding="utf-8",
            )
            _log.debug("cache %s: %d rows written", key, len(frame))
        except Exception as exc:  # noqa: BLE001 - caching is an optimization
            _log.warning("cache %s could not be written: %s", key, exc)

    def clear(self) -> None:
        """Drop every entry."""
        if not self.directory.exists():
            return
        for path in self.directory.glob("*"):
            if path.suffix in (".csv", ".json"):
                path.unlink()


def fingerprint_of(entity: Any) -> str:
    """What identifies the content of a dataset page.

    Built from what its distributions report - the file they name and how many
    bytes it has - so a re-uploaded dataset does not match its old entry. A
    dataset that carries its rows inline has no distribution and is
    fingerprinted by its row count, which is all that is known without reading
    them.
    """
    parts = []
    for dist in getattr(entity, "distributions", None) or []:
        for attribute in ("download_url", "access_url", "byte_size", "media_type"):
            value = getattr(dist, attribute, None)
            if value is not None:
                parts.append(f"{attribute}={value}")
        file = getattr(dist, "file", None)
        name = getattr(file, "name", None) or getattr(file, "uuid", None)
        if name is not None:
            parts.append(f"file={name}")
    if not parts:
        parts.append(f"inline={len(getattr(entity, 'data', None) or [])}")
    return "|".join(str(part) for part in parts)
