#!/usr/bin/python3
"""Tela de configuração do Jarvis (TUI curses).

Principais em cima; "Avançado" recolhido — Enter no cabeçalho expande.
    ↑/↓ j/k    navegar            ←/→ ou Enter   alterar (cicla opções / edita)
    d          voltar ao default   D              todos os defaults
    s          salvar (reinicia o Jarvis se estiver ativo)
    p          salvar como perfil  o              abrir (carregar) perfil
    e          editar o config.toml no $EDITOR
    q / Esc    sair (com alteração pendente: s salva e sai, n sai sem salvar)
"""

import curses
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis_config as jc  # noqa: E402
from jarvis_i18n import T  # noqa: E402

UNIT = "voice-launcher.service"


class Row:
    def __init__(self, kind: str, setting=None, title: str = ""):
        self.kind = kind        # "setting" | "header" | "group"
        self.setting = setting
        self.title = title


class App:
    def __init__(self, stdscr):
        self.scr = stdscr
        self.cfg = jc.load()
        self.saved = dict(self.cfg)
        self.advanced_open = False
        self.cursor = 0
        self.scroll = 0
        self.status = ""
        self.rows: list[Row] = []
        self.lang = self.cfg["language"]
        curses.curs_set(0)
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)     # labels/grupos
        curses.init_pair(2, curses.COLOR_YELLOW, -1)   # alterado
        curses.init_pair(3, curses.COLOR_GREEN, -1)    # status ok
        curses.init_pair(4, curses.COLOR_RED, -1)      # erro
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)  # headers
        self.build_rows()

    # --- modelo de linhas ----------------------------------------------

    def build_rows(self) -> None:
        rows: list[Row] = []
        for section, title in (("main", T(self.lang, "cfg_main")), ("advanced", T(self.lang, "cfg_advanced"))):
            rows.append(Row("header", title=title, setting=section))
            if section == "advanced" and not self.advanced_open:
                continue
            group = None
            for s in jc.SETTINGS:
                if s.section != section:
                    continue
                if s.group != group:
                    group = s.group
                    rows.append(Row("group", title=jc.group_for(group, self.lang)))
                rows.append(Row("setting", setting=s))
        self.rows = rows
        self.cursor = min(self.cursor, len(rows) - 1)
        if self.rows[self.cursor].kind == "group":
            self.cursor += 1

    def dirty(self) -> bool:
        return self.cfg != self.saved

    # --- render ---------------------------------------------------------

    def fmt_value(self, s) -> str:
        v = self.cfg[s.key]
        if s.secret:
            return jc.mask(v) or T(self.lang, "cfg_secret_empty")
        if isinstance(v, bool):
            return T(self.lang, "cfg_on") if v else T(self.lang, "cfg_off")
        if isinstance(v, float):
            return f"{v:g}"
        text = str(v)
        if s.multiline:
            text = text.replace("\n", " ")
        return text if len(text) <= 42 else text[:39] + "..."

    def draw(self) -> None:
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        width = min(w - 2, 100)

        title = " " + T(self.lang, "cfg_title")
        marker = "  " + T(self.lang, "cfg_modified") if self.dirty() else ""
        scr.addstr(0, 0, title, curses.A_BOLD)
        if marker:
            scr.addstr(0, len(title), marker, curses.color_pair(2))
        path = str(jc.CONFIG_FILE).replace(str(Path.home()), "~")
        scr.addstr(0, max(len(title) + len(marker) + 2, width - len(path)), path[: w - 1], curses.A_DIM)
        scr.hline(1, 0, curses.ACS_HLINE, width)

        # rodapé: help do item (até 4 linhas — cresce com o texto) + atalhos
        row = self.rows[self.cursor]
        if row.kind == "setting":
            s = row.setting
            extra = f"  {T(self.lang, 'cfg_default')}: {s.default!r}" if not s.multiline else ""
            help_lines = textwrap.wrap(jc.text_for(s, self.lang)[1] + extra, width - 2)[:4]
        elif row.setting == "advanced":
            help_lines = [T(self.lang, "cfg_expand")]
        else:
            help_lines = [""]

        top = 2
        footer_h = 4 + len(help_lines)  # hline + help + 2 linhas de atalhos + status
        avail = h - top - footer_h
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + avail:
            self.scroll = self.cursor - avail + 1

        label_w = 38
        y = top
        for idx in range(self.scroll, min(len(self.rows), self.scroll + avail)):
            row = self.rows[idx]
            selected = idx == self.cursor
            if row.kind == "header":
                arrow = "" if row.setting == "main" else ("▾ " if self.advanced_open else "▸ ")
                attr = curses.color_pair(5) | curses.A_BOLD | (curses.A_REVERSE if selected else 0)
                scr.addstr(y, 0, f" {arrow}{row.title} ".ljust(width), attr)
            elif row.kind == "group":
                scr.addstr(y, 2, row.title, curses.color_pair(1) | curses.A_DIM)
            else:
                s = row.setting
                changed = self.cfg[s.key] != s.default
                attr = curses.A_REVERSE if selected else 0
                label = ("* " if changed else "  ") + jc.text_for(s, self.lang)[0]
                scr.addstr(y, 2, label[: label_w - 1].ljust(label_w), attr | (curses.color_pair(2) if changed and not selected else 0))
                val = self.fmt_value(s)
                if s.choices:
                    val = f"‹ {val} ›"
                elif isinstance(s.default, bool):
                    val = f"[{'x' if self.cfg[s.key] else ' '}] {val}"
                scr.addstr(y, 2 + label_w, val[: width - label_w - 3], attr)
            y += 1

        fy = h - footer_h
        scr.hline(fy, 0, curses.ACS_HLINE, width)
        for i, line in enumerate(help_lines):
            scr.addstr(fy + 1 + i, 1, line[: width - 1], curses.A_DIM)
        ky = fy + 1 + len(help_lines)
        keys1 = T(self.lang, "cfg_keys1")
        keys2 = T(self.lang, "cfg_keys2")
        scr.addstr(ky, 1, keys1[: width - 1], curses.color_pair(1) | curses.A_BOLD)
        scr.addstr(ky + 1, 1, keys2[: width - 1], curses.color_pair(1))
        if self.status:
            color = curses.color_pair(4) if self.status.startswith(("erro", "error")) else curses.color_pair(3)
            scr.addstr(ky + 2, 1, self.status[: width - 1], color)
        scr.refresh()

    # --- edição ---------------------------------------------------------

    def prompt(self, label: str, initial: str = "") -> str | None:
        """Linha de edição no rodapé. Retorna None se cancelou (Esc)."""
        h, w = self.scr.getmaxyx()
        y = h - 1
        buf = list(initial)
        curses.curs_set(1)
        try:
            while True:
                self.scr.move(y, 0)
                self.scr.clrtoeol()
                text = f"{label}: {''.join(buf)}"
                self.scr.addstr(y, 0, text[: w - 1])
                self.scr.refresh()
                ch = self.scr.get_wch()
                if ch in ("\n", "\r"):
                    return "".join(buf)
                if ch == "\x1b":
                    return None
                if ch in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    if buf:
                        buf.pop()
                elif isinstance(ch, str) and ch.isprintable():
                    buf.append(ch)
        finally:
            curses.curs_set(0)
            self.scr.move(y, 0)
            self.scr.clrtoeol()

    def step(self, s, direction: int) -> None:
        v = self.cfg[s.key]
        if s.key == "language":
            self.cfg[s.key] = s.choices[(s.choices.index(v) + direction) % len(s.choices)]
            self.lang = self.cfg[s.key]
            self.build_rows()
            return
        if s.choices:
            i = s.choices.index(v) if v in s.choices else -1
            self.cfg[s.key] = s.choices[(i + direction) % len(s.choices)]
        elif isinstance(v, bool):
            self.cfg[s.key] = not v
        elif isinstance(v, (int, float)) and s.step is not None:
            nv = v + direction * s.step
            if s.min is not None:
                nv = max(s.min, nv)
            if s.max is not None:
                nv = min(s.max, nv)
            self.cfg[s.key] = int(round(nv)) if isinstance(v, int) else round(nv, 4)
        else:
            self.edit(s)

    def edit(self, s) -> None:
        if s.multiline:
            self.edit_in_editor(s)
            return
        raw = self.prompt(jc.text_for(s, self.lang)[0], "" if s.secret else str(self.cfg[s.key]))
        if raw is None:
            return
        if s.secret and raw == "":
            return  # Enter vazio mantém a chave atual; use d pra limpar
        try:
            self.cfg[s.key] = jc._coerce(s, raw)
            self.status = ""
        except ValueError as e:
            self.status = f"{T(self.lang, 'cfg_error')}: {e}"

    def edit_in_editor(self, s) -> None:
        editor = os.environ.get("EDITOR") or shutil.which("nvim") or shutil.which("nano") or "vi"
        tmp = Path(f"/tmp/jarvis-{s.key}.txt")
        tmp.write_text(str(self.cfg[s.key]))
        curses.endwin()
        subprocess.run([editor, str(tmp)])
        self.scr.refresh()
        try:
            self.cfg[s.key] = tmp.read_text().rstrip("\n")
        except OSError:
            pass
        tmp.unlink(missing_ok=True)

    def edit_file(self) -> None:
        """Abre o config.toml inteiro no editor e recarrega."""
        if self.dirty():
            jc.save(self.cfg)
        editor = os.environ.get("EDITOR") or shutil.which("nvim") or shutil.which("nano") or "vi"
        jc.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not jc.CONFIG_FILE.exists():
            jc.save(self.cfg)
        curses.endwin()
        subprocess.run([editor, str(jc.CONFIG_FILE)])
        self.scr.refresh()
        self.cfg = jc.load()
        self.saved = dict(self.cfg)
        self.lang = self.cfg["language"]
        self.status = T(self.lang, "cfg_reloaded")

    # --- ações ----------------------------------------------------------

    def save(self) -> None:
        jc.save(self.cfg)
        self.saved = dict(self.cfg)
        active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", UNIT]).returncode == 0
        if os.environ.get("JARVIS_CONFIG_NO_RESTART"):
            self.status = T(self.lang, "cfg_saved")
        elif active:
            subprocess.run(["systemctl", "--user", "restart", UNIT])
            self.status = T(self.lang, "cfg_saved_restart")
        else:
            self.status = T(self.lang, "cfg_saved_off")

    def save_profile(self) -> None:
        name = self.prompt(T(self.lang, "cfg_profile_name"))
        if not name:
            return
        path = jc.save_profile(name, self.cfg)
        self.status = T(self.lang, "cfg_profile_saved", path=path)

    def load_profile(self) -> None:
        profiles = jc.list_profiles()
        if not profiles:
            self.status = T(self.lang, "cfg_no_profiles")
            return
        name = self.prompt(T(self.lang, "cfg_profile_pick", names=", ".join(profiles)))
        if not name:
            return
        if name not in profiles:
            self.status = T(self.lang, "cfg_profile_missing", name=name)
            return
        self.cfg = jc.load_profile(name)
        self.status = T(self.lang, "cfg_profile_loaded", name=name)

    def run(self) -> None:
        while True:
            self.draw()
            ch = self.scr.getch()
            row = self.rows[self.cursor]
            if ch == 27:
                # Esc solto = sair; Esc seguido de bytes = sequência de tecla não mapeada
                self.scr.nodelay(True)
                following = self.scr.getch()
                self.scr.nodelay(False)
                if following != -1:
                    continue
                ch = ord("q")
            if ch == ord("q"):
                if self.dirty():
                    ans = self.prompt(T(self.lang, "cfg_quit_prompt"))
                    if ans is None:
                        continue
                    if ans.strip().lower().startswith("s"):
                        self.save()
                    elif not ans.strip().lower().startswith("n"):
                        continue
                return
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.move(1)
            elif ch in (curses.KEY_UP, ord("k")):
                self.move(-1)
            elif ch in (curses.KEY_NPAGE,):
                self.move(10)
            elif ch in (curses.KEY_PPAGE,):
                self.move(-10)
            elif ch in (curses.KEY_RIGHT, ord("l")) and row.kind == "setting":
                self.step(row.setting, +1)
            elif ch in (curses.KEY_LEFT, ord("h")) and row.kind == "setting":
                self.step(row.setting, -1)
            elif ch in (10, 13, ord(" ")):
                if row.kind == "header" and row.setting == "advanced":
                    self.advanced_open = not self.advanced_open
                    self.build_rows()
                elif row.kind == "setting":
                    s = row.setting
                    if s.choices or isinstance(s.default, bool):
                        self.step(s, +1)
                    else:
                        self.edit(s)
            elif ch == ord("d") and row.kind == "setting":
                self.cfg[row.setting.key] = row.setting.default
            elif ch == ord("D"):
                self.cfg = jc.defaults()
                self.status = T(self.lang, "cfg_all_defaults")
            elif ch == ord("s"):
                self.save()
            elif ch == ord("p"):
                self.save_profile()
            elif ch == ord("o"):
                self.load_profile()
            elif ch == ord("e"):
                self.edit_file()
                self.build_rows()

    def move(self, delta: int) -> None:
        n = len(self.rows)
        i = self.cursor
        for _ in range(abs(delta)):
            j = i + (1 if delta > 0 else -1)
            while 0 <= j < n and self.rows[j].kind == "group":
                j += 1 if delta > 0 else -1
            if not 0 <= j < n:
                break
            i = j
        self.cursor = i


def main() -> None:
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(lambda scr: App(scr).run())


if __name__ == "__main__":
    main()
