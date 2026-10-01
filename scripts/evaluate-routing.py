#!/usr/bin/env python3
"""Evaluate classification only on the same public PT-BR fixture. Never run agent actions."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
import jarvis_config
from jarvis_conversation_routing import build_backend
from jarvis_routing import RouteRequest, RouterUnavailable, choose_route, resolve_profile


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered)-1, int((len(ordered)-1)*fraction))] if ordered else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['agent', 'assistant', 'jev', 'local'], required=True)
    parser.add_argument('--fixture', type=Path, default=Path(__file__).resolve().parent.parent/'tests/fixtures/routing_pt_br.json')
    parser.add_argument('--local-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cfg = jarvis_config.load()
    cfg['routing_mode'] = args.mode
    if args.mode == 'local' and args.local_dir:
        import jarvis_router_local
        jarvis_router_local.LOCAL_DIR = args.local_dir.resolve()
    if args.mode != 'assistant':
        # A synthetic profile makes route comparison independent of response credentials.
        # This program never executes a selected API or agent profile.
        cfg.update(api_provider='openai', api_model='evaluation-only-no-execution')
    cancel = threading.Event()
    backend = build_backend(args.mode, cfg, cancel, lambda *_: None) if args.mode != 'agent' else None
    cases = json.loads(args.fixture.read_text())
    report = {'mode':args.mode, 'cases':[], 'executors_called':0, 'fixture':str(args.fixture),
              'classifier': 'GLiNER2.5-multi-Decide/CPU' if args.mode=='local' else args.mode}
    import os
    missing = (args.mode == 'jev' and not (cfg.get('jev_api_key') or os.environ.get('JEV_API_KEY')))
    missing = missing or (args.mode == 'assistant' and (
        cfg.get('api_provider') != 'openai' or not cfg.get('api_model')
        or not (cfg.get('openai_api_key') or os.environ.get('OPENAI_API_KEY'))))
    if missing:
        report['status'] = 'not_executed_credentials_or_opt_in_missing'
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(report['status'])
        return
    if args.mode == 'local':
        import jarvis_router_local
        report['model_revision'] = jarvis_router_local.MODEL_REVISION
        warm = RouteRequest('warm','evaluation','Olá',(),Path('/tmp'))
        start = time.monotonic()
        try:
            backend(warm)
        except RouterUnavailable as exc:
            report['cold_result'] = str(exc)
        report['cold_fallback_ms'] = round((time.monotonic()-start)*1000,2)
        if not backend.process:
            report['status']='not_executed_local_not_installed'
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            return
        while time.monotonic()-start<120 and not backend.ready():
            time.sleep(.1)
        report['cold_load_seconds']=round(time.monotonic()-start,2)
    latencies=[]
    for case in cases:
        request=RouteRequest(case['id'],'evaluation',case['text'],tuple(tuple(x) for x in case['context']),
                             Path('/tmp'), 'known-evaluation-session' if case['active_session'] else None)
        start=time.monotonic()
        try:
            decision=choose_route(request,cfg,{args.mode:backend} if backend else {})
            elapsed=(time.monotonic()-start)*1000
            effective=resolve_profile(decision,cfg).transport
            entry={'id':case['id'],'route':effective,'expected':case['expected_route'],
                   'correct':effective==case['expected_route'],'unsafe_api':case['needs_computer'] and effective=='api',
                   'reason':decision.reason,'complexity':decision.complexity,'latency_ms':round(elapsed,2)}
            latencies.append(elapsed)
        except Exception as exc:
            entry={'id':case['id'],'error':type(exc).__name__, 'correct':False, 'unsafe_api':False}
        report['cases'].append(entry)
    report.update(status='completed' if args.mode != 'agent' else 'bypass_policy_only',
                  confidence_evaluated=False,
                  correct=sum(row['correct'] for row in report['cases']),total=len(cases),
                  unsafe_api=sum(row['unsafe_api'] for row in report['cases']),
                  p50_ms=round(percentile(latencies,.5),2),p95_ms=round(percentile(latencies,.95),2))
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False))
    if args.mode=='local':
        backend.close()


if __name__=='__main__':
    main()
