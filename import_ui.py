"""Interactive Catch Up and Dump Info screens for Storyteller's curses panel."""
import curses
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import import_modes as importer
import memory_worker as memory
import prompt_mode

ROOT = Path(__file__).resolve().parent


class ImportUI:
    def arm_next_prompt(self, project, mode):
        current = prompt_mode.read_state(project).get("armed")
        title = "CATCH UP" if mode == "catchup" else "DUMP INFO"
        if current and current.get("mode") == mode:
            if self.confirm(f"{title} is already armed for {project['name']}.\n"
                            "Cancel it so your next Jan message behaves normally?"):
                prompt_mode.cancel(project)
                self.notice("One-shot import cancelled; Jan is back to normal.")
            return
        try:
            prompt_mode.arm(project, mode)
        except (OSError, ValueError) as exc:
            self.overlay("COULD NOT ARM MODE", str(exc))
            return
        self.notice(f"{title} ARMED for {project['alias']}. Paste the NEXT message into Jan.")
        self.overlay(
            title + " / READY FOR NEXT JAN MESSAGE",
            f"Project: {project['name']}\n"
            f"Select model: {project['alias']} in Jan.\n\n"
            "Paste the story or lore directly into Jan's chat.\n"
            "The NEXT sent message will be captured and catalogued.\n"
            "Cydonia will NOT generate a story from that message.\n"
            "The import runs section by section using the CPU memory model.\n"
            + ("The story position will advance when Catch Up finishes.\n"
               if mode == "catchup" else
               "The story position will NOT move.\n")
            + "\nPress J in Storyteller to check progress.\n"
            "Press the same mode key again to cancel before sending.",
            "Press any key to return; Storyteller stays armed")

    def choose_file_import(self, project):
        choice = self.overlay(
            "OPTIONAL FILE IMPORT",
            "Choose which mode should process a text file or chapter folder:\n\n"
            "C — Catch Up (updates story position)\n"
            "I — Dump Info (leaves story position unchanged)\n\n"
            "This is optional. For ordinary text, simply use C or I\n"
            "from the main screen and paste into your next Jan message.",
            "Press C / I, or Esc to cancel")
        if choice in (ord("c"), ord("C")):
            self.import_mode(project, "catchup")
        elif choice in (ord("i"), ord("I")):
            self.import_mode(project, "dump")

    def prompt_status(self, project):
        latest = prompt_mode.recent_task(project)
        armed = prompt_mode.read_state(project).get("armed")
        if not latest:
            self.overlay("JAN PROMPT IMPORTS",
                         "No captured Jan prompts for this project yet.\n\n"
                         + (f"{armed['mode'].upper()} is ARMED for your next Jan message."
                            if armed else "Press C or I to arm the next Jan prompt."))
            return
        task_path = Path(latest["task_path"])
        while True:
            latest = prompt_mode.read_task(task_path)
            progress_text = ""
            try:
                plan = importer.prepare(project, latest["mode"], latest["source"])
                progress = importer.status_path(project, plan["job_id"])
                if progress.is_file():
                    status = json.loads(progress.read_text())
                    progress_text = (
                        f"Sections: {status.get('completed', 0)}/{status.get('total', '?')}\n"
                        f"Facts saved: {status.get('facts', 0)}\n"
                        f"Needs review: {status.get('rejected', 0)}\n")
            except (OSError, ValueError):
                pass
            body = (
                f"Project: {project['name']}\n"
                f"Mode: {latest.get('mode', '?').upper()}\n"
                f"Status: {latest.get('status', '?').upper()}\n"
                f"Text: {latest.get('characters', 0):,} characters\n"
                + progress_text
                + f"Source: {latest.get('source', '?')}\n"
                + (f"Error: {latest['error']}\n" if latest.get("error") else "")
                + "\nAll inputs are saved locally and can resume.\n"
                "P pauses the current import. R resumes a paused/failed import."
            )
            choice = self.overlay(
                "JAN PROMPT IMPORT / STATUS", body,
                "P pause | R resume | Any other key return")
            if choice in (ord("p"), ord("P")):
                try:
                    prompt_mode.request_pause(project, latest["token"])
                    self.notice("Import will pause after its current section.")
                except (OSError, ValueError) as exc:
                    self.notice(str(exc))
                continue
            if choice in (ord("r"), ord("R")):
                try:
                    prompt_mode.retry_task(project, latest["token"])
                    self.notice("Import requeued; work will resume from its checkpoint.")
                except (OSError, ValueError) as exc:
                    self.notice(str(exc))
                continue
            return

    def choose_import_source(self, project, mode):
        label = "CATCH UP" if mode == "catchup" else "DUMP INFO"
        key = self.overlay(
            label + " / INPUT",
            "1. Select an existing .md/.txt file or folder of chapters\n"
            "2. Paste or compose information in a text editor\n\n"
            "Both modes catalogue characters, events and world facts.\n"
            "Catch Up ALSO moves the story to the END of its material.\n"
            "Dump Info NEVER changes the story's current position.\n\n"
            "Source documents are never overwritten.",
            "Press 1 or 2, Esc to cancel")
        if key in (ord("1"), curses.KEY_F1):
            source = self.prompt(
                label + " / FILE OR FOLDER",
                "Paste an absolute path to a text file or folder:")
            return Path(source).expanduser() if source else None
        if key == ord("2"):
            saved = Path(project["folder"]) / ".storyteller" / "imports" / "inputs"
            saved.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = saved / (mode + "-" + stamp + ".md")
            n = 1
            while path.exists():
                path = saved / (mode + "-" + stamp + f"-{n}.md")
                n += 1
            path.write_text("", encoding="utf-8")
            curses.def_prog_mode()
            curses.endwin()
            try:
                editor = os.environ.get("EDITOR", "nano").split()
                if not editor:
                    editor = ["nano"]
                subprocess.run(editor + [str(path)], check=False)
            except OSError as exc:
                self.notice("Could not open text editor: " + str(exc))
                return None
            finally:
                curses.reset_prog_mode()
                self.screen.clear()
                self.screen.refresh()
            if not path.read_text(encoding="utf-8", errors="replace").strip():
                path.unlink(missing_ok=True)
                self.notice("Empty text input was discarded.")
                return None
            self.notice("Saved your pasted text to " + str(path))
            return path
        return None

    def import_mode(self, project, mode):
        if getattr(self.supervisor, "import_process", None) is not None:
            running = self.supervisor.import_process
            if running.poll() is None:
                self.notice("An import is already running. Finish or pause it first.")
                return
        source = self.choose_import_source(project, mode)
        if source is None:
            return self.notice("No text imported.")
        try:
            plan = importer.prepare(project, mode, source)
        except (OSError, ValueError) as exc:
            self.overlay("UNSUPPORTED INPUT", str(exc))
            return
        jobid = plan["job_id"]
        status = importer.status_path(project, jobid)
        old = json.loads(status.read_text()) if status.exists() else {}
        label = "CATCH UP" if mode == "catchup" else "DUMP INFO"
        if old.get("state") == "complete":
            self.overlay(label + " / ALREADY DONE",
                         "This exact input was fully imported before.\n"
                         "No additional changes are necessary.\n"
                         "If you have changed the input, save it and import again.")
            return
        if not memory.is_healthy():
            self.overlay("MEMORY AI OFFLINE",
                         "The small CPU memory model is not available.\n\n"
                         "Start Storyteller with S in the control panel\n"
                         "and try again once Memory CPU says ONLINE.")
            return
        preview = (
            f"Project: {project['name']}\n"
            f"Mode: {label}\n"
            f"Input: {source}\n"
            f"Files: {plan['file_count']}\n"
            f"Text: {plan['characters']:,} characters\n"
            f"Sections: {plan['sections']}\n"
            f"Already finished: {old.get('completed', 0)}\n\n"
            "Completed sections and confirmed facts are saved as we go.\n"
            + ("Catch Up will move the story point to this input's ending.\n"
               if mode == "catchup" else
               "Dump Info will NOT move the story's current point.\n")
            + "Press P during an import to pause and resume another time.\n"
            "This will ONLY write to the project's .storyteller folder."
        )
        if not self.confirm(preview):
            return self.notice("Import cancelled.")
        joblog = status.with_suffix(".log")
        joblog.parent.mkdir(parents=True, exist_ok=True)
        fp = joblog.open("a", encoding="utf-8", buffering=1)
        command = [sys.executable, "-u", str(ROOT / "import_modes.py"),
                   "run", "--project", project["alias"],
                   "--mode", mode, "--source", str(source)]
        try:
            proc = subprocess.Popen(command, cwd=str(ROOT),
                                    stdout=fp, stderr=subprocess.STDOUT)
        except OSError as exc:
            fp.close()
            self.overlay("IMPORT COULD NOT START", str(exc))
            return
        self.supervisor.import_process = proc
        self.supervisor.import_log = fp
        try:
            self.monitor_import(project, mode, source, plan, proc, status, joblog)
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=3)
            self.supervisor.import_process = None
            self.supervisor.import_log = None
            fp.close()

    def monitor_import(self, project, mode, source, plan, proc, status_file, log_file):
        label = "CATCH UP" if mode == "catchup" else "DUMP INFO"
        pause_requested = False
        first_finished = None
        self.screen.timeout(300)
        try:
            while True:
                h, w = self.screen.getmaxyx()
                self.screen.erase()
                if h < 20 or w < 68:
                    self.put(1, 2, "Enlarge terminal to at least 68x20.")
                    self.screen.refresh()
                    key = self.screen.getch()
                    if key in (ord("p"), ord("P"), 27):
                        if proc.poll() is None and not pause_requested:
                            proc.send_signal(signal.SIGINT)
                            pause_requested = True
                    continue
                data = {}
                if status_file.exists():
                    try:
                        data = json.loads(status_file.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        pass
                completed = int(data.get("completed", 0))
                total = int(data.get("total", plan["sections"]))
                facts = int(data.get("facts", 0))
                rejected = int(data.get("rejected", 0))
                ratio = min(1.0, completed / max(1, total))
                bar_width = max(10, min(58, w-22))
                filled = int(bar_width * ratio)
                progress = "[" + "#" * filled + "-" * (bar_width-filled) + "]"
                state = data.get("state", "starting")
                if pause_requested and proc.poll() is None:
                    state = "finishing current section before pausing"
                self.put(1, 3, "STORYTELLER / " + label, self.color(1) | curses.A_BOLD)
                self.line(2)
                self.put(4, 3, "Project: " + project["name"])
                self.put(5, 3, "Input: " + str(source))
                self.put(7, 3, f"Progress: {completed} / {total} sections")
                self.put(8, 3, progress + f" {ratio:>5.0%}", self.color(2))
                self.put(10, 3, f"Facts saved: {facts}    Needs review: {rejected}")
                self.put(11, 3, "Current: " + str(data.get("current_section", "")) +
                         "  " + Path(data.get("current_source", "")).name)
                self.put(12, 3, "Status: " + str(state), self.color(3))
                self.line(14)
                self.put(15, 3, "RECENT IMPORT ACTIVITY", self.color(1) | curses.A_BOLD)
                for i, line in enumerate(self.read_import_activity(log_file, max(1, min(4, h-20)))):
                    self.put(16+i, 4, line)
                if proc.poll() is None:
                    self.put(h-3, 3, "[P] Pause after current section  [Esc] Pause and return")
                else:
                    self.put(h-3, 3, "[Enter] Return to projects")
                    if data.get("state") == "complete":
                        self.put(h-2, 3, "Import complete. Your memory files are ready.", self.color(2))
                    else:
                        self.put(h-2, 3, "Stopped. Reopen the same input to resume.", self.color(3))
                self.screen.refresh()
                key = self.screen.getch()
                if key in (ord("p"), ord("P"), 27) and proc.poll() is None and not pause_requested:
                    proc.send_signal(signal.SIGINT)
                    pause_requested = True
                if proc.poll() is not None:
                    if first_finished is None:
                        first_finished = time.monotonic()
                    if key in (10, 13, curses.KEY_ENTER, 27, ord("q")):
                        break
                    if time.monotonic() - first_finished > 8:
                        break
            result = json.loads(status_file.read_text()) if status_file.exists() else {}
            self.notice(f"{label}: {result.get('state', 'stopped')} — "
                        f"{result.get('completed', 0)}/{plan['sections']} sections, "
                        f"{result.get('facts', 0)} facts, {result.get('rejected', 0)} for review.")
        finally:
            self.screen.timeout(650)

    def view_import_reviews(self, project):
        path = Path(project["folder"]) / ".storyteller" / "imports" / "review.jsonl"
        if not path.exists():
            self.overlay("IMPORT REVIEWS", "No import reviews were recorded for this project.")
            return
        rows = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except json.JSONDecodeError:
                pass
        if not rows:
            self.overlay("IMPORT REVIEWS", "There are no review entries.")
            return
        index = len(rows) - 1
        self.screen.timeout(-1)
        try:
            while True:
                self.screen.erase()
                h, w = self.screen.getmaxyx()
                row = rows[index]
                self.put(1, 3, f"STORYTELLER / IMPORT REVIEWS  {index+1}/{len(rows)}",
                         self.color(1) | curses.A_BOLD)
                self.line(2)
                self.put(4, 3, "Project: " + project["name"])
                self.put(6, 3, "Source: " + str(row.get("source", "?")))
                self.put(7, 3, "Section: " + str(row.get("section", "?")))
                self.put(9, 3, "Reason: " + str(row.get("reason", "?")), self.color(3))
                self.put(11, 3, "Proposal needing review:")
                raw = str(row.get("detail", ""))
                width = max(20, w - 12)
                for i in range(0, min(len(raw), width * max(1, h-19)), width):
                    self.put(12+i//width, 5, raw[i:i+width])
                self.line(h-4)
                self.put(h-3, 3, "Left/Right: browse    Esc/Enter/Q: return")
                self.put(h-2, 3, "Rejected proposals have NOT been added to canon.")
                self.screen.refresh()
                key = self.screen.getch()
                if key in (27,10,13,ord("q"),ord("Q")):
                    break
                if key in (curses.KEY_LEFT,curses.KEY_UP,ord("h"),ord("k")):
                    index = (index - 1) % len(rows)
                if key in (curses.KEY_RIGHT,curses.KEY_DOWN,ord("l"),ord("j")):
                    index = (index + 1) % len(rows)
        finally:
            self.screen.timeout(650)


    @staticmethod
    def read_import_activity(log_file, n=4):
        try:
            with log_file.open("rb") as fp:
                fp.seek(0, os.SEEK_END)
                fp.seek(max(0, fp.tell()-7000))
                raw = fp.read().decode("utf-8", errors="replace")
            return raw.splitlines()[-n:]
        except OSError:
            return []
