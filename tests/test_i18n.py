"""Tratamento configurável ({address}) nas falas fixas e no prompt de sistema.

    ~/miniconda3/envs/voice/bin/python -m unittest tests.test_i18n -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

import jarvis_i18n  # noqa: E402
from jarvis_i18n import T  # noqa: E402


class AddressTest(unittest.TestCase):
    def tearDown(self) -> None:
        jarvis_i18n.set_address("")

    def test_language_defaults(self):
        self.assertEqual(T("pt-BR", "not_heard"), "Não ouvi, senhor")
        self.assertEqual(T("en", "not_heard"), "I didn't catch that, sir")

    def test_override_applies_to_every_phrase_and_language(self):
        jarvis_i18n.set_address("chefe")
        self.assertEqual(T("pt-BR", "greeting"), "No que vamos trabalhar, chefe?")
        self.assertEqual(T("en", "done"), "Done, chefe.")
        self.assertEqual(T("pt-BR", "consent_needed"), "Preciso da sua autorização, chefe. Veja a tela.")

    def test_blank_override_falls_back_to_default(self):
        jarvis_i18n.set_address("   ")
        self.assertEqual(jarvis_i18n.address("en"), "sir")

    def test_tuples_are_filled_too(self):
        jarvis_i18n.set_address("Ana")
        name, hint = T("pt-BR", "ph_listening")
        self.assertEqual(name, "OUVINDO")
        self.assertIn("pode falar, Ana", hint)

    def test_format_arguments_still_work(self):
        self.assertEqual(T("pt-BR", "opening_project", name="x"), "abrindo o projeto x")

    def test_no_placeholder_leaks(self):
        for lang, table in jarvis_i18n.STRINGS.items():
            for key in table:
                value = T(lang, key)
                for part in (value if isinstance(value, tuple) else (value,)):
                    self.assertNotIn("{address}", part, f"{lang}/{key}")

    def test_system_prompt_names_the_address_and_forbids_the_name(self):
        jarvis_i18n.set_address("boss")
        prompt = jarvis_i18n.system_prompt("en")
        self.assertIn('Address the user as "boss"', prompt)
        self.assertIn("do not use their name", prompt)
        self.assertNotIn("{address}", prompt)
        self.assertIn('como "boss"', jarvis_i18n.system_prompt("pt-BR"))

    def test_custom_system_prompt_gets_the_address_too(self):
        jarvis_i18n.set_address("boss")
        self.assertEqual(jarvis_i18n.system_prompt("en", "Call me {address}."), "Call me boss.")


if __name__ == "__main__":
    unittest.main()
