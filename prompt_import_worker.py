#!/usr/bin/env python3
"""Run captured Jan prompts through Storyteller's resumable importer.

The worker is owned by the manually launched gateway. It does not install
a service or continue operating when the Storyteller terminal is closed.
"""
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from time import sleep

import import_modes
import memory_worker
import prompt_mode

HOME = Path(__file__).resolve().parent


def log(message):
    print("[Prompt Import] " + message, flush=True)


class PromptImportWorker:
    def __init__(self, projects):
        self.projects = projects
        self.stop_event = threading.Event()
        self.thread = None
        self.process = None

    def start(self):
        self.thread = threading.Thread(
            target=self.run, name="storyteller-jan-imports", daemon=True)
        self.thread.start()
        log("Ready for one-shot Catch Up / Dump Info prompts from Jan.")

    def run(self):
        while not self.stop_event.is_set():
            try:
                projects = self.projects() if callable(self.projects) else self.projects
                for project in projects:
                    if self.stop_event.is_set():
                        break
                    for task_path, task in prompt_mode.pending_tasks(project):
                        if self.stop_event.is_set():
                            break
                        if not memory_worker.is_healthy():
                            break
                        self.process_task(project, task_path, task)
            except Exception as exc:
                log("Queue error: " + type(exc).__name__ + ": " + str(exc)[:200])
            self.stop_event.wait(1)

    def process_task(self, project, task_path, task):
        if task_path.with_suffix(".pause").exists():
            prompt_mode.update_task(task_path, status="paused")
            return
        log(f"{project['alias']}: {task['mode']} started, "
            f"{task['characters']:,} characters")
        prompt_mode.update_task(task_path, status="running", error=None)
        output_path = task_path.with_suffix(".log")
        command = [
            sys.executable, "-u", str(HOME / "import_modes.py"),
            "run", "--project", project["alias"],
            "--mode", task["mode"], "--source", task["source"],
        ]
        outcome = "error"
        message = None
        try:
            with output_path.open("a", encoding="utf-8", buffering=1) as file:
                self.process = subprocess.Popen(
                    command, cwd=str(HOME), stdout=file,
                    stderr=subprocess.STDOUT)
                while self.process.poll() is None:
                    if self.stop_event.is_set() or task_path.with_suffix(".pause").exists():
                        self.process.send_signal(signal.SIGINT)
                        try:
                            self.process.wait(timeout=4)
                        except subprocess.TimeoutExpired:
                            self.process.terminate()
                            try:
                                self.process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                self.process.kill()
                                self.process.wait(timeout=3)
                        outcome = "paused"
                        break
                    self.stop_event.wait(.2)
                else:
                    if self.process.returncode == 0:
                        outcome = "complete"
                    else:
                        outcome = "error"
                        message = "Extractor exited with status " + str(self.process.returncode)
        except (OSError, ValueError) as exc:
            outcome = "error"
            message = type(exc).__name__ + ": " + str(exc)[:200]
        finally:
            self.process = None

        # The importer writes granular progress. Mirror its final state here.
        try:
            plan = import_modes.prepare(project, task["mode"], task["source"])
            status_path = import_modes.status_path(project, plan["job_id"])
            if status_path.exists():
                record = json.loads(status_path.read_text(encoding="utf-8"))
                if record.get("state") == "complete":
                    outcome = "complete"
                elif record.get("state") in ("paused", "error"):
                    outcome = record["state"]
                    message = record.get("error", message)
                prompt_mode.update_task(
                    task_path, status=outcome, error=message,
                    progress=record.get("completed", 0),
                    sections=record.get("total", plan["sections"]),
                    facts=record.get("facts", 0),
                    rejected=record.get("rejected", 0))
            else:
                prompt_mode.update_task(task_path, status=outcome, error=message)
        except Exception as exc:
            log(f"Could not refresh final import status: {exc}")
            prompt_mode.update_task(task_path, status=outcome, error=message)

        log(f"{project['alias']}: {task['mode']} {outcome}. "
            f"See import status in the terminal control panel.")

    def stop(self):
        self.stop_event.set()
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
        if self.thread is not None:
            self.thread.join(timeout=9)
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
