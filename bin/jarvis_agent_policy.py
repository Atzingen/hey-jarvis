"""Shared prompts and subscription environment for native agent sessions."""
from __future__ import annotations

import json
import os
from pathlib import Path

import jarvis_config
import jarvis_i18n
from jarvis_routing import RouteRequest

CODEX_DISABLED_FEATURES = ('shell_tool', 'unified_exec', 'view_image', 'multi_agent', 'plugins',
                           'memories', 'skill_search', 'apps', 'image_generation', 'computer_use', 'browser_use')


def subscription_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'CLAUDECODE'):
        env.pop(key, None)
    return env


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
