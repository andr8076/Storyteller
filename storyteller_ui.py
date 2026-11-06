#!/usr/bin/env python3
"""Storyteller terminal control panel (stdlib curses, no persistent service)."""
from __future__ import annotations

import atexit
import curses
import json
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import storyteller
import story_state
import prompt_mode
from import_ui import ImportUI
from affix_ui import AffixUI
import prompt_affixes

HOME = Path(__file__).resolve().parent
WRITER_PORT = int(os.environ.get("STORYTELLER_UI_PORT", "18880"))
CPU_PORT = 18885
NO_ANALYSIS = os.environ.get("STORYTELLER_UI_NO_ANALYSIS", "") == "1"
LOG_FILE = HOME / "storyteller.log"
VALID_ALIAS = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def read_projects():
    return storyteller.load_config()["projects"]


def validate_folder(value):
    if not value.strip():
        raise ValueError("Choose an existing story folder.")
    path = Path(os.path.expandvars(value)).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Folder does not exist: {path}")
    return path


def create_project(name, alias, folder, model):
    name, alias, model = name.strip(), alias.strip(), model.strip()
    if not name:
        raise ValueError("Project name is required.")
    if not VALID_ALIAS.fullmatch(alias):
        raise ValueError("Use a lowercase alias (2-64 letters, numbers or hyphens).")
    if not model:
        raise ValueError("Choose a writing model.")
    folder = validate_folder(folder)
    config = storyteller.load_config()
    if any(p["alias"] == alias for p in config["projects"]):
        raise ValueError("Another project already has that model alias.")
    storyteller.ensure_memory(folder)
    config["projects"].append({"alias": alias, "name": name,
                               "folder": str(folder), "model": model})
    storyteller.save_config(config)
    return alias


def update_project(alias, name, folder, model):
    if not name.strip() or not model.strip():
        raise ValueError("Name and writing model are required.")
    folder = validate_folder(folder)
    config = storyteller.load_config()
    project = next((x for x in config["projects"] if x["alias"] == alias), None)
    if project is None:
        raise ValueError("Project no longer exists.")
    storyteller.ensure_memory(folder)
    project.update(name=name.strip(), folder=str(folder), model=model.strip())
    storyteller.save_config(config)


def detach_project(alias):
    config = storyteller.load_config()
    n = len(config["projects"])
    config["projects"] = [p for p in config["projects"] if p["alias"] != alias]
    if len(config["projects"]) == n:
        raise ValueError("Project no longer exists.")
    # Never delete, move or modify story data on project detach.
    storyteller.save_config(config)


def toggle_response_learning(alias):
    config = storyteller.load_config()
    project = next((p for p in config["projects"] if p["alias"] == alias), None)
    if project is None:
        raise ValueError("Project no longer exists.")
    project["learn_from_responses"] = not project.get("learn_from_responses", True)
    storyteller.save_config(config)
    return project["learn_from_responses"]


def port_available(host, port, timeout=.12):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def recent_lines(path, limit=14):
    if not path.exists():
        return []
    try:
        with path.open("rb") as fp:
            fp.seek(0, os.SEEK_END)
            fp.seek(max(0, fp.tell()-20000))
            contents = fp.read().decode("utf-8", errors="replace")
        return [line for line in contents.splitlines() if line.strip()][-limit:]
    except OSError:
        return []


def get_models():
    try:
        with urlopen("http://127.0.0.1:1337/v1/models", timeout=1) as response:
            data = json.load(response)
        ids = [item.get("id") for item in data.get("data", [])
               if isinstance(item, dict) and isinstance(item.get("id"), str)]
        # Jan also lists Storyteller's virtual aliases; never use those as upstream models.
        return [x for x in dict.fromkeys(ids) if not x.startswith("storyteller-")]
    except (OSError, ValueError, TypeError):
        return []


def project_counts(project):
    base = Path(project["folder"]) / ".storyteller"
    count, errors, reviews = 0, 0, 0
    db = base / "processing.sqlite3"
    if db.is_file():
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=.2) as conn:
                count = conn.execute("SELECT COUNT(*) FROM processed WHERE status='done'").fetchone()[0]
                errors = conn.execute("SELECT COUNT(*) FROM processed WHERE status='error'").fetchone()[0]
        except (OSError, sqlite3.Error):
            pass
    review = base / "review.jsonl"
    if review.is_file():
        try:
            for line in review.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    entry = json.loads(line)
                    reviews += not bool(entry.get("resolution"))
                except (ValueError, TypeError):
                    pass
        except OSError:
            pass
    return count, errors, reviews


