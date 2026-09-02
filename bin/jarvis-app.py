#!/usr/bin/python3
"""Tela principal do Jarvis no terminal — a mesma interface do painel da bar
do Omarchy, como aplicativo standalone (funciona em qualquer Linux).

Mostra o estado do serviço, os chips de configuração, o guia de voz, o ditado
(speech-to-text) e os atalhos, com botões de ação: ligar/desligar, pausar,
ditar, logs e configuração. Aberta por `jarvis app` (terminal flutuante) ou
pela entrada de desktop "Jarvis" no launcher de aplicativos.

Só stdlib (curses); textos seguem a chave `language` do config, como o resto.
"""
from __future__ import annotations

import curses
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis_config as jc  # noqa: E402

JARVIS = str(Path(__file__).resolve().parent / "jarvis")
DICTATING_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "jarvis-dictating"
STATUS_POLL_SECONDS = 2.0

STRINGS = {
    "pt-BR": {
        "status_on": "Ativo — escutando “hey jarvis”",
        "status_paused": "Pausado",
        "status_off": "Desligado — microfone livre",
        "status_dictating": "Gravando ditado…",
        "status_manual": "Ativo — só atalhos (“hey jarvis” desligado, mic fechado)",
        "not_installed": "Serviço não instalado — rode install.sh",
        "chips": [("IDIOMA", "language"), ("STT", "stt_provider"),
                  ("RÁPIDO", "quick_provider"), ("PENSE BEM", "deep_model"), ("ACESSO", "system_access")],
        "voice_title": "CONVERSA POR VOZ",
        "voice_meta": "“hey jarvis”",
        "intro": "Diga “hey jarvis” e fale depois da saudação — ele escuta até você parar "
                 "e abre a janela da conversa. Não há palavras-chave: o modelo decide.",
        "voice_rows": [
            ("“abre o projeto X”", "layout dev: terminal 2×2 + VS Code + Chrome"),
            ("“abre o btop / o Chrome”", "abre um app instalado"),
            ("“pense bem <pergunta>”", "modelo mais forte (Claude Fable)"),
            ("“quantos containers no Docker?”", "roda o comando e responde o resultado"),
            ("falar por cima da resposta", "ele para e escuta você (barge-in)"),
            ("“fecha a conversa” / “é só isso”", "encerra (o modelo entende)"),
            ("“pode dormir”", "suspende o computador"),
        ],
        "dict_title": "DITADO",
        "dict_ready": "pronto", "dict_recording": "gravando", "dict_off": "Jarvis desligado",
        "dict_intro": "Speech-to-text: fale e o texto é transcrito e colado na janela ativa.",
        "dict_rows": [
            ("Ctrl+Shift+K", "aperta, fala, aperta de novo"),
            ("Ctrl+Shift+L", "segura e fala, solta pra colar"),
            ("outra tecla", "cancela e descarta"),
        ],
        "keys_title": "ATALHOS",
        "keys_rows": [
            ("Ctrl+Shift+H", "falar agora, sem “hey jarvis”"),
            ("Ctrl+Shift+J", "liga/desliga o Jarvis"),
            ("Ctrl+Shift+K", "ditado (toggle)"),
            ("Ctrl+Shift+L", "ditado (push-to-talk)"),
            ("jarvis config", "configuração no terminal"),
        ],
        "btn_on": "Ligar", "btn_off": "Desligar", "btn_pause": "Pausar 30 min",
        "btn_wake_on": "Ligar “hey jarvis”", "btn_wake_off": "Desligar “hey jarvis”",
        "btn_dictate": "Ditar agora", "btn_dictate_stop": "Parar e colar",
        "btn_logs": "Logs", "btn_config": "Config", "btn_quit": "Sair",
        "hint": "←/→ ou Tab navega · Enter executa · atalhos: l liga/desliga  w wake  p pausa  d dita  g logs  c config · q sai",
        "logs_hint": "Ctrl+C volta pra tela do Jarvis",
    },
    "en": {
        "status_on": "Active — listening for “hey jarvis”",
        "status_paused": "Paused",
        "status_off": "Off — microphone free",
        "status_dictating": "Recording dictation…",
        "status_manual": "Active — hotkeys only (“hey jarvis” off, mic closed)",
        "not_installed": "Voice service not installed — run install.sh",
        "chips": [("LANGUAGE", "language"), ("STT", "stt_provider"),
                  ("QUICK", "quick_provider"), ("THINK HARD", "deep_model"), ("ACCESS", "system_access")],
        "voice_title": "VOICE CONVERSATION",
        "voice_meta": "“hey jarvis”",
        "intro": "Say “hey jarvis” and talk after the greeting — it listens until you stop "
                 "and opens the conversation window. No keywords: the model decides.",
        "voice_rows": [
            ("“open project X”", "dev layout: terminal 2×2 + VS Code + Chrome"),
            ("“open btop / Chrome”", "launches an installed app"),
            ("“think hard <question>”", "stronger model (Claude Fable)"),
            ("“how many Docker containers?”", "runs the command, answers with the result"),
            ("talk over the answer", "it stops and listens (barge-in)"),
            ("“close it” / “that's all, thanks”", "ends it (the model understands)"),
            ("“go to sleep”", "suspends the computer"),
        ],
        "dict_title": "DICTATION",
        "dict_ready": "ready", "dict_recording": "recording", "dict_off": "Jarvis off",
        "dict_intro": "Speech-to-text: talk and the text is transcribed and pasted into the active window.",
        "dict_rows": [
            ("Ctrl+Shift+K", "press, talk, press again"),
            ("Ctrl+Shift+L", "hold to talk, release to paste"),
            ("other key", "cancels and discards"),
        ],
        "keys_title": "KEYBINDINGS",
        "keys_rows": [
            ("Ctrl+Shift+H", "talk now, no “hey jarvis” needed"),
            ("Ctrl+Shift+J", "toggles Jarvis on/off"),
            ("Ctrl+Shift+K", "dictation (toggle)"),
            ("Ctrl+Shift+L", "dictation (push-to-talk)"),
            ("jarvis config", "settings in the terminal"),
        ],
        "btn_on": "Turn on", "btn_off": "Turn off", "btn_pause": "Pause 30 min",
        "btn_wake_on": "Wake word on", "btn_wake_off": "Wake word off",
        "btn_dictate": "Dictate now", "btn_dictate_stop": "Stop and paste",
        "btn_logs": "Logs", "btn_config": "Settings", "btn_quit": "Quit",
        "hint": "←/→ or Tab moves · Enter runs · hotkeys: l toggle  w wake  p pause  d dictate  g logs  c settings · q quits",
        "logs_hint": "Ctrl+C returns to the Jarvis screen",
    },
}


