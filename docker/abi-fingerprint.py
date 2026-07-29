#!/usr/bin/env python3
"""abi-fingerprint.py — compute the machine fingerprint for v4 license binding.

    fingerprint = sha256( DMI product_uuid  [+ primary NIC MAC if ABI_HOST_MAC set] )

WHY: v4 licensing is machine-fingerprint-bound (replaces seat-count). A clone
gets a NEW product_uuid (Proxmox regenerates it on clone) → new fingerprint →
license `verify` fails → the clone can't run. See
workspace/abi-v4-fingerprint-licensing/PLAN.md + memory/abi-v4-fingerprint-licensing.

ROOT-ONLY: product_uuid is mode 0400 (root). The license gate already runs as
root at boot (v4 main-wrapper / v3 ExecStartPre) and shells out to this helper.
A non-root agent never reads it.

LIVE EACH SESSION: recomputed from hardware every boot — NEVER cached to disk
(a cloner copying the disk gets a different product_uuid → different fingerprint).

v4 CONTAINER CAVEAT: the gate runs as root IN the container and reads the HOST
product_uuid via /sys (Docker shares it — verified on .19: the container sees the
same product_uuid as the host). But the container CANNOT see the host NIC MAC
(container net is veth; /sys/class/net shows ephemeral veth MACs). So v4 defaults
to product_uuid-ONLY — still clone-proof (product_uuid is the PRIMARY signal; MAC
was defense-in-depth). To add MAC, pass the host MAC via ABI_HOST_MAC env from a
host-side step (bare-metal v3 keeps uuid+mac).

Exit 0 + prints the hex fingerprint on success; exit 2 + stderr on failure (no
product_uuid → the license gate fails closed: container won't start).

Stdlib-only — runs on the bare product python before any venv.
"""
import hashlib
import os
import sys

UUID_PATH = "/sys/class/dmi/id/product_uuid"


def _read_product_uuid():
    try:
        with open(UUID_PATH) as f:
            v = f.read().strip()
        if v:
            return v.lower()
    except OSError as e:
        print(f"[fingerprint] cannot read {UUID_PATH}: {e}", file=sys.stderr)
        print("[fingerprint] (need root; product_uuid is mode 0400)", file=sys.stderr)
    return ""


def compute():
    """Return the hex fingerprint, or '' if product_uuid is unavailable."""
    uuid_ = _read_product_uuid()
    if not uuid_:
        return ""
    mac = os.environ.get("ABI_HOST_MAC", "").strip().lower()
    signal = f"{uuid_}:{mac}" if mac else uuid_
    return hashlib.sha256(signal.encode()).hexdigest()


def main():
    fp = compute()
    if not fp:
        print("[fingerprint] FATAL: no product_uuid — cannot bind a license "
              "(not a VM with DMI, or not running as root).", file=sys.stderr)
        sys.exit(2)
    # bare fingerprint on stdout (no decoration) so the gate can capture it directly
    print(fp)


if __name__ == "__main__":
    main()
