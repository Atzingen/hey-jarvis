#!/usr/bin/env python3
"""Instalação opcional e processo local do NeMo-Speech.cpp; nenhum download no boot."""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import socket
import subprocess
import tarfile
import time
import urllib.error
import urllib.request

ROOT = Path.home() / '.local/share/jarvis/nemotron'
VERSION = '0.1.0'
ARCHIVES = {
    'linux-x86_64-cpu': '0f74131d631ad2c694cf0ec53490866bb6461147959589a69fb6fc231944065b',
    'linux-x86_64-cuda': 'e68628f396489c98fb353e070efaea5bc4977409ae7734fce56c251a79e29147',
    'macos-aarch64-cpu': '971661d38d4bf97a63c528d13041a964316d25068d8df045e5b4839848092f25',
    'macos-aarch64-metal': 'f1dff4f9dd9c96214f8cb78b982812459132df8a4ad1a42409fd94de4a366244',
    'macos-x86_64-cpu': '042a4612e07460fab6a39b5d862aa1e39d0ac3eaedfdb979f3f5fc12de510c20',
}
MODEL_REPO = 'nvidia/nemotron-3.5-asr-streaming-0.6b'
MODEL_REVISION = '1c8deaecc64b91f034d73e08dd8b64625eb3395d'
MODEL_FILE = 'nemotron-3.5-asr-streaming-0.6b.q8_0.gguf'
MODEL_SHA = 'a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae'


def file_hash(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def download_verified(url: str, destination: Path, sha256: str, max_bytes: int) -> None:
    if destination.is_file() and file_hash(destination) == sha256:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    try:
        size = 0
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError('download excedeu o tamanho máximo')
                output.write(chunk)
        if file_hash(temporary) != sha256:
            raise ValueError('SHA-256 incorreto; arquivo não instalado')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def default_device() -> str:
    if platform.system() == 'Darwin' and platform.machine() in ('arm64', 'aarch64'):
        return 'metal'
    return 'cuda' if shutil.which('nvidia-smi') else 'cpu'


def install(device: str) -> None:
    device = default_device() if device == 'auto' else device
    system = {'Linux': 'linux', 'Darwin': 'macos'}.get(platform.system(), '')
    machine = {'arm64': 'aarch64', 'AMD64': 'x86_64'}.get(platform.machine(), platform.machine())
    target = f'{system}-{machine}-{device}'
    if target not in ARCHIVES:
        raise RuntimeError(f'Sem pacote verificado para {target}; use o Whisper ou a API.')
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = f'nemo-speech-{VERSION}-{target}'
    archive = ROOT / f'{name}.tar.gz'
    print(f'Instalando runtime {VERSION} ({target}) e Nemotron PT-BR Q8 (~707 MiB).', flush=True)
    download_verified(f'https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v{VERSION}/{name}.tar.gz',
                      archive, ARCHIVES[target], 256 * 1024 * 1024)
    with tarfile.open(archive) as package:
        package.extractall(ROOT / 'runtime', filter='data')
    executable = ROOT / 'runtime' / name / 'bin/nemo-speech'
    subprocess.run([str(executable), '--version'], check=True, timeout=10)
    download_verified(f'https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{MODEL_FILE}',
                      ROOT / MODEL_FILE, MODEL_SHA, 742 * 1024 * 1024)
    manifest = {'version': VERSION, 'device': device, 'binary': str(executable.relative_to(ROOT)),
                'model': MODEL_REPO, 'revision': MODEL_REVISION, 'sha256': MODEL_SHA}
    (ROOT / 'installed.json').write_text(json.dumps(manifest, indent=2) + '\n')
    archive.unlink()
    print('Instalado. Ative em Reconhecimento de fala: nemotron; ou jarvis config set stt_provider nemotron.')


class NemotronServer:
    """Um servidor privado por processo Jarvis, encerrado junto com seu dono."""

    def __init__(self, device: str = 'auto'):
        self.device = device
        self.process: subprocess.Popen | None = None
        self.token = secrets.token_urlsafe(32)
        self.url = ''
        self.log = None

    def start(self) -> None:
        try:
            manifest = json.loads((ROOT / 'installed.json').read_text())
            binary = ROOT / manifest['binary']
        except (OSError, ValueError, KeyError) as error:
            raise RuntimeError('instale com jarvis stt install-nemotron') from error
        model = ROOT / MODEL_FILE
        if not binary.is_file() or not model.is_file():
            raise RuntimeError('instalação incompleta; execute jarvis stt install-nemotron')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        log_path = ROOT / 'server.log'
        self.log = os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w')
        self.process = subprocess.Popen([
            str(binary), 'serve', '--asr-model', str(model), '--device', self.device,
            '--host', '127.0.0.1', '--port', str(port), '--api-key', self.token, '--no-ui',
            '--asr.endpointing.enable=false', '--asr.batching.enabled=false',
        ], stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
            env={**os.environ, 'OMP_NUM_THREADS': '8'})
        atexit.register(self.close)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(f'NeMo-Speech.cpp encerrou; veja {log_path}')
                try:
                    with opener.open(f'http://127.0.0.1:{port}/health', timeout=0.5) as response:
                        if response.status == 200:
                            self.url = f'ws://127.0.0.1:{port}/v1/realtime'
                            for line in log_path.read_text().splitlines():
                                if '[asr]' in line and 'backend=' in line:
                                    self.device = line.split('backend=', 1)[1].split()[0].lower()
                            return
                except (urllib.error.URLError, TimeoutError):
                    pass
                time.sleep(0.1)
            raise TimeoutError('Nemotron não ficou pronto em 30 segundos')
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if self.log is not None:
            self.log.close()
            self.log = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install-nemotron', 'status'])
    parser.add_argument('--device', choices=['auto', 'cpu', 'cuda', 'metal'], default='auto')
    args = parser.parse_args()
    try:
        if args.action == 'install-nemotron':
            install(args.device)
        else:
            print((ROOT / 'installed.json').read_text() if (ROOT / 'installed.json').exists()
                  else 'Nemotron não instalado. Use: jarvis stt install-nemotron')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
