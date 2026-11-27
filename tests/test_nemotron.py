from __future__ import annotations

import hashlib
import importlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))


class NemotronInstallTest(unittest.TestCase):
    def test_model_update_preserves_installed_runtime(self) -> None:
        module = importlib.import_module('jarvis_nemotron')
        model_bytes = b'new verified model'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binary = root / f'runtime/nemo-speech-{module.VERSION}-linux-x86_64-cpu/bin/nemo-speech'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            original_inode = binary.stat().st_ino

            def download(url: str, **kwargs) -> io.BytesIO:
                if not url.endswith('.gguf'):
                    raise AssertionError('must not reinstall the running runtime')
                return io.BytesIO(model_bytes)

            with (patch.object(module, 'ROOT', root),
                  patch.object(module, 'MODEL_SHA', hashlib.sha256(model_bytes).hexdigest()),
                  patch('platform.system', return_value='Linux'),
                  patch('platform.machine', return_value='x86_64'),
                  patch('urllib.request.urlopen', side_effect=download)):
                module.install('cpu')
            self.assertEqual(binary.stat().st_ino, original_inode)
            self.assertEqual(binary.read_text(), '#!/bin/sh\nexit 0\n')
            self.assertEqual((root / module.MODEL_FILE).read_bytes(), model_bytes)

    def test_invalid_download_does_not_replace_existing_file(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec('jarvis_nemotron'))
        module = importlib.import_module('jarvis_nemotron')
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'model.gguf'
            destination.write_bytes(b'previous model')
            with patch('urllib.request.urlopen', return_value=io.BytesIO(b'corrupted')):
                with self.assertRaises(ValueError):
                    module.download_verified('https://example.test/model', destination,
                                             hashlib.sha256(b'valid model').hexdigest(), 100)
            self.assertEqual(destination.read_bytes(), b'previous model')
            self.assertFalse(destination.with_suffix('.tmp').exists())

    def test_missing_install_returns_to_whisper_without_api(self) -> None:
        import jarvis_stt
        self.assertIsNotNone(importlib.util.find_spec('jarvis_nemotron'))
        module = importlib.import_module('jarvis_nemotron')
        with tempfile.TemporaryDirectory() as folder:
            with (patch.object(module, 'ROOT', Path(folder)),
                  patch.object(jarvis_stt, 'LocalWhisper', return_value=type('Whisper', (), {'label': 'whisper small/cpu'})())):
                backend = jarvis_stt.build_transcriber({'stt_provider': 'nemotron', 'openai_api_key': 'key'})
        self.assertEqual(backend.label, 'whisper small/cpu')
