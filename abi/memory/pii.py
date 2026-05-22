"""PII auto-detection for memory classification.

Pure regex-based detection (no external deps). Ported from
abi_loop/dlp/pii_detector.py (112 lines).
"""

import re

# Patterns for common PII types
_PATTERNS = [
    # Email addresses
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
    # Phone numbers (international)
    re.compile(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b'),
    # Credit card numbers (basic pattern)
    re.compile(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b'),
    # Social security / ID numbers
    re.compile(r'\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b'),
    # IP addresses
    re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'),
    # API keys (common prefixes)
    re.compile(r'\b(?:sk-|xkeysib-|ghp_|glpat-|AKIA)[A-Za-z0-9]{16,}\b'),
]


def classify_pii(text: str) -> bool:
    """Check if text contains PII.

    Returns True if any PII pattern matches.
    """
    for pattern in _PATTERNS:
        if pattern.search(text):
            return True
    return False
