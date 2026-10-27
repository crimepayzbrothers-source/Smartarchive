"""Dateiablage. Originale werden unter ihrer Prüfsumme gespeichert und nie verändert."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from . import config

MIME_EXT = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/tiff": ".tif",
}


def sniff_mime(data: bytes, fallback: str = "application/octet-stream") -> str:
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypmsf1"):
        return "image/heic"
    return fallback


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(target: Path, data: bytes, readonly: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if readonly:
            os.chmod(tmp, 0o444)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def store_original(data: bytes, mime: str) -> tuple[str, str]:
    """Speichert ein Original und liefert (sha256, relativer Pfad)."""
    digest = sha256_bytes(data)
    rel = Path("originals") / digest[:2] / digest[2:4] / (digest + MIME_EXT.get(mime, ".bin"))
    target = config.settings.files_dir / rel
    if not target.exists():
        _atomic_write(target, data, readonly=True)
    return digest, str(rel)


def store_derived(document_id: int, name: str, src: Path) -> tuple[str, str, int]:
    """Kopiert eine abgeleitete Datei (z. B. durchsuchbares PDF) in die Ablage."""
    rel = Path("derived") / str(document_id) / name
    target = config.settings.files_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, target)
    return sha256_file(target), str(rel), target.stat().st_size


def abs_path(rel: str) -> Path:
    path = (config.settings.files_dir / rel).resolve()
    root = config.settings.files_dir.resolve()
    if root not in path.parents:
        raise ValueError("Pfad außerhalb der Ablage")
    return path


def page_cache_dir(sha: str) -> Path:
    d = config.settings.cache_dir / "pages" / sha
    d.mkdir(parents=True, exist_ok=True)
    return d
