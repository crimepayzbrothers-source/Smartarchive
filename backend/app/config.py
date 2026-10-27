"""Zentrale Konfiguration aus Umgebungsvariablen."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "ja", "on"}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", "./data")).resolve())
    setup_token: str = field(default_factory=lambda: os.environ.get("SETUP_TOKEN", ""))
    mistral_api_key: str = field(default_factory=lambda: os.environ.get("MISTRAL_API_KEY", ""))
    mistral_model: str = field(default_factory=lambda: os.environ.get("MISTRAL_MODEL", "mistral-small-latest"))
    mistral_base_url: str = field(default_factory=lambda: os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1"))
    cookie_secure: bool = field(default_factory=lambda: _bool("COOKIE_SECURE", True))
    static_dir: Path = field(default_factory=lambda: Path(os.environ.get("STATIC_DIR", Path(__file__).resolve().parent.parent / "static")))
    max_upload_mb: int = field(default_factory=lambda: int(os.environ.get("MAX_UPLOAD_MB", "40")))
    session_days: int = field(default_factory=lambda: int(os.environ.get("SESSION_DAYS", "30")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "smartarchive.sqlite3"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def ai_available(self) -> bool:
        return bool(self.mistral_api_key)


settings = Settings()


def reload_settings() -> Settings:
    """Für Tests: Einstellungen neu aus der Umgebung lesen."""
    global settings
    settings = Settings()
    return settings
