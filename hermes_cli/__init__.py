"""
Hermes CLI - Unified command-line interface for Hermes Agent.

Provides subcommands for:
- hermes chat          - Interactive chat (same as ./hermes)
- hermes gateway       - Run gateway in foreground
- hermes gateway start - Start gateway service
- hermes gateway stop  - Stop gateway service
- hermes setup         - Interactive setup wizard
- hermes status        - Show status of all components
- hermes cron          - Manage cron jobs
"""

import os
import sys

from hermes_cli.build_info import get_abi_version

__release_date__ = "2026.5.29"
# The Hermes framework (upstream Nous fork) version. Used for the HTTP
# User-Agent sent to LLM providers (models.py / model_catalog.py) and shown as
# the "Hermes framework vX" lineage in `hermes --version`. Deliberately NOT
# bumped with the ABI image tag, so the UA stays stable across releases.
__framework_version__ = "0.16.0"
# The version the agent REPORTS as its own. In a published ABI image this is the
# image tag (baked at build into .hermes_build_version); in a source install it
# falls back to the framework version. Everything that answers "what version am
# I" — `hermes_cli.__version__`, `hermes --version`, `hermes dump`, the startup
# banner — reads THIS, so an LLM introspecting the runtime gets the ABI version.
__version__ = get_abi_version() or __framework_version__


def _ensure_utf8():
    """Force UTF-8 stdout/stderr on Windows to prevent UnicodeEncodeError.

    Windows services and terminals default to cp1252, which cannot encode
    box-drawing characters used in CLI output. This causes unhandled
    UnicodeEncodeError crashes on gateway startup.
    """
    if sys.platform != "win32":
        return
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            if getattr(stream, "encoding", "").lower().replace("-", "") != "utf8":
                new_stream = open(
                    stream.fileno(), "w", encoding="utf-8",
                    buffering=1, closefd=False,
                )
                setattr(sys, stream_name, new_stream)
        except (AttributeError, OSError):
            pass


_ensure_utf8()
