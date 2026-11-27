from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
from jarvis_http import post_json
from jarvis_routing import RouterUnavailable, RouterCancelled, ExecutionUncertain


class Handler(BaseHTTPRequestHandler):
    forwarded: list[str] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        Handler.forwarded.append(self.headers.get('Authorization', ''))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({'ok': True}).encode())

    def do_POST(self) -> None:
        if self.path == '/redirect':
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(302)
            self.send_header('Location', '/elsewhere')
            self.end_headers()
            return
        if self.path == '/slow':
            time.sleep(0.8)
        status = int(self.path[1:]) if self.path[1:].isdigit() else 200
        self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(status)
        self.end_headers()
        try:
            self.wfile.write(json.dumps({'ok': True}).encode())
        except BrokenPipeError:
            pass


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_response_and_definitive_rejections(self) -> None:
        self.assertTrue(post_json(self.base + '/ok', 'fake', {}, 3, threading.Event())['ok'])
        for code in (400, 401, 429):
            with self.assertRaisesRegex(RouterUnavailable, str(code)):
                post_json(self.base + '/' + str(code), 'fake', {}, 3, threading.Event())
        with self.assertRaises(ExecutionUncertain):
            post_json(self.base + '/500', 'fake', {}, 3, threading.Event())

    def test_redirect_never_forwards_the_key(self) -> None:
        Handler.forwarded.clear()
        with self.assertRaisesRegex(RouterUnavailable, '302'):
            post_json(self.base + '/redirect', 'secret-key', {}, 3, threading.Event())
        self.assertEqual(Handler.forwarded, [])

    def test_cancel_interrupts_inflight_http(self) -> None:
        cancel = threading.Event()
        timer = threading.Timer(0.2, cancel.set)
        timer.start()
        started = time.monotonic()
        with self.assertRaises(RouterCancelled):
            post_json(self.base + '/slow', 'fake', {}, 3, cancel)
        timer.join()
        self.assertLess(time.monotonic() - started, 0.7)

    def test_timeout_after_submission_is_uncertain(self) -> None:
        with self.assertRaises(ExecutionUncertain):
            post_json(self.base + '/slow', 'fake', {}, 0.2, threading.Event())
