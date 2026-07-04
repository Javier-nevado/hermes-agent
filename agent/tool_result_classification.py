"""Shared helpers for classifying tool result payloads."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, List, Optional


FILE_MUTATING_TOOL_NAMES = frozenset({"write_file", "patch"})


def file_mutation_result_landed(tool_name: str, result: Any) -> bool:
    """Return True when a file mutation result proves the write landed."""
    if tool_name not in FILE_MUTATING_TOOL_NAMES or not isinstance(result, str):
        return False
    try:
        data = json.loads(result.strip())
    except Exception:
        return False
    if not isinstance(data, dict) or data.get("error"):
        return False
    if tool_name == "write_file":
        return "bytes_written" in data
    if tool_name == "patch":
        return data.get("success") is True
    return False


# ---------------------------------------------------------------------------
# Phantom file-write detection
# ---------------------------------------------------------------------------
# Models (GLM-5.1, GPT-5.5) frequently mention files in their response text
# without actually calling write_file.  This scanner extracts those references
# so the conversation loop can inject a retry nudge.

_DELIVERABLE_EXTENSIONS = frozenset({
    ".html", ".htm", ".pdf", ".txt", ".md", ".csv",
    ".xlsx", ".docx", ".pptx", ".json",
    ".ps1", ".py", ".sh", ".bat", ".yaml", ".yml",
})

_MENTIONED_FILE_RE = re.compile(
    r'(?:^|[\s\'"`(>:/\*])'
    r'([\w][\w\-./]*)'
    r'(' + '|'.join(
        re.escape(e) for e in sorted(_DELIVERABLE_EXTENSIONS, key=len, reverse=True)
    ) + ')'
    r'(?:$|[\s\'"`)<\\,;.!?\]\*\(]|\.\.\.|\.$)',
    re.IGNORECASE,
)


def extract_deliverable_filenames_from_text(text: str) -> List[str]:
    """Scan response text for references to deliverable files.

    Returns lowercased basenames, deduplicated, in order of first appearance.
    E.g. text containing "content-plan.html" and "report.pdf" returns
    ["content-plan.html", "report.pdf"].
    """
    if not text or not isinstance(text, str):
        return []
    seen = set()
    results: List[str] = []
    for m in _MENTIONED_FILE_RE.finditer(text):
        basename = Path(m.group(1) + m.group(2)).name.lower()
        if basename not in seen:
            seen.add(basename)
            results.append(basename)
    return results


# ---------------------------------------------------------------------------
# Intent-gated phantom file detection (existence-based, method-agnostic)
# ---------------------------------------------------------------------------
# The legacy `extract_deliverable_filenames_from_text` above is intent-blind:
# ANY filename with a deliverable extension is treated as a write claim, which
# fires on files the model merely reads/references (robots.txt, skill.md) and
# on files created via execute_code/execute_shell (invisible to the
# write_file-only allowlist).  The helpers below gate on write-claim INTENT
# and verify existence on disk, so a file is only a "phantom" when the model
# CLAIMS it AND it genuinely does not exist -- regardless of how files are made.

# Verbs/phrases that signal a DELIVERY CLAIM (not a read/reference).  Matched
# case-insensitively as whole words.  Crucially, read/load/refer/mention are
# NOT here, so "I read robots.txt" / "load the skill at skill.md" never bind.
_CLAIM_INTENT_TERMS = (
    "creat(?:e|ed|ing|ion)",
    "sav(?:e|ed|ing)",
    "wrot?e",
    "writ(?:e|ten|ing)",
    "generat(?:e|ed|ing)",
    "download(?:ed|ing)?",
    "deliver(?:e|ed|ing|y)",
    "export(?:e|ed|ing)",
    "produc(?:e|ed|ing)",
    "attach(?:e|ed|ing)",
    "upload(?:e|ed|ing)?",
    "saved at",
    "saved to",
    "here(?:'s| is)? (?:your|the|a)",
    "the file is at",
    "is ready (?:at|in)",
)

# A filename token: basename OR absolute/~/ path, with a deliverable extension.
# Broader than _MENTIONED_FILE_RE so it catches "/tmp/x/report.pdf" and "~/o.csv".
_CLAIM_FILE_TOKEN_RE = re.compile(
    r'(?:~/[\w.\-./]*|/[\w.\-/]+|[\w][\w\-./]*)'
    + '(?:' + '|'.join(
        re.escape(e) for e in sorted(_DELIVERABLE_EXTENSIONS, key=len, reverse=True)
    ) + r')',
    re.IGNORECASE,
)

_URL_PREFIX_RE = re.compile(r'^https?://|^ftp://|^s3://', re.IGNORECASE)

# Claim intent must appear within this many chars of the filename token to bind.
_CLAIM_PROXIMITY_CHARS = 120


def extract_claimed_deliverable_files(text: str) -> List[str]:
    """Return filenames mentioned in a write/creation context (intent-gated).

    Unlike :func:`extract_deliverable_filenames_from_text` (extension-only),
    this requires a claim-intent verb (created/saved/wrote/...) within a small
    proximity window of the filename.  Reads, references, code samples, and
    URLs are excluded.  Returns raw tokens (basename or path), deduped, in
    order of first appearance.
    """
    if not text or not isinstance(text, str):
        return []
    # Strip fenced + inline code spans so code samples never count as claims.
    cleaned = re.sub(r'```[^\n]*\n.*?```', ' ', text, flags=re.DOTALL)
    cleaned = re.sub(r'`[^`\n]+`', ' ', cleaned)
    # Neutralize URLs entirely so "downloaded from https://x/report.pdf" does
    # not lift report.pdf out of the URL.
    cleaned = re.sub(r'https?://\S+|ftp://\S+|s3://\S+', ' ', cleaned)

    intent_re = re.compile(
        r'\b(?:' + '|'.join(_CLAIM_INTENT_TERMS) + r')\b', re.IGNORECASE,
    )
    seen: set = set()
    out: List[str] = []
    for m in _CLAIM_FILE_TOKEN_RE.finditer(cleaned):
        token = m.group(0).strip().strip('`"\'')
        if not token or _URL_PREFIX_RE.match(token):
            continue
        # Require an intent term within +/- proximity chars of the token.
        lo = max(0, m.start() - _CLAIM_PROXIMITY_CHARS)
        hi = min(len(cleaned), m.end() + _CLAIM_PROXIMITY_CHARS)
        if not intent_re.search(cleaned[lo:hi]):
            continue
        key = token.lower()
        if key not in seen:
            seen.add(key)
            out.append(token)
    return out


def resolve_claimed_file_to_path(name: str) -> Optional[str]:
    """Resolve a model-mentioned filename to an existing absolute path.

    Method-agnostic: works whether the file was created via write_file,
    execute_code, execute_shell, or a plugin.  Returns the validated absolute
    path if the file exists and is safe to deliver, else None.  Returns None
    for URLs/remote references, non-existent files, and denylisted paths.

    Delegates safety (denylist + allowed-roots + strict-mode recency) to the
    delivery pipeline's ``validate_media_delivery_path`` so a file only counts
    as "delivered" when it would actually be attachable -- keeping resolution
    consistent with the delivery pipeline regardless of how the file was made.
    """
    if not name or not isinstance(name, str):
        return None
    cand = name.strip().strip('`"\'')
    if not cand:
        return None
    if (
        _URL_PREFIX_RE.match(cand)
        or cand.startswith(('http://', 'https://', 'ftp://', 's3://'))
    ):
        return None  # remote reference -- not a local deliverable

    # Lazy import to avoid any agent<->gateway import cycle at module load.
    from gateway.platforms.base import (
        _media_delivery_allowed_roots,
        validate_media_delivery_path,
    )

    expanded = os.path.expanduser(cand)

    # Case 1: absolute or ~/-relative path -> validate directly.
    if os.path.isabs(expanded) or cand.startswith('~/'):
        return validate_media_delivery_path(expanded)

    # Case 2: bare basename -> search allowed roots + cwd + tmp for a match.
    basename = os.path.basename(expanded)
    search_dirs: List[Path] = list(_media_delivery_allowed_roots())
    try:
        search_dirs.append(Path(os.getcwd()).resolve())
    except OSError:
        pass
    search_dirs.append(Path(tempfile.gettempdir()))
    search_dirs.append(Path('/tmp'))

    seen_dirs: set = set()
    for d in search_dirs:
        try:
            d_resolved = d.resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if d_resolved in seen_dirs:
            continue
        seen_dirs.add(d_resolved)
        candidate = d_resolved / basename
        if candidate.is_file():
            validated = validate_media_delivery_path(str(candidate))
            if validated:
                return validated
    return None
