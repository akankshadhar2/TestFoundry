"""Project root, .env loading, provider loading, paths and the Claude model name."""

import importlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "claude-sonnet-5-5"

_env_loaded = False
_provider = None


def load_env() -> None:
    """Load ROOT/.env into os.environ (real environment variables win). Never prints values."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    path = ROOT / ".env"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and value and not os.environ.get(key):
            os.environ[key] = value


def model() -> str:
    load_env()
    return os.environ.get("CLAUDE_MODEL", "").strip() or DEFAULT_MODEL


def provider_name() -> str:
    load_env()
    return os.environ.get("DEMO_PROVIDER", "").strip() or "stripe"


def load_provider():
    """Import providers/<DEMO_PROVIDER>_provider.py (cached)."""
    global _provider
    if _provider is None:
        load_env()
        _provider = importlib.import_module(f"providers.{provider_name()}_provider")
    return _provider


# ---- paths (all derived from the provider NAME) ---------------------------------

def backup_dir(p) -> Path:
    return ROOT / "backup" / p.NAME


def backup_fixtures_dir(p) -> Path:
    return backup_dir(p) / "fixtures"


def backup_recordings_dir(p) -> Path:
    return backup_dir(p) / "recordings"


def backup_fake_path(p) -> Path:
    return backup_dir(p) / f"fake_{p.NAME}.py"


def generated_dir(p) -> Path:
    return ROOT / "generated" / p.NAME


def fixtures_dir(p) -> Path:
    return generated_dir(p) / "fixtures"


def fake_path(p) -> Path:
    return generated_dir(p) / f"fake_{p.NAME}.py"


def recordings_dir(p) -> Path:
    return ROOT / "recordings" / p.NAME


def record_dir(p) -> Path:
    return recordings_dir(p) / "record"


def real_run_path(p) -> Path:
    return recordings_dir(p) / "real_run.json"


def rel(path: Path) -> str:
    """Path relative to ROOT, with forward slashes, for printing."""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)
