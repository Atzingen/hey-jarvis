#!/usr/bin/env python3
"""Inspect optional routing or explicitly install the isolated CPU classifier."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

import jarvis_config


def status() -> dict:
    cfg = jarvis_config.load()
    local = Path.home() / '.local/share/jarvis/router-local/installed.json'
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    current = {}
    try:
        state = json.loads((runtime / 'jarvis-state.json').read_text())
        current = {key: state.get(key) for key in ('effective_route', 'fallback_reason', 'session_backend', 'session_id')}
    except (OSError, ValueError):
        pass
    return {'routing_mode': cfg['routing_mode'], 'agent_provider': cfg['quick_provider'],
            'agent_session_mode': cfg['agent_session_mode'],
            'api_enabled': cfg['api_provider'] != 'none' and bool(cfg['api_model']),
            'api_model': cfg['api_model'],
            'api_key_configured': bool(cfg.get('openai_api_key') or os.environ.get('OPENAI_API_KEY')),
            'jev_key_configured': bool(cfg.get('jev_api_key') or os.environ.get('JEV_API_KEY')),
            'local_installed': local.is_file(), 'local_device': 'cpu', 'current': current}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'install-local'], default='status', nargs='?')
    args = parser.parse_args()
    if args.action == 'status':
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return
    repository = Path(__file__).resolve().parent.parent
    candidates = [repository / 'scripts/install-router-local.sh',
                  Path.home() / '.local/share/jarvis/router-tools/scripts/install-router-local.sh']
    installer = next((path for path in candidates if path.is_file()), None)
    if installer is None:
        raise SystemExit('Optional installer missing; update Jarvis with install.sh --update.')
    raise SystemExit(subprocess.run(['bash', str(installer)]).returncode)


if __name__ == '__main__':
    main()
