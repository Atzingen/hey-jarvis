#!/bin/bash
# Hook theme-set: faz o estado "ativo" dos ícones da barra (omarchy-shell) usar a
# cor de destaque (accent) do tema, em vez do fallback `urgent` (vermelho).
# Escreve/atualiza só a chave [bar] active em ~/.config/omarchy/shell.toml,
# preservando o resto do arquivo (ex.: [font] base-size do `omarchy display text size`).
set -euo pipefail

COLORS="$HOME/.local/state/omarchy/current/theme/colors.toml"
SHELL_TOML="$HOME/.config/omarchy/shell.toml"

accent=$(sed -nE 's/^accent\s*=\s*"(#[0-9A-Fa-f]{6,8})".*/\1/p' "$COLORS" | head -1)
[ -n "$accent" ] || exit 0

python3 - "$SHELL_TOML" "$accent" <<'PY'
import re, sys
from pathlib import Path
path, accent = Path(sys.argv[1]), sys.argv[2]
text = path.read_text() if path.exists() else ""
lines = text.splitlines()
out, section, done = [], "", False
for line in lines:
    m = re.match(r"^\s*\[([A-Za-z0-9_-]+)\]", line)
    if m:
        if section == "bar" and not done:
            out.append(f'active = "{accent}"'); done = True
        section = m.group(1)
    elif section == "bar" and re.match(r"^\s*active\s*=", line):
        line = f'active = "{accent}"'; done = True
    out.append(line)
if not done:
    if section == "bar":
        out.append(f'active = "{accent}"')
    else:
        if out and out[-1].strip():
            out.append("")
        out += ["[bar]", f'active = "{accent}"']
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("\n".join(out).rstrip("\n") + "\n")
PY
