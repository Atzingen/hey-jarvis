"""Narração de progresso — sem áudio, sem rede.

    ~/miniconda3/envs/voice/bin/python -m unittest tests.test_narrate -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

import jarvis_narrate as narr  # noqa: E402


class FakeClock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


class Templates(unittest.TestCase):
    def test_tool_kinds_pt(self):
        self.assertEqual(narr.template_for("tool", "Read: /etc/hosts", "pt-BR"), "lendo um arquivo")
        self.assertEqual(narr.template_for("tool", "sed -n 1,20p x.py", "pt-BR"), "lendo um arquivo")
        self.assertEqual(narr.template_for("tool", "Grep: foo", "pt-BR"), "procurando no código")
        self.assertEqual(narr.template_for("tool", "rg -n foo src", "pt-BR"), "procurando no código")
        self.assertEqual(narr.template_for("tool", "WebFetch: https://x", "pt-BR"), "consultando a internet")
        self.assertEqual(narr.template_for("tool", "docker ps", "pt-BR"), "rodando um comando")
        self.assertEqual(narr.template_for("thinking", "qualquer coisa", "pt-BR"), "pensando")
        self.assertEqual(narr.template_for(None, "", "pt-BR"), "ainda pensando nisso")

    def test_tool_kinds_en(self):
        self.assertEqual(narr.template_for("tool", "cat x", "en"), "reading a file")
        self.assertEqual(narr.template_for("tool", "find . -name x", "en"), "searching the code")
        self.assertEqual(narr.template_for("tool", "curl https://x", "en"), "checking the internet")
        self.assertEqual(narr.template_for("tool", "ls", "en"), "running a command")
        self.assertEqual(narr.template_for("thinking", "x", "en"), "thinking")
        self.assertEqual(narr.template_for(None, "", "en"), "still thinking about it")

    def test_text_event_first_sentence(self):
        self.assertEqual(narr.template_for("text", "Vou listar os containers. Depois conto.", "pt-BR"),
                         "Vou listar os containers.")
        long = "a" * 200
        self.assertLessEqual(len(narr.template_for("text", long, "pt-BR")), 140)


class Guards(unittest.TestCase):
    def test_accepts_clean_sentence(self):
        self.assertEqual(narr.clean_output("  Estou listando os containers.  "), "Estou listando os containers.")

    def test_strips_quotes_and_markdown(self):
        self.assertEqual(narr.clean_output('"**Lendo o arquivo**"'), "Lendo o arquivo")
        self.assertEqual(narr.clean_output("`ls` agora"), "ls agora")

    def test_rejects_bad_output(self):
        self.assertIsNone(narr.clean_output(""))
        self.assertIsNone(narr.clean_output("   "))
        self.assertIsNone(narr.clean_output("linha 1\nlinha 2"))
        self.assertIsNone(narr.clean_output("<<FIM>>"))
        self.assertIsNone(narr.clean_output("x" * 141))


class NarratorChain(unittest.TestCase):
    def _narrator(self, generate, mode="local"):
        clock = FakeClock()
        n = narr.Narrator(mode, "pt-BR", 8.0, "quantos containers?", generate=generate, clock=clock)
        n.spoke()
        return n, clock

    def test_generator_phrase_wins(self):
        n, clock = self._narrator(lambda q, ev: "Estou contando os containers.")
        n.feed("tool", "docker ps")
        clock.advance(8.5)
        self.assertIsNone(n.poll())            # geração disparada em thread
        phrase = n.wait(2.0)
        self.assertEqual(phrase, "Estou contando os containers.")

    def test_generator_failure_falls_to_model_text(self):
        n, clock = self._narrator(lambda q, ev: None)
        n.feed("tool", "docker ps")
        n.feed("text", "Vou contar os containers agora.")
        clock.advance(8.5)
        n.poll()
        self.assertEqual(n.wait(2.0), "Vou contar os containers agora.")

    def test_generator_failure_falls_to_template(self):
        n, clock = self._narrator(lambda q, ev: None)
        n.feed("tool", "docker ps")
        clock.advance(8.5)
        n.poll()
        self.assertEqual(n.wait(2.0), "rodando um comando")

    def test_generator_bad_output_falls_back(self):
        n, clock = self._narrator(lambda q, ev: "linha\noutra <<FIM>>")
        n.feed("tool", "Read: x.py")
        clock.advance(8.5)
        n.poll()
        self.assertEqual(n.wait(2.0), "lendo um arquivo")

    def test_templates_mode_is_synchronous(self):
        n, clock = self._narrator(None, mode="templates")
        n.feed("tool", "Grep: foo")
        clock.advance(8.5)
        self.assertEqual(n.poll(), "procurando no código")

    def test_generator_gets_question_and_new_events_only(self):
        seen = []

        def gen(q, ev):
            seen.append((q, list(ev)))
            return "ok frase"

        n, clock = self._narrator(gen)
        n.feed("tool", "a")
        n.feed("thinking", "b")
        clock.advance(8.5)
        n.poll()
        n.wait(2.0)
        n.spoke()
        n.feed("tool", "c")
        clock.advance(8.5)
        n.poll()
        n.wait(2.0)
        self.assertEqual(seen[0][0], "quantos containers?")
        self.assertEqual(seen[0][1], [("tool", "a"), ("thinking", "b")])
        self.assertEqual(seen[1][1], [("tool", "c")])


class NarratorTiming(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.n = narr.Narrator("templates", "pt-BR", 8.0, "q", generate=None, clock=self.clock)
        self.n.spoke()

    def test_silent_before_interval(self):
        self.n.feed("tool", "ls")
        self.clock.advance(5)
        self.assertIsNone(self.n.poll())

    def test_speaks_after_interval_with_new_event(self):
        self.n.feed("tool", "ls")
        self.clock.advance(8)
        self.assertEqual(self.n.poll(), "rodando um comando")

    def test_no_new_event_no_phrase_until_dry_period(self):
        self.clock.advance(9)
        self.assertIsNone(self.n.poll())
        self.clock.advance(8)             # 17 s sem fala = > 2 × intervalo
        self.assertEqual(self.n.poll(), "ainda pensando nisso")
        self.n.spoke()
        self.clock.advance(40)
        self.assertIsNone(self.n.poll())  # só uma vez por período seco

    def test_dry_flag_resets_after_new_event(self):
        self.clock.advance(17)
        self.assertEqual(self.n.poll(), "ainda pensando nisso")
        self.n.spoke()
        self.n.feed("tool", "ls")
        self.clock.advance(9)
        self.assertEqual(self.n.poll(), "rodando um comando")
        self.n.spoke()
        self.clock.advance(17)
        self.assertEqual(self.n.poll(), "ainda pensando nisso")

    def test_does_not_repeat_previous_phrase(self):
        self.n.feed("tool", "ls")
        self.clock.advance(9)
        self.assertEqual(self.n.poll(), "rodando um comando")
        self.n.spoke()
        self.n.feed("tool", "docker ps")
        self.clock.advance(9)
        self.assertIsNone(self.n.poll())  # mesmo template de novo: pula

    def test_off_mode_never_speaks(self):
        n = narr.Narrator("off", "pt-BR", 8.0, "q", generate=None, clock=self.clock)
        n.feed("tool", "ls")
        self.clock.advance(30)
        self.assertIsNone(n.poll())


class ResolveMode(unittest.TestCase):
    def _resolve(self, want, models, cuda, key):
        cfg = {"narration": want, "narration_local_model": "gemma3:4b", "openai_api_key": key}
        return narr.resolve_mode(cfg, ollama_models=lambda: models, cuda=lambda: cuda)

    def test_explicit_modes_pass_through(self):
        for m in ("local", "openai", "self", "templates", "off"):
            self.assertEqual(self._resolve(m, None, False, "")[0], m)

    def test_auto_prefers_local_with_gpu_and_model(self):
        self.assertEqual(self._resolve("auto", ["gemma3:4b"], True, "k")[0], "local")

    def test_auto_without_gpu_uses_openai_when_key(self):
        self.assertEqual(self._resolve("auto", ["gemma3:4b"], False, "k")[0], "openai")

    def test_auto_model_missing_uses_openai_when_key(self):
        self.assertEqual(self._resolve("auto", ["qwen3:8b"], True, "k")[0], "openai")

    def test_auto_ollama_down_no_key_uses_templates(self):
        mode, why = self._resolve("auto", None, True, "")
        self.assertEqual(mode, "templates")
        self.assertTrue(why)


class OpenAIParsing(unittest.TestCase):
    def test_reads_message_after_reasoning_item(self):
        payload = {"output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "Estou lendo o arquivo."}]},
        ]}
        self.assertEqual(narr.openai_text(payload), "Estou lendo o arquivo.")

    def test_no_message_returns_none(self):
        self.assertIsNone(narr.openai_text({"output": [{"type": "reasoning"}]}))
        self.assertIsNone(narr.openai_text({}))


if __name__ == "__main__":
    unittest.main()
