from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import jarvis_config as config
from tests.test_actions import vl


class RoutingConfigTest(unittest.TestCase):
    def test_upgrade_keeps_subscription_and_saved_models(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text('quick_provider="codex"\ncodex_model="existing"\ncodex_effort="medium"\n'
                            'openai_api_key="fake-stt-key"\ndeep_model="opus"\n')
            cfg = config.load(path)
            self.assertEqual(cfg.get("routing_mode"), "agent")
            self.assertEqual(cfg.get("api_provider"), "none")
            self.assertEqual((cfg["codex_model"], cfg["codex_effort"]), ("existing", "medium"))
            config.save(cfg, path)
            self.assertEqual(config.load(path)["deep_model"], "opus")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_modes_and_secrets_survive_profile_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.object(config, "PROFILES_DIR", Path(folder)):
            cfg = config.defaults()
            cfg.update(routing_mode="jev", jev_api_key="test-only-secret", api_provider="none")
            config.save_profile("routing", cfg)
            saved = config.load_profile("routing")
            self.assertEqual(saved.get("routing_mode"), "jev")
            self.assertEqual(saved.get("jev_api_key"), "test-only-secret")
            self.assertTrue(config.BY_KEY["jev_api_key"].secret)

    def test_special_phrase_is_preserved_as_ordinary_text(self) -> None:
        for text in ("Pense bem antes de responder: não abra o projeto.", "think hard about it"):
            self.assertEqual(vl.parse_command(text), ("ask", (text, False)))

    def test_immediate_commands_stay_immediate(self) -> None:
        for text, kind in (("fim", "end"), ("pare", "hush"), ("", "noop")):
            self.assertEqual(vl.parse_command(text), (kind, None))

    def test_legacy_settings_are_preserved_but_hidden(self) -> None:
        for key in ("deep_model", "deep_effort", "handoff_seconds_deep", "narration_interval_deep"):
            self.assertIn(key, config.defaults())
            self.assertEqual(config.BY_KEY[key].section, "legacy")
