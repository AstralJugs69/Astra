#!/usr/bin/env bash
# Build the exact Liblouis release required by Braille Errata Relay in WSL.
#
# This script changes only the local WSL development floor. It does not invoke
# CUPS, inspect jobs, or interact with any production device.
set -euo pipefail

LIBLOUIS_VERSION="3.38.0"
LIBLOUIS_COMMIT="07c61e58cfb8814f6842c7212063f829288638c1"
SOURCE_DIR="/usr/local/src/braille-relay-liblouis-3.38.0"
BUILD_DIR="$SOURCE_DIR/build"
PREFIX="/opt/liblouis-3.38.0"
PYTHON_TARGET="/opt/liblouis-python-3.38.0"
LD_CONFIG="/etc/ld.so.conf.d/braille-relay-liblouis-3.38.0.conf"
UEB_TABLE_SHA256="45b83481438667b57f57793d9369aeb44a5e5c50980767c69ba99ea83f442059"
BRF_TABLE_SHA256="47ff9400f4b8a0206b0c1942e13c32b539d17ec099dcd1097873a1b9f5cb8b3c"

fail() {
  echo "BLOCKED: $*" >&2
  exit 1
}

if [[ "$(id -u)" -ne 0 ]]; then
  fail "run with sudo from WSL: sudo bash infra/wsl/setup_liblouis_3_38.sh"
fi

apt-get update
apt-get install -y --no-install-recommends \
  autoconf automake build-essential ca-certificates gettext git libtool pkg-config \
  python3-dev python3-pip python3-venv

if [[ -e "$SOURCE_DIR" && ! -d "$SOURCE_DIR/.git" ]]; then
  fail "existing source path is not the pinned Liblouis checkout: $SOURCE_DIR"
fi
if [[ ! -d "$SOURCE_DIR/.git" ]]; then
  install -d -m 0755 "$(dirname "$SOURCE_DIR")"
  git clone --depth 1 --branch "v$LIBLOUIS_VERSION" \
    https://github.com/liblouis/liblouis.git "$SOURCE_DIR"
fi

actual_commit="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
[[ "$actual_commit" == "$LIBLOUIS_COMMIT" ]] || {
  fail "Liblouis checkout commit differs from the required pin"
}

cd "$SOURCE_DIR"
./autogen.sh
install -d -m 0755 "$BUILD_DIR"
cd "$BUILD_DIR"
"$SOURCE_DIR/configure" --prefix="$PREFIX"
make -j2
make install
install -m 0644 "$SOURCE_DIR/python/README.md" "$BUILD_DIR/python/README.md"
python3 -m pip install --no-cache-dir --upgrade --target "$PYTHON_TARGET" "$BUILD_DIR/python"

printf '%s\n' "$PREFIX/lib" > "$LD_CONFIG"
ldconfig

env \
  PYTHONPATH="$PYTHON_TARGET" \
  LD_LIBRARY_PATH="$PREFIX/lib" \
  LIBLOUIS_TABLEPATH="$PREFIX/share/liblouis/tables" \
  python3 - "$PREFIX/share/liblouis/tables" "$LIBLOUIS_VERSION" "$UEB_TABLE_SHA256" "$BRF_TABLE_SHA256" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import louis

table_root = Path(sys.argv[1])
expected_version = sys.argv[2]
expected_hashes = {
    "en-ueb-g2.ctb": sys.argv[3],
    "en-us-brf.dis": sys.argv[4],
}
if str(louis.version()) != expected_version:
    raise SystemExit("BLOCKED: Liblouis version verification failed")
for name, expected_hash in expected_hashes.items():
    actual_hash = hashlib.sha256((table_root / name).read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise SystemExit(f"BLOCKED: table hash verification failed for {name}")
mode = int(louis.dotsIO) | int(louis.ucBrl)
translated = louis.translateString(["en-ueb-g2.ctb"], "Gate 0 smoke", mode=mode)
if not translated or any(not 0x2800 <= ord(cell) <= 0x283F for cell in translated):
    raise SystemExit("BLOCKED: Unicode six-dot translation smoke test failed")
print("PASS: pinned Liblouis version, table hashes, and Unicode smoke translation verified")
PY

echo "PASS: installed Liblouis $LIBLOUIS_VERSION at $PREFIX"
echo "NEXT: source infra/wsl/liblouis_env.sh before local Relay commands"
