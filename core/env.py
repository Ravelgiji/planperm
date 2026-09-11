"""Local environment loading.

The agents read `OPENAI_API_KEY` straight from `os.environ`, and LOCAL_SETUP.md
tells you to put it in `.env` - but nothing was reading that file, so the key
never arrived and every agent silently fell back to deterministic output.

Parsed by hand rather than adding a python-dotenv dependency for a dozen lines
of work. Real environment variables win, so `OPENAI_API_KEY=... streamlit run`
and CI secrets still override the file.
"""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_loaded = False


def load_env(force: bool = False) -> bool:
    """Read `.env` at the project root into the environment. Idempotent.

    Returns True if the file existed and was read.
    """
    global _loaded

    if _loaded and not force:
        return True

    env_path = _PROJECT_ROOT / ".env"
    if not env_path.is_file():
        _loaded = True
        return False

    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value

    _loaded = True
    return True


def llm_configured() -> bool:
    load_env()
    return bool(os.environ.get("OPENAI_API_KEY"))


def llm_label() -> str:
    """Describe the active backend, for display in the UI."""
    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        return "no API key - deterministic mode"
    model = os.environ.get("PLANPERM_LLM_MODEL", "gpt-4.1-mini")
    host = os.environ.get("PLANPERM_LLM_BASE_URL") or "api.openai.com"
    return f"{model} via {host}"
