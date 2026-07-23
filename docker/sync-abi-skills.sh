#!/bin/sh
# docker/sync-abi-skills.sh — stage the Opteia shared skills into the build context.
#
# The shared skills live in a SEPARATE repo (Javier-nevado/abi-skills, private) so
# they are NOT committed to this hermes-agent repo (see .gitignore: abi-tools-skills/).
# Before `docker build`, this script copies abi-skills/skills/ -> ./abi-tools-skills/,
# which the Dockerfile bakes into the image at /opt/abi-tools/skills (read-only). This
# keeps the abi-skills repo the single canonical source — the hermes repo never
# vendors a copy that can drift.
#
# Usage (from the repo root, or via release-upload.sh):
#   ABI_SKILLS_DIR=/path/to/abi-skills docker/sync-abi-skills.sh
# Default ABI_SKILLS_DIR=../abi-skills (sibling checkout).
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ABI_SKILLS_DIR=${ABI_SKILLS_DIR:-"$ROOT/../abi-skills"}
SRC="$ABI_SKILLS_DIR/skills"
DST="$ROOT/abi-tools-skills"

if [ ! -d "$SRC" ]; then
    echo "[sync-abi-skills] source not found: $SRC" >&2
    echo "[sync-abi-skills] set ABI_SKILLS_DIR to a checkout of Javier-nevado/abi-skills" >&2
    exit 1
fi

rm -rf "$DST"
mkdir -p "$DST"
cp -a "$SRC/." "$DST/"

n=$(find "$DST" -name SKILL.md | wc -l | tr -d ' ')
echo "[sync-abi-skills] staged $n skills ($DST) from $ABI_SKILLS_DIR"
