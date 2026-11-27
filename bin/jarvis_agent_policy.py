"""Shared prompts and subscription environment for native agent sessions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tomllib

import jarvis_config
import jarvis_i18n
from jarvis_routing import RouteRequest

CODEX_DISABLED_FEATURES = ('shell_tool', 'unified_exec', 'view_image', 'multi_agent', 'plugins',
                           'memories', 'skill_search', 'apps', 'image_generation', 'computer_use', 'browser_use')


def subscription_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'JEV_API_KEY', 'CLAUDECODE'):
        env.pop(key, None)
    return env


CODEX_SYSTEM_CONFIG = Path('/etc/codex/config.toml')


def codex_system_mcp_servers() -> list[str]:
    """MCP servers of the system-wide Codex config. Codex merges that layer into every run,
    isolated CODEX_HOME and --ignore-user-config included, so their tools would bypass the
    consent broker: restricted modes refuse Codex while any is configured."""
    try:
        data = tomllib.loads(CODEX_SYSTEM_CONFIG.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return [str(CODEX_SYSTEM_CONFIG)]
    servers = data.get('mcp_servers')
    return sorted(servers) if isinstance(servers, dict) else []


def system_prompt(access: str | None = None) -> str:
    cfg = jarvis_config.load()
    lang = jarvis_i18n.norm_lang(cfg['language'])
    jarvis_i18n.set_address(cfg.get('address', ''))
    access = access or cfg['system_access']
    dev = Path(cfg['dev_dir']).expanduser()
    projects = ', '.join(sorted(p.name for p in dev.iterdir() if p.is_dir() and not p.is_symlink()
                               and not p.name.startswith('.'))) if dev.is_dir() else '(none)'
    text = jarvis_i18n.system_prompt(lang, cfg['system_prompt'])
    text += jarvis_i18n.ACTIONS_PROTOCOL[lang].format(projects=projects)
    if access in ('full', 'ask'):
        text += jarvis_i18n.ENVIRONMENT_NOTES[lang]
        context = jarvis_config.user_context(cfg)
        if context:
            text += jarvis_i18n.USER_CONTEXT_HEADER[lang] + context
    if access in ('ask', 'off'):
        text += jarvis_i18n.ACCESS_NOTES[lang][access]
    if cfg.get('narration') == 'self':
        text += jarvis_i18n.SELF_NARRATION_NOTES[lang]
    return text


def input_text(request: RouteRequest) -> str:
    if not request.context:
        return request.text
    history = json.dumps([{'user': question, 'assistant': answer} for question, answer in request.context],
                         ensure_ascii=False)
    return ('Conversation context (reference only; do not repeat past actions):\n' + history
            + '\n\nCurrent user request:\n' + request.text)