def jarvis_status() -> tuple[str, str, bool]:
    """(state on|paused|off, detalhe curto, instalado) a partir do CLI."""
    try:
        out = subprocess.run([JARVIS, "status"], capture_output=True, text=True, timeout=10).stdout
        data = json.loads(out.strip().splitlines()[0])
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, IndexError):
        return "off", "", False
    state = data.get("alt", "off")
    tooltip = str(data.get("tooltip", ""))
    lines = tooltip.split("\\n") if "\\n" in tooltip else tooltip.split("\n")
    detail = " · ".join(lines[1:]) if len(lines) > 1 else ""
    # timestamps do systemd ("Sun 2026-08-30 15:55:40 -03") -> só a hora
    import re
    detail = re.sub(r"\w{3} \d{4}-\d{2}-\d{2} (\d{2}:\d{2}):\d{2}( [-+]\d{2,4}| \w+)?", r"\1", detail)
    return state, detail, True


class Card:
    """Um card com moldura, título colorido e linhas de conteúdo."""

    def __init__(self, title: str, meta: str, tint: int, lines: list[tuple[int, str, str]]):
        self.title = title
        self.meta = meta
        self.tint = tint
        self.lines = lines          # (atributo da 1ª coluna, col1, col2)

    def height(self) -> int:
        return len(self.lines) + 2


