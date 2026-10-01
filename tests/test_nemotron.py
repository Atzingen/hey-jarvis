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
