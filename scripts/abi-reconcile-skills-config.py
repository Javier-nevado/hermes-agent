#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
abi-reconcile-skills-config — ensure every agent's config.yaml loads the SHARED
skills dir (/opt/abi-tools/skills) via `skills.external_dirs`.

WHY
  Provisioning (abi/provision/agent.py) writes `skills.external_dirs:
  [/opt/abi-tools/skills]`. But older provisions or hand-edits left it `[]`. With it
  empty the agent never scans the shared `core/` skills (incl. self-update) and may
  improvise an update (pip/git) — the Snowbytes "Frost" 2026-07-22 incident. This
  reconciles that drift idempotently so the signed self-update path is always visible.

SAFETY
  Round-trips with ruamel.yaml (preserves comments + formatting). Only ever ADDS
  /opt/abi-tools/skills to external_dirs — never removes an existing entry. Exits 0
  always (best-effort); never aborts a deploy/update.

USAGE (host venv has ruamel; system python3 may not):
  /opt/hermes-agent/.venv/bin/python abi-reconcile-skills-config.py            # all /home/*/.hermes/config.yaml
  /opt/hermes-agent/.venv/bin/python abi-reconcile-skills-config.py --user snowbytes
  /opt/hermes-agent/.venv/bin/python abi-reconcile-skills-config.py /path/to/config.yaml

Author: Major Tom — Opteia Ground Control.
"""
import glob
import os
import sys

SHARED_DIR = "/opt/abi-tools/skills"


def _load_ruamel():
    try:
        from ruamel.yaml import YAML
        y = YAML()
        y.preserve_quotes = True
        return y
    except Exception as e:
        sys.stderr.write(f"[reconcile] ruamel.yaml unavailable: {e}\n")
        return None


def reconcile_config(path, yaml):
    """Ensure skills.external_dirs contains SHARED_DIR. Returns True if changed."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.load(f)
    except Exception as e:
        sys.stderr.write(f"[reconcile] skip {path}: {e}\n")
        return False
    if not isinstance(data, dict):
        sys.stderr.write(f"[reconcile] skip {path}: top-level not a mapping\n")
        return False

    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    skills = data.get("skills")
    if skills is None:
        skills = CommentedMap()
        data["skills"] = skills
    if not isinstance(skills, dict):
        sys.stderr.write(f"[reconcile] skip {path}: 'skills' not a mapping\n")
        return False

    ed = skills.get("external_dirs")
    if ed is None:
        ed = CommentedSeq()
        skills["external_dirs"] = ed
    # Normalize current entries to strings.
    if isinstance(ed, list):
        have = [str(x) for x in ed]
    elif ed == []:
        have = []
    else:
        have = [str(ed)]

    if SHARED_DIR in have:
        return False  # already correct — idempotent no-op

    if isinstance(ed, list):
        ed.append(SHARED_DIR)
    else:
        new = CommentedSeq()
        for x in have:
            new.append(x)
        new.append(SHARED_DIR)
        skills["external_dirs"] = new

    try:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
    except Exception as e:
        sys.stderr.write(f"[reconcile] write failed {path}: {e}\n")
        return False
    print(f"[reconcile] {path}: added {SHARED_DIR} to skills.external_dirs")
    return True


def main():
    yaml = _load_ruamel()
    if yaml is None:
        sys.exit(0)

    argv = sys.argv[1:]
    if argv and argv[0] == "--user" and len(argv) > 1:
        targets = [f"/home/{argv[1]}/.hermes/config.yaml"]
    elif argv and not argv[0].startswith("-"):
        targets = [argv[0]]
    else:
        targets = sorted(glob.glob("/home/*/.hermes/config.yaml"))

    existing = [t for t in targets if os.path.exists(t)]
    if not existing:
        sys.stderr.write("[reconcile] no agent configs found\n")
        sys.exit(0)

    changed = sum(1 for t in existing if reconcile_config(t, yaml))
    print(f"[reconcile] done: {changed}/{len(existing)} config(s) reconciled "
          f"({'no changes needed' if changed == 0 else 'shared skills dir wired'})")
    sys.exit(0)


if __name__ == "__main__":
    main()
