#!/usr/bin/env python3
"""Bounded HTTP in a cancellable child. Credentials travel over a private pipe."""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jarvis_routing import RouterUnavailable, RouterCancelled, ExecutionUncertain

MAX_BYTES = 2 * 1024 * 1024


def post_json(url: str, key: str, body: dict, timeout: float, cancel: threading.Event) -> dict:
    if cancel.is_set():
        raise RouterCancelled()
    wire = json.dumps({'url': url, 'key': key, 'body': body, 'timeout': timeout}).encode()
    if len(wire) > MAX_BYTES:
        raise RouterUnavailable('request_too_large')
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    output = bytearray()
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    try:
        process.stdin.write(wire)
        process.stdin.close()
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            if cancel.is_set():
                raise RouterCancelled()
            if time.monotonic() >= deadline:
                raise ExecutionUncertain('http_timeout')
            if not selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > MAX_BYTES:
                raise ExecutionUncertain('response_too_large')
        try:
            result = json.loads(output)
        except (ValueError, UnicodeError):
            raise ExecutionUncertain('invalid_http_response') from None
        if result.get('error'):
            status = result.get('status')
            if status in (400, 401, 403, 404, 413, 422, 429):
                raise RouterUnavailable(f'http_{status}')
            raise ExecutionUncertain('http_failure')
        data = result.get('data')
        if not isinstance(data, dict):
            raise ExecutionUncertain('invalid_http_response')
        return data
    except (BrokenPipeError, OSError):
        raise ExecutionUncertain('http_connection_lost') from None
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        if not process.stdin.closed:
            process.stdin.close()


def worker() -> None:
    try:
        data = json.loads(sys.stdin.buffer.read(MAX_BYTES + 1))
        request = Request(data['url'], data=json.dumps(data['body']).encode(), headers={
            'Authorization': 'Bearer ' + data['key'], 'Content-Type': 'application/json'}, method='POST')
        with urlopen(request, timeout=data['timeout']) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('too large')
            result = {'data': json.loads(raw)}
    except HTTPError as exc:
        result = {'error': True, 'status': exc.code}
    except Exception:
        result = {'error': True}
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    worker()
