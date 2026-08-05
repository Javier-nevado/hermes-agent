#!/usr/bin/env python3
"""Extract a persona-name slug from an agent's SOUL.md.

provision-vault.sh uses this to name the agent's Passbolt account after its actual
persona (e.g. 'ailean') when one is defined, instead of the generic abi-<customer>
fallback. A real persona is ALREADY distinct from any human, so it needs no 'abi-'
prefix; the prefix is only the fallback for a nameless generic agent.

Prints the sanitized slug to stdout if a real persona name is found. Prints nothing
(exit 0) when: there is no SOUL, the SOUL is empty/only-comments, or the only
"name" is a generic default ('Hermes Agent', 'Agent', 'ABI', …). The caller then
falls back to abi-<slug>@<fqdn>.

Conservative by design — a name extracted by mistake is worse than the fallback, so
this errs toward printing nothing.

Usage:
  passbolt_agent_name.py /opt/data/SOUL.md
  cat SOUL.md | passbolt_agent_name.py
"""
from __future__ import annotations

import re
import sys

# "Names" that are generic defaults / product words, not real personas → reject.
GENERIC = {
    "hermes", "agent", "hermes-agent", "hermesagent", "abi", "abi-agent",
    "assistant", "claude", "ai", "soul", "you", "hermes-agent-persona",
    "nous", "opteia", "bot",
}
# Words that follow "I am …" but are not a name ("I am an AI agent", "I am your …").
STOPWORDS = {
    "an", "a", "the", "your", "my", "i", "we", "this", "its", "his", "her",
    "here", "not", "also", "now", "very",
}


def candidates(text: str):
    """Yield raw persona-name candidates from a SOUL.md, best signal first."""
    # 1. H1 heading, e.g. "# AIlean — Soul & Identity" → "AIlean".
    m = re.search(r"^#\s+(.+)$", text, re.M)
    if m:
        head = re.split(r"\s+[—\-|]\s+", m.group(1), maxsplit=1)[0].strip()
        if head:
            yield head
    # 2. "My name is AIlean" (any case).
    for m in re.finditer(r"my name is\s+([A-Za-z][A-Za-z0-9_.\-']+)", text, re.I):
        yield m.group(1)
    # 3. "I am AIlean" — skip stopwords ("I am an AI agent" → "an" dropped).
    for m in re.finditer(r"\bI am\s+([A-Za-z][A-Za-z0-9_.\-']+)", text):
        if m.group(1).lower() not in STOPWORDS:
            yield m.group(1)


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s).strip("-")
    return s


def is_generic(slug: str) -> bool:
    base = slug.replace("-", "")
    if slug in GENERIC or base in GENERIC:
        return True
    if "hermes" in slug or slug == "agent":
        return True
    return False


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] not in ("-", ""):
        try:
            text = open(sys.argv[1], encoding="utf-8").read()
        except OSError:
            return 0  # no file → no name → caller falls back
    else:
        text = sys.stdin.read()

    # The image-default SOUL.md is all HTML comments → treat as empty.
    if not re.sub(r"<!--.*?-->", "", text, flags=re.S).strip():
        return 0

    for name in candidates(text):
        slug = slugify(name)
        if not (2 <= len(slug) <= 56):
            continue
        if is_generic(slug):
            continue
        print(slug)
        return 0
    return 0  # no real persona name found


if __name__ == "__main__":
    sys.exit(main())
