#!/usr/bin/env python3
"""stub_fingerprint.py — test stand-in for abi-fingerprint.py (NO root needed).

abi-license-gate.py computes the fingerprint by shelling out to a helper that
reads the mode-0400 DMI product_uuid (root-only). For tests we don't want to be
root or touch hardware, so the gate accepts ABI_FINGERPRINT_HELPER pointing here:
this stub just echoes $STUB_FP (set per-case by the test driver). Same stdout
contract as the real helper — bare hex fingerprint, exit 0; exit 2 if STUB_FP
unset (mirrors abi-fingerprint.py's "no signal" failure).

Stdlib only.
"""
import os
import sys


def main():
    fp = os.environ.get("STUB_FP", "").strip()
    if not fp:
        print("[fingerprint-stub] STUB_FP unset", file=sys.stderr)
        sys.exit(2)
    print(fp)


if __name__ == "__main__":
    main()
