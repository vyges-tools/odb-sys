#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Sparse-checkout ONLY the OpenROAD dirs needed for the standalone libodb,
# pinned to the SHA in openroad-pin.yaml. Blobless + cone sparse => ~24MB.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
dest="${1:-$here/vendor/OpenROAD}"

sha="$(awk '/^[[:space:]]*commit:/{print $2; exit}' "$here/openroad-pin.yaml")"
src="$(awk '/^[[:space:]]*source:/{print $2; exit}' "$here/openroad-pin.yaml")"
[ -n "$sha" ] || { echo "no commit: in openroad-pin.yaml" >&2; exit 1; }

echo "pin: $src @ $sha"
mkdir -p "$(dirname "$dest")"
if [ ! -d "$dest/.git" ]; then
  git clone --quiet --filter=blob:none --no-checkout "$src" "$dest"
fi
cd "$dest"
git sparse-checkout set --cone src/odb src/utl cmake
git checkout --quiet "$sha"
echo "OpenROAD odb subtree @ $(git rev-parse --short HEAD) -> $dest ($(du -sh . | cut -f1))"
