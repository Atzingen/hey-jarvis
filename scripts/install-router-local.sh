#!/usr/bin/env bash
# Explicit opt-in; never called by the standard Jarvis installer.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PREFIX=${1:-"$HOME/.local/share/jarvis/router-local"}
PYTHON=${JARVIS_ROUTER_PYTHON:-python3}
if [[ $("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")') != 3.11 ]]; then
  echo 'Use Python 3.11: JARVIS_ROUTER_PYTHON=/path/to/python3.11 jarvis router install-local' >&2
  exit 2
fi
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo 'This CPU dependency lock supports Linux x86_64.' >&2
  exit 2
fi
umask 077
mkdir -p "$PREFIX"
"$PYTHON" -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/python" -m pip install --extra-index-url https://download.pytorch.org/whl/cpu --require-hashes --no-deps -r "$HERE/requirements-router-local.lock"
"$PREFIX/venv/bin/python" - "$PREFIX" <<'PY'
import hashlib
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

REPOSITORY = 'fastino/GLiNER2.5-multi-Decide'
REVISION = 'a35a0cd3b7a0f00f2effc576f454cd48fa98aa5f'
# Exactly the files the classifier loads, with the size and SHA-256 of this revision.
FILES = [
    ('config.json', 3152, 'be5123080c0f3f01b938bc46a5dd0d7a2e515a34f6df798dfd70ed04c277c8bf'),
    ('encoder_config/config.json', 858, 'd0ebbcb8b458e285a39e12cc315cbaf3d1c6f631e7281e6b22dd5b4071183f83'),
    ('tokenizer_config.json', 646, 'fd4a31dc2f1f17e31638c5f0e783b81cdb2fbe6bddd116a8d9e5d50d78148cf1'),
    ('tokenizer.json', 16035853, 'c62446df87ae18ec98b133f8f84fc449a07cc89bbf8ef192a4cb5f9c53777a7a'),
    ('model.safetensors', 1149461028, '9efe0f88c99f2aa794452e9559dc60e98d60d9fa2bf1b60cf2710411b6da5b4e'),
]


def fetch(name: str, size: int, sha256: str, model_dir: Path) -> None:
    target = model_dir / name
    if target.is_file() and target.stat().st_size == size:
        digest = hashlib.sha256()
        with target.open('rb') as existing:
            for block in iter(lambda: existing.read(1 << 20), b''):
                digest.update(block)
        if digest.hexdigest() == sha256:
            return
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, partial = tempfile.mkstemp(dir=target.parent, prefix=target.name + '.', suffix='.part')
    url = f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}'
    digest = hashlib.sha256()
    received = 0
    try:
        with urllib.request.urlopen(url, timeout=60) as response, os.fdopen(fd, 'wb') as output:
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                received += len(block)
                if received > size:
                    raise SystemExit(f'{name}: larger than the expected {size} bytes')
                digest.update(block)
                output.write(block)
        if received != size or digest.hexdigest() != sha256:
            raise SystemExit(f'{name}: size or SHA-256 does not match revision {REVISION}')
        os.replace(partial, target)
    finally:
        Path(partial).unlink(missing_ok=True)


model_dir = Path(sys.argv[1]) / 'model'
for name, size, sha256 in FILES:
    fetch(name, size, sha256, model_dir)
PY
MODULE="$HERE/bin/jarvis_router_local.py"
[[ -f "$MODULE" ]] || MODULE="$HOME/.local/bin/jarvis_router_local.py"
CUDA_VISIBLE_DEVICES='' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  "$PREFIX/venv/bin/python" "$MODULE" --check --root "$PREFIX"
"$PREFIX/venv/bin/python" - "$PREFIX" <<'PY'
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
(root / 'installed.json').write_text(json.dumps({
    'model': 'fastino/GLiNER2.5-multi-Decide',
    'revision': 'a35a0cd3b7a0f00f2effc576f454cd48fa98aa5f', 'device': 'cpu'}))
PY
du -sh "$PREFIX"
echo 'Installed and checked on CPU. Enable explicitly: jarvis config set routing_mode local'
