from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class RoutingDistributionTest(unittest.TestCase):
    def test_stage_contains_runtime_but_no_classifier_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            result = subprocess.run(['bash', str(ROOT/'install.sh'), '--stage', str(stage)],
                                    capture_output=True, text=True, timeout=15,
                                    env={**os.environ, 'HOME': str(stage/'isolated-home')})
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ('jarvis_routing.py','jarvis_router_api.py','jarvis_router_jev.py','jarvis_router_local.py',
                         'jarvis_sessions.py','jarvis_agent_codex.py','jarvis_agent_claude.py',
                         'jarvis_agent_policy.py','jarvis_conversation_routing.py','jarvis_http.py','jarvis-router.py',
                         'jarvis_nemotron.py','jarvis_dictation_stream.py'):
                file = stage/'bin'/name
                self.assertTrue(file.is_file(), name)
                self.assertTrue(file.stat().st_mode & 0o100, name)
            share = stage/'share/jarvis'
            self.assertTrue((share/'router-tools/scripts/install-router-local.sh').is_file())
            self.assertTrue((share/'router-tools/requirements-router-local.lock').is_file())
            self.assertFalse((share/'router-local').exists())
            self.assertFalse((share/'nemotron').exists())
            self.assertFalse((share/'venv').exists())
            self.assertFalse((stage/'isolated-home').exists())

    def test_unknown_option_has_no_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)/'home'
            result = subprocess.run(['bash', str(ROOT/'install.sh'), '--not-a-real-option'],
                                    capture_output=True, text=True, timeout=5,
                                    env={**os.environ, 'HOME': str(home)})
            self.assertEqual(result.returncode, 2)
            self.assertFalse(home.exists())

    def test_fixture_has_sixty_public_cases_and_six_groups(self) -> None:
        cases=json.loads((ROOT/'tests/fixtures/routing_pt_br.json').read_text())
        self.assertEqual(len(cases),60)
        self.assertEqual(len({case['id'] for case in cases}),60)
        groups={case['group'] for case in cases}
        self.assertEqual(len(groups),6)
        for group in groups:
            self.assertEqual(sum(case['group']==group for case in cases),10)
