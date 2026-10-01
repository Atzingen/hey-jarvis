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
import json
from pathlib import Path
import sys
from huggingface_hub import snapshot_download
root = Path(sys.argv[1])
snapshot_download('fastino/GLiNER2.5-multi-Decide',
                  revision='a35a0cd3b7a0f00f2effc576f454cd48fa98aa5f', local_dir=root / 'model',
                  allow_patterns=['*.json', '*.safetensors', 'encoder_config/*'])
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
