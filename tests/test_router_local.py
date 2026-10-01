from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
from jarvis_routing import RouteRequest, RouterUnavailable, RouterCancelled
from jarvis_router_local import LocalRouter, worker_environment

WORKER = '''import json,sys,time
print(json.dumps({'ready': True, 'cuda_initialized': False}), flush=True)
for line in sys.stdin:
 data=json.loads(line)
 mode=data['text']
 if mode=='slow':time.sleep(3)
 if mode=='eof':break
 result={'id':data['id'], 'route':'api', 'complexity':'low', 'continuation':'new'}
 if mode=='bad-id':result['id']='wrong'
 if mode=='big':result['noise']='x'*70000
 print(json.dumps(result),flush=True)
'''


class LocalRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        worker = self.root / 'worker.py'
        worker.write_text(WORKER)
        self.cancel = threading.Event()
        self.router = LocalRouter(self.root, .4, 1, self.cancel, [sys.executable, str(worker)])
        self.request = RouteRequest('r1', 'c1', 'question', (), self.root)

    def tearDown(self) -> None:
        self.router.close()
        self.folder.cleanup()

    def warm(self) -> None:
        with self.assertRaisesRegex(RouterUnavailable, 'warming'):
            self.router(self.request)
        for _ in range(50):
            if self.router.ready():
                return
            time.sleep(.01)
        self.fail('worker never became ready')

    def test_missing_worker_is_safe(self) -> None:
        missing = LocalRouter(self.root, .4, 1, self.cancel)
        with self.assertRaisesRegex(RouterUnavailable, 'not_installed'):
            missing(self.request)

    def test_cold_then_warm_and_environment_is_cpu_only(self) -> None:
        self.warm()
        decision = self.router(self.request)
        self.assertEqual(decision.kind, 'api')
        env = worker_environment()
        self.assertEqual(env['CUDA_VISIBLE_DEVICES'], '')
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        self.assertEqual(env['JARVIS_ROUTER_DEVICE'], 'cpu')

    def test_bad_id_eof_and_oversized_result_stop_worker(self) -> None:
        for text in ('bad-id', 'eof', 'big'):
            self.warm()
            request = RouteRequest('r1', 'c1', text, (), self.root)
            with self.assertRaises(RouterUnavailable):
                self.router(request)
            self.assertIsNone(self.router.process)

    def test_timeout_and_cancellation_kill_classifier(self) -> None:
        self.warm()
        request = RouteRequest('r1', 'c1', 'slow', (), self.root)
        with self.assertRaises(RouterUnavailable):
            self.router(request)
        self.assertIsNone(self.router.process)
        self.warm()
        timer = threading.Timer(.05, self.cancel.set)
        timer.start()
        with self.assertRaises(RouterCancelled):
            self.router(request)
        timer.join()
        self.assertIsNone(self.router.process)

    def test_large_input_is_rejected_before_pipe_write(self) -> None:
        self.warm()
        with self.assertRaises(RouterUnavailable):
            self.router(RouteRequest('r', 'c', 'x'*100000, (), self.root))
