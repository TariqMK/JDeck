#!/usr/bin/env bash
# Prints the next release tag (vX.Y.Z). Reads existing tags from stdin, one per line,
# ignores anything not in vX.Y.Z format, and bumps the highest by $1: major, minor or patch.
set -euo pipefail

bump="${1:-patch}"
highest="$(grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's/^v//' | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1 || true)"
highest="${highest:-0.0.0}"
IFS=. read -r major minor patch <<< "$highest"

case "$bump" in
  major) major=$((major + 1)); minor=0; patch=0 ;;
  minor) minor=$((minor + 1)); patch=0 ;;
  patch) patch=$((patch + 1)) ;;
  *) echo "Unknown bump type: $bump (use major, minor or patch)" >&2; exit 1 ;;
esac

echo "v${major}.${minor}.${patch}"
