#!/usr/bin/env python3
"""Optional CPU classifier process; no model imports or downloads on the direct path."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
import time

from jarvis_routing import RouteRequest, RouteDecision, RouterCall, RouterUnavailable, RouterCancelled
from jarvis_router_jev import QUESTIONS, decision_from_categories

LOCAL_DIR = Path.home() / '.local/share/jarvis/router-local'
MODEL_ID = 'fastino/GLiNER2.5-multi-Decide'
MODEL_REVISION = 'a35a0cd3b7a0f00f2effc576f454cd48fa98aa5f'
MAX_LINE = 64 * 1024
_cached: LocalRouter | None = None


def worker_environment() -> dict[str, str]:
    return os.environ | {'CUDA_VISIBLE_DEVICES': '', 'JARVIS_ROUTER_DEVICE': 'cpu',
                         'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                         'TOKENIZERS_PARALLELISM': 'false', 'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2'}


class LocalRouter:
    def __init__(self, root: Path, timeout: float, idle: float, cancel: threading.Event,
                 worker_argv: list[str] | None = None) -> None:
        self.root = root
        self.timeout = timeout
        self.idle = idle
        self.cancel = cancel
        self.worker_argv = worker_argv
        self.process: subprocess.Popen | None = None
        self.buffer = bytearray()
        self.is_ready = False
        self.lock = threading.Lock()

    def close(self) -> None:
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait()
            self.process.stdin.close()
            self.process.stdout.close()
        self.process = None
        self.is_ready = False
        self.buffer.clear()

    def read_line(self, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while True:
            if self.cancel.is_set():
                self.close()
                raise RouterCancelled()
            if b'\n' in self.buffer:
                line, _, remaining = self.buffer.partition(b'\n')
                self.buffer = bytearray(remaining)
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        raise ValueError()
                    return data
                except (ValueError, UnicodeError):
                    raise RouterUnavailable('local_invalid_response') from None
            remaining_time = max(0, deadline - time.monotonic())
            ready, _, _ = select.select([self.process.stdout], [], [], min(.05, remaining_time))
            if ready:
                data = os.read(self.process.stdout.fileno(), 8192)
                if not data:
                    raise RouterUnavailable('local_worker_stopped')
                self.buffer.extend(data)
                if len(self.buffer) > MAX_LINE:
                    raise RouterUnavailable('local_response_too_large')
            elif time.monotonic() >= deadline:
                return None

    def ready(self) -> bool:
        if self.process is None:
            return False
        if self.is_ready:
            return self.process.poll() is None
        try:
            data = self.read_line(0)
            if data is None:
                return False
            if data.get('ready') is not True or data.get('cuda_initialized') is not False:
                raise RouterUnavailable('local_cpu_check_failed')
            self.is_ready = True
            return True
        except (RouterUnavailable, RouterCancelled):
            self.close()
            raise

    def __call__(self, request: RouteRequest) -> RouteDecision:
        with self.lock:
            if self.cancel.is_set():
                self.close()
                raise RouterCancelled()
            if self.process is not None and self.process.poll() is not None:
                self.close()
            if self.process is None:
                argv = self.worker_argv
                if argv is None:
                    python = self.root / 'venv/bin/python'
                    if not python.is_file() or not (self.root / 'installed.json').is_file():
                        raise RouterUnavailable('local_not_installed')
                    argv = [str(python), str(Path(__file__).resolve()), '--worker', '--root', str(self.root),
                            '--idle', str(self.idle)]
                self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL, env=worker_environment())
                raise RouterUnavailable('local_warming')
            if not self.ready():
                raise RouterUnavailable('local_warming')
            data = {'id': request.request_id, 'text': request.text, 'context': request.context,
                    'has_active_session': bool(request.active_session_id)}
            wire = (json.dumps(data) + '\n').encode()
            if len(wire) > MAX_LINE:
                raise RouterUnavailable('local_request_too_large')
            try:
                self.process.stdin.write(wire)
                self.process.stdin.flush()
                result = self.read_line(self.timeout)
                if result is None:
                    raise RouterUnavailable('local_timeout')
                if result.get('id') != request.request_id:
                    raise RouterUnavailable('local_request_id_mismatch')
                values = {name: result.get(name) for name in QUESTIONS}
                if any(not isinstance(value, str) for value in values.values()):
                    raise RouterUnavailable('local_invalid_response')
                return decision_from_categories(values)
            except (OSError, ValueError):
                self.close()
                raise RouterUnavailable('local_worker_stopped') from None
            except (RouterUnavailable, RouterCancelled):
                self.close()
                raise


def build_local_router(cfg: dict, cancel: threading.Event) -> RouterCall:
    global _cached
    root = LOCAL_DIR
    timeout = float(cfg.get('router_timeout_seconds', 5))
    idle = float(cfg.get('local_router_idle_seconds', 300))
    if _cached is None or (_cached.root, _cached.timeout, _cached.idle) != (root, timeout, idle):
        if _cached is not None:
            _cached.close()
        _cached = LocalRouter(root, timeout, idle, cancel)
    _cached.cancel = cancel
    return _cached


def worker(root: Path, idle: float, check: bool = False) -> None:
    os.environ.update(worker_environment())
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from gliner2 import AutoExtractor
        torch.set_num_threads(2)
        model = AutoExtractor.from_pretrained(str(root / 'model'), local_files_only=True)
        model.to('cpu')
        model.eval()
    if torch.cuda.is_initialized():
        raise RuntimeError('CUDA unexpectedly initialized')
    print(json.dumps({'ready': True, 'cuda_initialized': False}), flush=True)
    if check:
        return
    schema = {
        'route': {'labels': {
            'resposta_em_texto': 'Explicar, calcular, traduzir, resumir ou escrever usando apenas o texto fornecido e conhecimento geral.',
            'pesquisa_na_internet': 'Pesquisar informação pública atual na internet: clima, notícias, cotação, horários ou documentação atualizada.',
            'acao_no_computador': 'Abrir aplicativos ou projetos, ler ou alterar arquivos locais, executar comandos, ver o estado da máquina, ou esclarecer uma tarefa no computador.'}, 'multi_label': False},
        'complexity': {'labels': {'low': 'Resposta direta ou tarefa simples.',
                                  'medium': 'Explicação ou tarefa com alguns passos.',
                                  'high': 'Análise profunda, arquitetura, comparação complexa ou planejamento detalhado.'}, 'multi_label': False}}
    route_names = {'resposta_em_texto': 'api', 'pesquisa_na_internet': 'web', 'acao_no_computador': 'agent'}
    while select.select([sys.stdin], [], [], idle)[0]:
        line = sys.stdin.buffer.readline(MAX_LINE + 1)
        if not line or len(line) > MAX_LINE:
            return
        try:
            data = json.loads(line)
            text = data['text']
            if data['context']:
                context = '\n'.join('Usuário: ' + q + '\nAssistente: ' + a for q, a in data['context'])
                text = 'Contexto anterior:\n' + context + '\n\nPedido atual: ' + text
            current_schema = dict(schema)
            if data['has_active_session']:
                current_schema['continuation'] = {'labels': QUESTIONS['continuation']['criteria'], 'multi_label': False}
            with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
                result = model.classify_text(text, current_schema)
            result['route'] = route_names[result['route']]
            result.setdefault('continuation', 'new')
            decision_from_categories(result)
            print(json.dumps({'id': data['id']} | result), flush=True)
        except Exception:
            print(json.dumps({'id': data.get('id', ''), 'error': 'classification_failed'}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--root', type=Path, default=LOCAL_DIR)
    parser.add_argument('--idle', type=float, default=300)
    args = parser.parse_args()
    if args.worker or args.check:
        worker(args.root, args.idle, args.check)
    else:
        print(json.dumps({'installed': (args.root / 'installed.json').is_file(),
                          'path': str(args.root), 'model': MODEL_ID, 'device': 'cpu'}))


if __name__ == '__main__':
    main()