class Supervisor:
    """Owns only processes this panel launched; never kills existing Jan services."""

    def __init__(self, port=WRITER_PORT, no_analysis=NO_ANALYSIS):
        self.port = port
        self.no_analysis = no_analysis
        self.process = None
        self.log_stream = None
        self.last_message = ""

    def status(self):
        if self.process is not None and self.process.poll() is None:
            return "RUNNING"
        if port_available("127.0.0.1", self.port):
            return "EXTERNAL"
        return "STOPPED"

    def start(self):
        if self.status() == "RUNNING":
            return True
        if self.status() == "EXTERNAL":
            self.last_message = (
                "An older Storyteller instance is using this port. "
                "Close its terminal first; this panel will not stop it.")
            return False
        try:
            self.log_stream = LOG_FILE.open("a", encoding="utf-8", buffering=1)
            command = [sys.executable, "-u", str(HOME / "storyteller.py"),
                       "serve", "--port", str(self.port)]
            if self.no_analysis:
                command.append("--no-analysis")
            self.process = subprocess.Popen(
                command, cwd=str(HOME), stdout=self.log_stream,
                stderr=subprocess.STDOUT)
            self.last_message = "Starting the gateway and memory AI..." if not self.no_analysis else "Starting gateway..."
            return True
        except OSError as exc:
            self.last_message = f"Could not start: {exc}"
            if self.log_stream:
                self.log_stream.close()
                self.log_stream = None
            return False

    def stop(self):
        imported = getattr(self, "import_process", None)
        if imported is not None and imported.poll() is None:
            imported.send_signal(signal.SIGINT)
            try:
                imported.wait(timeout=2)
            except subprocess.TimeoutExpired:
                imported.terminate()
                try:
                    imported.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    imported.kill()
                    imported.wait(timeout=3)
        self.import_process = None
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=9)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=4)
            self.process = None
        if self.log_stream:
            self.log_stream.close()
            self.log_stream = None
        self.last_message = "Storyteller stopped. Jan's regular models are unaffected."


