"""Interactive editor for optional per-project user prompt prefixes and suffixes."""
import curses
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

import prompt_affixes


class AffixUI:
    def prompt_affix_settings(self, project):
        while True:
            try:
                cfg = prompt_affixes.get_settings(project)
                prefix = prompt_affixes.get_text(project, "prefix")
                suffix = prompt_affixes.get_text(project, "suffix")
            except (OSError, ValueError) as exc:
                self.overlay("PROMPT TEXT ERROR", str(exc))
                return
            body = (
                f"Project: {project['name']}\n\n"
                "1  Edit PREFIX (text before your message)\n"
                "2  Edit SUFFIX (text after your message)\n"
                f"3  Toggle PREFIX: {'ON' if cfg['prefix_enabled'] else 'OFF'}"
                f"  ({len(prefix)} characters)\n"
                f"4  Toggle SUFFIX: {'ON' if cfg['suffix_enabled'] else 'OFF'}"
                f"  ({len(suffix)} characters)\n"
                "5  Preview the complete outgoing user message\n\n"
                "Applied only to the LAST normal Jan user message.\n"
                "The extra text is NOT a system message.\n"
                "Catch Up / Dump Info input is never decorated.\n"
                "Jan's visible chat and saved story files stay unchanged."
            )
            key = self.overlay(
                "PROJECT PROMPT PREFIX / SUFFIX", body,
                "Press 1-5, or Esc / Enter to close")
            if key == ord("1"):
                self.edit_affix(project, "prefix")
            elif key == ord("2"):
                self.edit_affix(project, "suffix")
            elif key in (ord("3"), ord("4")):
                kind = "prefix" if key == ord("3") else "suffix"
                try:
                    state = prompt_affixes.get_settings(project)
                    prompt_affixes.set_enabled(project, kind, not state[kind+"_enabled"])
                    self.notice(kind.capitalize() + " toggled.")
                except (OSError, ValueError) as exc:
                    self.overlay("CANNOT ENABLE", str(exc))
            elif key == ord("5"):
                self.preview_affixes(project)
            else:
                return

    def edit_affix(self, project, kind):
        try:
            base = prompt_affixes.folder(project)
            base.mkdir(parents=True, exist_ok=True)
            old_text = prompt_affixes.get_text(project, kind)
        except (OSError, ValueError) as exc:
            self.overlay("PROMPT TEXT ERROR", str(exc))
            return
        filename = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", prefix="edit-"+kind+"-",
                dir=base, encoding="utf-8", delete=False
            ) as temp:
                filename = Path(temp.name)
                temp.write(old_text)
            curses.def_prog_mode()
            curses.endwin()
            try:
                editor = shlex.split(os.environ.get("EDITOR") or "nano")
                if not editor:
                    editor = ["nano"]
                process = subprocess.run(editor+[str(filename)],check=False)
            finally:
                curses.reset_prog_mode()
                self.screen.clear()
                self.screen.refresh()
            if process.returncode:
                self.notice("Editor exited without saving a new "+kind+".")
                return
            updated = filename.read_text(encoding="utf-8")
            prompt_affixes.set_text(project, kind, updated, enable=True)
            state = "enabled" if updated.strip() else "cleared and disabled"
            self.notice(f"{kind.capitalize()} {state} ({len(updated)} characters).")
        except (OSError, ValueError) as exc:
            self.overlay("COULD NOT SAVE PROMPT TEXT", str(exc))
        finally:
            if filename is not None:
                filename.unlink(missing_ok=True)

    def preview_affixes(self, project):
        prefix, suffix = prompt_affixes.effective(project)
        sample = "This is the message you type in Jan."
        contents = (prefix + "\n\n" if prefix else "") + sample + (
            "\n\n" + suffix if suffix else "")
        text = (
            "This is ONE user message, not a system instruction.\n\n"
            + contents[:1500]
        )
        if len(contents) > 1500:
            text += "\n\n[Preview shortened; complete text is preserved.]"
        self.overlay("PREVIEW: OUTGOING USER PROMPT", text)