class App:
    C_ACCENT, C_DICT, C_KEYS, C_PAUSED, C_URGENT, C_DIM = 1, 2, 3, 4, 5, 6

    def __init__(self, stdscr):
        self.scr = stdscr
        self.cfg = jc.load()
        self.lang = self.cfg.get("language") if self.cfg.get("language") in STRINGS else "en"
        self.t = STRINGS[self.lang]
        self.state, self.detail, self.installed = "off", "", True
        self.dictating = False
        self.cursor = 0
        self.status_msg = ""
        self.last_poll = 0.0
        self.button_regions: list[tuple[int, int, int, int]] = []  # y, x0, x1, index
        curses.curs_set(0)
        curses.use_default_colors()
        curses.init_pair(self.C_ACCENT, curses.COLOR_CYAN, -1)
        curses.init_pair(self.C_DICT, curses.COLOR_GREEN, -1)
        curses.init_pair(self.C_KEYS, curses.COLOR_MAGENTA, -1)
        curses.init_pair(self.C_PAUSED, curses.COLOR_YELLOW, -1)
        curses.init_pair(self.C_URGENT, curses.COLOR_RED, -1)
        curses.init_pair(self.C_DIM, curses.COLOR_WHITE, -1)
        curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED)
        self.scr.timeout(500)
        self.poll(force=True)

    # --- estado ----------------------------------------------------------

    def poll(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_poll < STATUS_POLL_SECONDS:
            return
        self.last_poll = now
        self.state, self.detail, self.installed = jarvis_status()
        self.dictating = DICTATING_FILE.exists()

    def run_cmd(self, *args: str) -> None:
        try:
            subprocess.run([JARVIS, *args], capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            self.status_msg = str(exc)
        self.poll(force=True)

    def run_fullscreen(self, argv: list[str], hint: str = "") -> None:
        """Sai do curses, roda um comando na mesma janela e volta."""
        curses.endwin()
        if hint:
            print(hint)
        try:
            subprocess.run(argv)
        except KeyboardInterrupt:
            pass
        self.scr.clear()
        curses.doupdate()
        self.poll(force=True)

    # --- conteúdo --------------------------------------------------------

    def state_line(self) -> tuple[str, int]:
        t = self.t
        if not self.installed:
            return t["not_installed"], self.C_URGENT
        if self.dictating:
            return t["status_dictating"], self.C_URGENT
        if self.state == "manual":
            return t["status_manual"], self.C_DICT
        if self.state == "on":
            return t["status_on"], self.C_ACCENT
        if self.state == "paused":
            return t["status_paused"], self.C_PAUSED
        return t["status_off"], self.C_DIM

    def buttons(self) -> list[tuple[str, str]]:
        t = self.t
        on = self.state in ("on", "manual")
        items = [("toggle", t["btn_off"] if on else t["btn_on"])]
        if on:
            items.append(("wake", t["btn_wake_off"] if self.state == "on" else t["btn_wake_on"]))
            items.append(("pause", t["btn_pause"]))
        items.append(("dictate", t["btn_dictate_stop"] if self.dictating else t["btn_dictate"]))
        items += [("logs", t["btn_logs"]), ("config", t["btn_config"]), ("quit", t["btn_quit"])]
        return items

    def activate(self, action: str) -> bool:
        """Executa a ação de um botão; retorna False pra sair do app."""
        if action == "quit":
            return False
        if action == "toggle":
            self.run_cmd("toggle")
        elif action == "wake":
            self.run_cmd("wake", "toggle")
        elif action == "pause":
            self.run_cmd("pause", "30m")
        elif action == "dictate":
            if self.state == "on":
                self.run_cmd("dictate", "toggle")
        elif action == "logs":
            self.run_fullscreen(
                ["journalctl", "--user", "-u", "voice-launcher.service", "-n", "200", "-f"],
                hint=self.t["logs_hint"])
        elif action == "config":
            self.run_fullscreen([sys.executable, str(Path(JARVIS).parent / "jarvis-config.py")])
            self.cfg = jc.load()
            self.lang = self.cfg.get("language") if self.cfg.get("language") in STRINGS else "en"
            self.t = STRINGS[self.lang]
        return True

    # --- desenho ---------------------------------------------------------

    def put(self, y: int, x: int, text: str, attr: int = 0) -> int:
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and x < w - 1:
            self.scr.addnstr(y, max(x, 0), text, w - 1 - x, attr)
        return x + len(text)

    def draw_card(self, y: int, x: int, width: int, card: Card) -> int:
        tint = curses.color_pair(card.tint)
        inner = width - 4
        top = "╭─ "
        self.put(y, x, top, tint)
        self.put(y, x + len(top), card.title + " ", tint | curses.A_BOLD)
        fill_from = x + len(top) + len(card.title) + 1
        end = x + width - 1
        meta = f" {card.meta} ─" if card.meta else ""
        meta_at = end - len(meta) - 1
        for cx in range(fill_from, end):
            self.put(y, cx, "─", tint)
        if meta and meta_at > fill_from:
            self.put(y, meta_at, meta, tint)
        self.put(y, end, "╮", tint)
        for i, (attr1, col1, col2) in enumerate(card.lines):
            row = y + 1 + i
            self.put(row, x, "│", tint)
            self.put(row, end, "│", tint)
            if col2 is None:
                self.put(row, x + 2, col1[:inner], attr1)
            else:
                c1w = max(len(c1) for a, c1, c2 in card.lines if c2 is not None) + 2
                self.put(row, x + 2, col1[:inner], attr1)
                self.put(row, x + 2 + c1w, col2[: inner - c1w], curses.color_pair(self.C_DIM))
        bottom = y + 1 + len(card.lines)
        self.put(bottom, x, "╰" + "─" * (width - 2) + "╯", tint)
        return bottom + 1

    def build_voice_card(self, width: int) -> Card:
        t = self.t
        lines: list[tuple[int, str, str]] = []
        for chunk in textwrap.wrap(t["intro"], width - 4):
            lines.append((curses.color_pair(self.C_DIM), chunk, None))
        lines.append((0, "", None))
        for phrase, action in t["voice_rows"]:
            lines.append((curses.color_pair(self.C_ACCENT), phrase, action))
        return Card(t["voice_title"], t["voice_meta"], self.C_ACCENT, lines)

    def build_dict_card(self, width: int) -> Card:
        t = self.t
        meta = (t["dict_recording"] if self.dictating
                else t["dict_ready"] if self.state in ("on", "manual") else t["dict_off"])
        lines: list[tuple[int, str, str]] = []
        for chunk in textwrap.wrap(t["dict_intro"], width - 4):
            lines.append((curses.color_pair(self.C_DIM), chunk, None))
        lines.append((0, "", None))
        for key, action in t["dict_rows"]:
            lines.append((curses.color_pair(self.C_DICT) | curses.A_BOLD, f"[{key}]", action))
        return Card(t["dict_title"], meta, self.C_DICT, lines)

    def build_keys_card(self, width: int) -> Card:
        t = self.t
        lines = [(curses.color_pair(self.C_KEYS) | curses.A_BOLD, f"[{key}]", action)
                 for key, action in t["keys_rows"]]
        return Card(t["keys_title"], "", self.C_KEYS, lines)

    def draw(self) -> None:
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        width = min(w - 2, 104)
        x0 = 1

        # hero
        self.put(0, x0, "Jarvis", curses.A_BOLD)
        line, color = self.state_line()
        dot = "●" if (self.state in ("on", "paused") or self.dictating) else "○"
        y = 1
        pos = self.put(y, x0, dot + " ", curses.color_pair(color))
        pos = self.put(y, pos, line, curses.color_pair(color))
        if self.detail and not self.dictating:
            self.put(y, pos, f"  ·  {self.detail}", curses.color_pair(self.C_DIM))

        # chips
        y = 3
        pos = x0
        chip_tints = [0, self.C_DICT, self.C_ACCENT, self.C_KEYS, 0]
        for (label, key), tint in zip(self.t["chips"], chip_tints):
            value = str(self.cfg.get(key, ""))
            pos = self.put(y, pos, f" {label} ", curses.color_pair(self.C_DIM))
            pos = self.put(y, pos, value, (curses.color_pair(tint) if tint else 0) | curses.A_BOLD)
            pos += 2

        # cards — em telas baixas entra o modo compacto: só hero, ditado e botões,
        # pra barra de botões nunca ficar fora da tela (ex.: terminal 80×24).
        y = 5
        two_cols = w >= 92
        voice_card = self.build_voice_card(width)
        dict_card = self.build_dict_card(width // 2 - 1 if two_cols else width)
        keys_card = self.build_keys_card(width - width // 2 - 1 if two_cols else width)
        needed = y + voice_card.height() + 4
        needed += max(dict_card.height(), keys_card.height()) if two_cols \
            else dict_card.height() + keys_card.height()
        compact = h < needed
        if compact:
            dict_card = self.build_dict_card(width)
            y = self.draw_card(y, x0, width, dict_card)
        elif two_cols:
            y = self.draw_card(y, x0, width, voice_card)
            rows = max(dict_card.height(), keys_card.height())
            dict_card.lines += [(0, "", None)] * (rows - dict_card.height())
            keys_card.lines += [(0, "", None)] * (rows - keys_card.height())
            self.draw_card(y, x0, width // 2 - 1, dict_card)
            y = self.draw_card(y, x0 + width // 2 + 1, width - width // 2 - 1, keys_card)
        else:
            y = self.draw_card(y, x0, width, voice_card)
            y = self.draw_card(y, x0, width, dict_card)
            y = self.draw_card(y, x0, width, keys_card)

        # botões
        y += 1
        self.button_regions = []
        btns = self.buttons()
        self.cursor = min(self.cursor, len(btns) - 1)
        pos = x0
        for i, (action, label) in enumerate(btns):
            sel = i == self.cursor
            disabled = action == "dictate" and self.state not in ("on", "manual")
            attr = curses.A_REVERSE | curses.A_BOLD if sel else 0
            if disabled:
                attr |= curses.A_DIM
            tint = {"dictate": self.C_DICT, "pause": self.C_PAUSED, "config": self.C_KEYS}.get(action, self.C_ACCENT)
            if action == "dictate" and self.dictating:
                tint = self.C_URGENT
            text = f"[ {label} ]"
            x1 = pos + len(text)
            if x1 < w - 1:
                self.put(y, pos, text, attr | (curses.color_pair(tint) if not sel else 0))
                self.button_regions.append((y, pos, x1, i))
            pos = x1 + 1

        # rodapé
        self.put(y + 2, x0, self.t["hint"][: width], curses.color_pair(self.C_DIM))
        if self.status_msg:
            self.put(y + 3, x0, self.status_msg[: width], curses.color_pair(self.C_URGENT))
        scr.refresh()

    # --- loop ------------------------------------------------------------

    def handle_mouse(self) -> bool:
        try:
            _, mx, my, _, _ = curses.getmouse()
        except curses.error:
            return True
        for y, x0, x1, idx in self.button_regions:
            if my == y and x0 <= mx < x1:
                self.cursor = idx
                return self.activate(self.buttons()[idx][0])
        return True

    def loop(self) -> None:
        hotkeys = {"l": "toggle", "w": "wake", "p": "pause", "d": "dictate", "g": "logs", "c": "config"}
        while True:
            self.poll()
            self.draw()
            try:
                ch = self.scr.getch()
            except KeyboardInterrupt:
                return
            if ch == -1:
                continue
            if ch in (ord("q"), 27):
                return
            if ch == curses.KEY_MOUSE:
                if not self.handle_mouse():
                    return
            elif ch in (curses.KEY_LEFT, curses.KEY_BTAB):
                self.cursor = (self.cursor - 1) % len(self.buttons())
            elif ch in (curses.KEY_RIGHT, ord("\t")):
                self.cursor = (self.cursor + 1) % len(self.buttons())
            elif ch in (curses.KEY_ENTER, 10, 13):
                if not self.activate(self.buttons()[self.cursor][0]):
                    return
            elif 0 < ch < 256 and chr(ch) in hotkeys:
                if not self.activate(hotkeys[chr(ch)]):
                    return


def main() -> int:
    curses.wrapper(lambda scr: App(scr).loop())
    return 0


if __name__ == "__main__":
    sys.exit(main())
