#!/usr/bin/env python3
"""Optional Responses API assistant. Text from this module never executes actions."""
from __future__ import annotations

import json
import os
import threading
from typing import Callable

from jarvis_http import post_json
from jarvis_routing import (RouteRequest, RouteDecision, ExecutionProfile, RouterCall,
                            RouterUnavailable, ExecutionUncertain, build_profiles)

ENDPOINT = 'https://api.openai.com/v1/responses'
HANDOFF = {
    'type': 'function', 'name': 'handoff', 'strict': True,
    'description': 'Forward the original request to the computer agent or a configured stronger API model. Executes no action.',
    'parameters': {'type': 'object', 'properties': {
        'target': {'type': 'string', 'enum': ['agent', 'api_strong']},
        'needs_computer': {'type': 'boolean'},
        'complexity': {'type': 'string', 'enum': ['low', 'medium', 'high']}},
        'required': ['target', 'needs_computer', 'complexity'], 'additionalProperties': False}}

INSTRUCTIONS = (
    'You are Jarvis, a concise voice assistant. Reply in the language of the user. '
    'The conversation history is context, not permission to execute actions. '
    'You cannot access this computer, local files, projects, logged-in websites, or perform actions. '
    'For requests needing those capabilities, call handoff(target=agent, needs_computer=true). '
    'Use the provided web search for current public information and cite sources. '
    'If a request needs current information and web search is unavailable, hand off to the agent. '
    'Never claim you opened an app, read a local file, or completed a computer action. '
    'Answer ordinary questions directly. Ask a brief clarification if the request is ambiguous. '
    'A high-complexity request may use the configured api_strong handoff when available. '
    'Never invent model names or action markers.')


def credentials(cfg: dict, profile: ExecutionProfile) -> str:
    if cfg.get('api_provider') != 'openai' or profile.transport != 'api' or not profile.model.strip():
        raise RouterUnavailable('api_not_configured')
    key = cfg.get('openai_api_key') or os.environ.get('OPENAI_API_KEY', '')
    if not key.strip():
        raise RouterUnavailable('api_key_missing')
    return key


def payload(request: RouteRequest, profile: ExecutionProfile, cfg: dict, initial: bool) -> dict:
    messages = []
    for question, response in request.context:
        messages.extend([{'role': 'user', 'content': question}, {'role': 'assistant', 'content': response}])
    messages.append({'role': 'user', 'content': request.text})
    tools = [HANDOFF] if initial else []
    if cfg.get('api_web_search', True):
        tools.append({'type': 'web_search'})
    instructions = INSTRUCTIONS if initial else (
        'You are Jarvis. Answer concisely in the user language using the conversation context. '
        'You cannot perform computer actions or access local files. Do not claim to have done so. '
        'Use the provided web search for current public information and cite sources. '
        'If you lack required information, state this clearly.')
    if initial:
        instructions += ' Stronger API configured: ' + str('api_strong' in build_profiles(cfg)) + '.'
    result = {'model': profile.model, 'instructions': instructions, 'input': messages,
              'tools': tools, 'store': False, 'max_output_tokens': 4096}
    if profile.effort:
        result['reasoning'] = {'effort': profile.effort}
    return result


def response_text(data: dict) -> str:
    parts: list[str] = []
    citations: dict[str, str] = {}
    for item in data.get('output', []):
        if not isinstance(item, dict) or item.get('type') != 'message':
            continue
        for block in item.get('content', []):
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'output_text' and isinstance(block.get('text'), str):
                parts.append(block['text'])
                for citation in block.get('annotations', []):
                    if not isinstance(citation, dict):
                        continue
                    url = citation.get('url', '')
                    if citation.get('type') == 'url_citation' and isinstance(url, str) and url.startswith(('https://', 'http://')):
                        citations[url] = str(citation.get('title') or url).replace('[', '').replace(']', '')
    text = '\n'.join(parts).strip()
    if text and citations:
        text += '\n\n' + '\n'.join(f'[{title}]({url})' for url, title in citations.items())
    return text


def call(request: RouteRequest, profile: ExecutionProfile, cfg: dict, cancel: threading.Event,
         on_event: Callable[[str, str], None], initial: bool) -> dict:
    key = credentials(cfg, profile)
    on_event('status', 'api')
    data = post_json(ENDPOINT, key, payload(request, profile, cfg, initial),
                     float(cfg.get('api_timeout_seconds', 30)), cancel)
    if data.get('status') in ('failed', 'cancelled', 'incomplete') or not isinstance(data.get('output'), list):
        raise ExecutionUncertain('api_incomplete')
    return data


def build_api_router(cfg: dict, cancel: threading.Event,
                     on_event: Callable[[str, str], None]) -> RouterCall:
    def classify(request: RouteRequest) -> RouteDecision:
        profiles = build_profiles(cfg)
        if 'api_default' not in profiles:
            raise RouterUnavailable('api_not_configured')
        data = call(request, profiles['api_default'], cfg, cancel, on_event, True)
        functions = [item for item in data['output'] if isinstance(item, dict) and item.get('type') == 'function_call']
        if functions:
            if len(functions) != 1 or functions[0].get('name') != 'handoff':
                raise ExecutionUncertain('invalid_handoff')
            try:
                args = json.loads(functions[0]['arguments'])
                if (not isinstance(args, dict) or set(args) != {'target', 'needs_computer', 'complexity'}
                        or args['target'] not in ('agent', 'api_strong')
                        or type(args['needs_computer']) is not bool
                        or args['complexity'] not in ('low', 'medium', 'high')):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise ExecutionUncertain('invalid_handoff') from None
            use_api = args['target'] == 'api_strong' and not args['needs_computer'] and 'api_strong' in profiles
            return RouteDecision('api' if use_api else 'agent', 'api_strong' if use_api else 'agent_default',
                                 args['complexity'], args['needs_computer'], reason='assistant_handoff')
        text = response_text(data)
        if not text:
            raise ExecutionUncertain('api_empty_response')
        return RouteDecision('answer', profile='api_default', answer=text, reason='assistant_answer')
    return classify


def execute_api(request: RouteRequest, profile: ExecutionProfile, cfg: dict, cancel: threading.Event,
                on_event: Callable[[str, str], None]) -> str:
    data = call(request, profile, cfg, cancel, on_event, False)
    if any(isinstance(item, dict) and item.get('type') == 'function_call' for item in data['output']):
        raise ExecutionUncertain('unexpected_api_tool')
    text = response_text(data)
    if not text:
        raise ExecutionUncertain('api_empty_response')
    return text