class Panel(ImportUI, AffixUI):
    def __init__(self, stdscr, supervisor):
        self.screen = stdscr
        self.supervisor = supervisor
        self.selected = 0
        self.scroll = 0
        self.message = supervisor.last_message or "Arrows: select project | Enter: details | ?: help"
        self.last_health = 0
        self.health = {}
        curses.curs_set(0)
        self.screen.keypad(True)
        self.screen.timeout(650)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(5, curses.COLOR_RED, -1)

    def color(self, pair):
        return curses.color_pair(pair) if curses.has_colors() else 0

    def put(self, y, x, value, attrs=0):
        h, w = self.screen.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        display = str(value).replace("\n", " ").replace("\r", " ")
        try:
            self.screen.addnstr(y, x, display, max(0, w-x-1), attrs)
        except curses.error:
            pass

    def line(self, y, attrs=0):
        _, w = self.screen.getmaxyx()
        self.put(y, 2, "-" * max(0, w-5), attrs)

    def cache_health(self):
        if time.monotonic() - self.last_health < 2:
            return self.health
        status = self.supervisor.status()
        self.health = {
            "gateway": status,
            "cpu": port_available("127.0.0.1", CPU_PORT),
            "jan": port_available("127.0.0.1", 1337),
        }
        self.last_health = time.monotonic()
        return self.health

    def draw(self):
        self.screen.erase()
        h, w = self.screen.getmaxyx()
        if w < 68 or h < 22:
            self.put(1, 2, "Please enlarge this terminal (minimum 68x22).")
            self.put(3, 2, "Press Q to quit.")
            self.screen.refresh()
            return
        projects = read_projects()
        if self.selected >= len(projects):
            self.selected = max(0, len(projects)-1)
        status = self.cache_health()
        self.put(1, 3, "STORYTELLER / CONTROL PANEL", self.color(1) | curses.A_BOLD)
        gateway_label = status["gateway"]
        gateway_color = 2 if gateway_label == "RUNNING" else (3 if gateway_label == "EXTERNAL" else 5)
        self.put(3, 3, "Gateway: ")
        self.put(3, 12, gateway_label, self.color(gateway_color) | curses.A_BOLD)
        self.put(3, 28, "Memory CPU: " + ("ONLINE" if status["cpu"] else "OFFLINE"),
                 self.color(2 if status["cpu"] else 3))
        self.put(3, 55, "Jan API: " + ("ONLINE" if status["jan"] else "OFFLINE"),
                 self.color(2 if status["jan"] else 3))
        self.put(4, 3, f"Projects: {len(projects)}  |  Local port: {self.supervisor.port}  |  Writing model chosen per project")
        self.line(5)
        self.put(6, 3, "PROJECTS", self.color(1) | curses.A_BOLD)
        project_rows = max(2, min(8, h-20))
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + project_rows:
            self.scroll = self.selected - project_rows + 1
        self.scroll = max(0, min(self.scroll, max(0, len(projects)-project_rows)))
        for row_num, project in enumerate(projects[self.scroll:self.scroll+project_rows]):
            i = self.scroll+row_num
            name = str(project.get("name", "Untitled"))[:25].ljust(26)
            alias = str(project.get("alias", "?"))[:29]
            row = f" {'>' if i==self.selected else ' '}  {name}  {alias}"
            attrs = self.color(4) | curses.A_BOLD if i==self.selected else 0
            self.put(7+row_num, 3, row, attrs)
        if not projects:
            self.put(8, 5, "No projects yet. Press A to add one.")
        if len(projects) > project_rows:
            self.put(6, 20,
                     f"Showing {self.scroll+1}-{min(len(projects),self.scroll+project_rows)} of {len(projects)} (Up/Down)", self.color(3))
        detail_y = 8+project_rows
        self.line(detail_y-1)
        self.put(detail_y, 3, "SELECTED PROJECT", self.color(1) | curses.A_BOLD)
        if projects:
            project = projects[self.selected]
            armed = prompt_mode.read_state(project).get("armed")
            status_label = ("NEXT JAN MESSAGE: " + armed["mode"].upper() + " ARMED"
                            if armed else "NORMAL CHAT")
            self.put(detail_y, 23, status_label, self.color(3 if armed else 2))
            settings = prompt_affixes.get_settings(project)
            enabled = [label for label, key in (("PREFIX", "prefix_enabled"),
                                               ("SUFFIX", "suffix_enabled"))
                       if settings[key]]
            self.put(detail_y, 67, "  ".join(enabled) if enabled else "", self.color(1))
            self.put(detail_y+1, 3, "Folder: " + project["folder"])
            self.put(detail_y+2, 3, "Writer: " + project["model"])
            self.put(detail_y+3, 3, "Jan model ID: " + project["alias"])
            self.put(detail_y+4, 3, "Learn from writer replies: " +
                     ("ON" if project.get("learn_from_responses", True) else "OFF"))
        else:
            self.put(detail_y+1, 3, "Choose an existing folder when creating a project.")
        activity_y = detail_y+6
        self.line(activity_y-1)
        self.put(activity_y, 3, "RECENT ACTIVITY", self.color(1) | curses.A_BOLD)
        logs = recent_lines(LOG_FILE, min(3, max(0, h-5-activity_y)))
        for i, msg in enumerate(logs):
            self.put(activity_y+1+i, 4, msg)
        if not logs:
            self.put(activity_y+1, 4, "No recent activity yet.")
        self.line(h-4)
        self.put(h-3, 3, "P Prefix/Suffix  C Catch Up  I Dump Info  F File Import  J Status")
        self.put(h-2, 3, "T Story Position  M Reply Learning  A Add  E Edit  D Remove  S Start/Stop  ? Help  Q Quit", self.color(3))
        self.screen.refresh()

    def notice(self, text):
        self.message = text[:150]
        self.last_health = 0

    def overlay(self, title, body, buttons="Press any key to return"):
        h, w = self.screen.getmaxyx()
        lines = body.splitlines()
        width = max(55, min(w-4, 95))
        height = min(h-2, max(10, len(lines)+6))
        y, x = max(0, (h-height)//2), max(0, (w-width)//2)
        win = curses.newwin(height, width, y, x)
        win.keypad(True)
        win.bkgd(" ", self.color(0))
        win.box()
        win.addnstr(1, 2, title, width-4, self.color(1) | curses.A_BOLD)
        for i, line in enumerate(lines[:height-5]):
            win.addnstr(i+3, 2, line, width-4)
        win.addnstr(height-2, 2, buttons, width-4, self.color(3))
        win.refresh()
        result = win.getch()
        del win
        self.draw()
        return result

    def confirm(self, text):
        key = self.overlay("CONFIRM", text, "Press Y to confirm, any other key to cancel")
        return key in (ord("y"), ord("Y"))

    def prompt(self, title, label, initial="", limit=480):
        h, w = self.screen.getmaxyx()
        width = max(55, min(w-4, 94))
        height = 10
        x, y = (w-width)//2, max(0, (h-height)//2)
        buffer = list(str(initial))
        cursor = len(buffer)
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            while True:
                win = curses.newwin(height, width, y, x)
                win.keypad(True)
                win.box()
                win.addnstr(1, 2, title, width-4, self.color(1) | curses.A_BOLD)
                win.addnstr(3, 2, label, width-4)
                field_width = width-8
                left = max(0, cursor - field_width+1)
                if len(buffer) - left < field_width:
                    left = max(0, len(buffer) - field_width)
                part = "".join(buffer[left:left+field_width])
                win.addnstr(5, 3, part.ljust(field_width), field_width, curses.A_REVERSE)
                win.addnstr(7, 2, "Enter: accept    Esc: cancel    Ctrl+U: clear", width-4, self.color(3))
                win.move(5, min(width-4, 3+cursor-left))
                win.refresh()
                try:
                    key = win.get_wch()
                except curses.error:
                    continue
                if isinstance(key, str):
                    if key in ("\n", "\r"):
                        return "".join(buffer).strip()
                    if key == "\x1b":
                        return None
                    if key == "\x15":
                        buffer, cursor = [], 0
                    elif key in ("\x08", "\x7f"):
                        if cursor:
                            buffer.pop(cursor-1)
                            cursor -= 1
                    elif key.isprintable() and len(buffer) < limit:
                        buffer.insert(cursor, key)
                        cursor += 1
                else:
                    if key in (curses.KEY_ENTER,):
                        return "".join(buffer).strip()
                    if key == curses.KEY_BACKSPACE and cursor:
                        buffer.pop(cursor-1)
                        cursor -= 1
                    elif key == curses.KEY_DC and cursor < len(buffer):
                        buffer.pop(cursor)
                    elif key == curses.KEY_LEFT:
                        cursor = max(0, cursor-1)
                    elif key == curses.KEY_RIGHT:
                        cursor = min(len(buffer), cursor+1)
                    elif key == curses.KEY_HOME:
                        cursor = 0
                    elif key == curses.KEY_END:
                        cursor = len(buffer)
                del win
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.draw()

    def choose_model(self, current=storyteller.DEFAULT_MODEL):
        models = get_models()
        if not models:
            return self.prompt("Writing model", "Enter exact Jan Local API model ID:", current)
        if current not in models:
            models.insert(0, current)
        models = list(dict.fromkeys(models))
        index = models.index(current)
        while True:
            choices = models + ["[Type a model ID manually]"]
            display = ["Model loaded in Jan's Local API:"]
            for i, model in enumerate(choices):
                display.append((" > " if i==index else "   ") + model)
            display.append("")
            display.append("Up/Down to choose | Enter select | Esc cancel")
            h, w = self.screen.getmaxyx()
            if len(choices) > h-10:
                return self.prompt("Writing model", "Enter Jan model ID:", current)
            key = self.overlay("SELECT WRITING MODEL", "\n".join(display), "")
            if key in (curses.KEY_UP, ord("k")):
                index = (index-1) % len(choices)
            elif key in (curses.KEY_DOWN, ord("j")):
                index = (index+1) % len(choices)
            elif key in (10, 13, curses.KEY_ENTER):
                if index == len(models):
                    return self.prompt("Writing model", "Enter Jan model ID:", current)
                return choices[index]
            elif key == 27:
                return None

    def add(self):
        name = self.prompt("NEW PROJECT / 1 of 4", "Project display name:")
        if not name:
            return self.notice("Add cancelled.")
        proposed = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
        alias = self.prompt("NEW PROJECT / 2 of 4", "Stable model ID in Jan:", "storyteller-" + proposed)
        if alias is None:
            return self.notice("Add cancelled.")
        folder = self.prompt("NEW PROJECT / 3 of 4",
                             "Paste the EXISTING story folder's absolute path:")
        if folder is None:
            return self.notice("Add cancelled.")
        model = self.choose_model()
        if model is None:
            return self.notice("Add cancelled.")
        try:
            new_alias = create_project(name, alias, folder, model)
        except (ValueError, OSError) as exc:
            self.overlay("COULD NOT ADD", str(exc))
            return
        projects = read_projects()
        self.selected = next(i for i,p in enumerate(projects) if p["alias"]==new_alias)
        self.notice(f"Added {name}. Select '{new_alias}' in Jan's model list.")

    def edit(self, project):
        alias = project["alias"]
        name = self.prompt("EDIT PROJECT / 1 of 3", "Display name:", project["name"])
        if name is None:
            return self.notice("Edit cancelled.")
        folder = self.prompt("EDIT PROJECT / 2 of 3", "Existing story folder:", project["folder"])
        if folder is None:
            return self.notice("Edit cancelled.")
        if Path(folder).expanduser().resolve() != Path(project["folder"]).resolve():
            if not self.confirm("Point this project to a different folder?\n\n"
                                "Existing story files will NOT be moved.\n"
                                "The new folder's memory will be used instead."):
                return self.notice("Folder change cancelled.")
        model = self.choose_model(project["model"])
        if model is None:
            return self.notice("Edit cancelled.")
        try:
            update_project(alias, name, folder, model)
            self.notice(f"Updated {alias}. Changes apply to the next generation.")
        except (ValueError, OSError) as exc:
            self.overlay("COULD NOT UPDATE", str(exc))

    def remove(self, project):
        if not self.confirm(
            "Remove '" + project["name"] + "' from Storyteller?\n\n"
            "Alias: " + project["alias"] + "\n\n"
            "The project folder and ALL of its story/memory files\n"
            "will remain untouched. Only its registration is removed."):
            return self.notice("Remove cancelled.")
        try:
            detach_project(project["alias"])
            self.selected = max(0, self.selected-1)
            self.notice("Project detached. No story files were deleted.")
        except (ValueError, OSError) as exc:
            self.overlay("COULD NOT REMOVE", str(exc))

    def story_position(self, project):
        state = story_state.current(project)
        if not state:
            self.overlay("STORY POSITION",
                         "No continuation point has been established yet.\n\n"
                         "Use Catch Up (C) to set the story's starting point,\n"
                         "or continue writing an actual scene in Jan.\n\n"
                         "Dump Info (I) never moves the continuation point.")
            return
        excerpt = str(state.get("source_excerpt", "")).strip()
        # On small terminals, show the LAST lines, where continuation begins.
        if len(excerpt) > 700:
            excerpt = "…" + excerpt[-699:]
        width = 77
        words = excerpt.split()
        wrapped, line = [], ""
        for word in words:
            if len(line) + len(word) + 1 > width:
                wrapped.append(line)
                line = word
            else:
                line = (line + " " + word).strip()
        if line:
            wrapped.append(line)
        body = ("Current source: " + str(state.get("source_type")) + "\n"
                "Position: " + str(state.get("position")) + "\n"
                "From: " + str(state.get("source_file") or "Jan-generated scene") + "\n"
                "Last update: " + str(state.get("updated_at", "?")) + "\n\n"
                "Last narrative excerpt:\n" + "\n".join(wrapped))
        self.overlay("STORY POSITION — " + project["name"], body)

    def details(self, project):
        done, errors, reviews = project_counts(project)
        current_state = story_state.current(project)
        import_jobs = list((Path(project["folder"]) / ".storyteller" / "imports" / "jobs").glob("*.json"))
        import_review = Path(project["folder"]) / ".storyteller" / "imports" / "review.jsonl"
        try:
            import_reviews = sum(1 for line in import_review.read_text().splitlines() if line.strip()) if import_review.exists() else 0
        except OSError:
            import_reviews = 0
        body = ("Name: " + project["name"] + "\n"
                "Jan model ID: " + project["alias"] + "\n"
                "Writing model: " + project["model"] + "\n"
                "Folder: " + project["folder"] + "\n\n"
                "Learn from writer replies: " +
                ("ON" if project.get("learn_from_responses", True) else "OFF") + " (M toggles)\n"
                f"Completed analyses: {done}\n"
                f"Failed analyses: {errors}\n"
                f"Needs review: {reviews}\n"                f"Import jobs: {len(import_jobs)}\n"                f"Import review entries: {import_reviews} (press V)\n"
                f"Story position: {current_state.get('position') if current_state else '(not established)'}\n"
                "Use T to view the current story position.\n\n"
                "Memory is in <folder>/.storyteller/memory/\n"
                "The model alias is stable to protect existing history.\n"
                "Changing the folder does not move any files.")
        self.overlay("PROJECT DETAILS", body)

    def logs(self):
        while True:
            lines = recent_lines(LOG_FILE, 18)
            body = "\n".join(lines) if lines else "No activity recorded."
            key = self.overlay("STORYTELLER ACTIVITY", body, "R refresh | any other key return")
            if key not in (ord("r"), ord("R")):
                break

    def help(self):
        self.overlay(
            "QUICK HELP",
            "P / Prefix/Suffix: edit and toggle optional prompt text.\n"
            "M / Reply Learning: toggle analysis of generated scenes per project.\n"
            "A / Add: register a story folder and choose its writing model.\n"
            "E / Edit: change name, folder or writing model.\n"
            "D / Remove: detach the project; NO files are deleted.\n"
            "C / Catch Up: catalogue facts AND advance to the final story point.\n"
            "I / Dump Info: catalogue the same facts WITHOUT moving the story.\n"
            "T / Story Position: show where the next scene should continue.\n"
            "Imports accept text files, folders, or pasted text in nano.\n"
            "V / Import Reviews: browse rejected/uncertain imported facts.\n"
            "Progress checkpoints let you pause and resume imports.\n"
            "Enter: see the selected project's details and processing counts.\n"
            "S: start or stop the gateway and its CPU memory model.\n"
            "L: inspect recent Storyteller activity.\n"
            "R: refresh the display.\n"
            "Q: quit; closes only the gateway started by THIS panel.\n\n"
            "Project changes take effect without restarting the models.\n"
            "Use the model ID shown for the project in Jan.\n"
            "Close an old Storyteller terminal before trying to\n"
            "control an externally running instance here.")

    def run(self):
        while True:
            self.draw()
            key = self.screen.getch()
            if key == -1:
                continue
            h, w = self.screen.getmaxyx()
            if (h < 22 or w < 68) and key not in (ord("q"), ord("Q")):
                continue
            if key in (ord("q"), ord("Q")):
                return
            projects = read_projects()
            if key == curses.KEY_UP:
                self.selected = max(0, self.selected-1)
            elif key == curses.KEY_DOWN:
                self.selected = min(max(0, len(projects)-1), self.selected+1)
            elif key in (ord("r"), ord("R")):
                self.last_health = 0
                self.notice("Refreshed.")
            elif key in (ord("s"), ord("S")):
                if self.supervisor.status() == "RUNNING":
                    if self.confirm("Stop Storyteller?\n\n"
                                    "Jan's Storyteller aliases will be unavailable\n"
                                    "until you start it again."):
                        self.supervisor.stop()
                else:
                    self.supervisor.start()
                self.notice(self.supervisor.last_message)
            elif key in (ord("a"), ord("A")):
                self.add()
            elif key in (ord("?"), curses.KEY_F1):
                self.help()
            elif key in (ord("l"), ord("L")):
                self.logs()
            elif projects and key in (ord("p"), ord("P")):
                self.prompt_affix_settings(projects[self.selected])
            elif projects and key in (ord("m"), ord("M")):
                enabled = toggle_response_learning(projects[self.selected]["alias"])
                self.notice("Learning from writer replies is " + ("ON" if enabled else "OFF") +
                            ". Author messages are still analyzed.")
            elif projects and key in (ord("c"), ord("C")):
                self.arm_next_prompt(projects[self.selected], "catchup")
            elif projects and key in (ord("i"), ord("I")):
                self.arm_next_prompt(projects[self.selected], "dump")
            elif projects and key in (ord("f"), ord("F")):
                self.choose_file_import(projects[self.selected])
            elif projects and key in (ord("j"), ord("J")):
                self.prompt_status(projects[self.selected])
            elif projects and key in (ord("t"), ord("T")):
                self.story_position(projects[self.selected])
            elif projects and key in (ord("v"), ord("V")):
                self.view_import_reviews(projects[self.selected])
            elif projects and key in (10, 13, curses.KEY_ENTER):
                self.details(projects[self.selected])
            elif projects and key in (ord("e"), ord("E")):
                self.edit(projects[self.selected])
            elif projects and key in (ord("d"), ord("D")):
                self.remove(projects[self.selected])


def main():
    controller = Supervisor()
    atexit.register(controller.stop)
    controller.start()

    def closing(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, closing)
    signal.signal(signal.SIGHUP, closing)
    try:
        curses.wrapper(lambda screen: Panel(screen, controller).run())
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
