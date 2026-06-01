"""Shared helpers for classifying tool result payloads."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, List


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
