#!/usr/bin/env python3
"""Two live local calls on disposable fiction; capture outputs for cheap replay.

Runs actual Catch Up persistence with the unchanged base 2B extraction mocked
empty, isolating the focused 9B pass and experimental broad episode pass.
Never reads personal projects or manuscripts and never starts a model server.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import character_knowledge
import import_modes
import memory_worker
import narrative_ledger

SOURCE = ("Sera watched Dain lock the silver flute inside the cedar cabinet. "
          "Dain told Oren, 'The flute burned in the kiln.' Oren believed him. "
          "Later, Oren found the flute inside the cedar cabinet.")


def projection_probe():
    """Separate a retrieval defect from variable model extraction quality."""
    source = ("Oren believed the flute was destroyed. "
              "Later, Oren discovered the flute had never been destroyed.")
    proposals = [
        {"kind": "belief", "actor": "Oren", "claim": "The flute was destroyed",
         "topic": "flute fate", "certainty": "believes", "evidence_numbers": [1]},
        {"kind": "observation", "actor": "Oren", "claim": "The flute had never been destroyed",
         "topic": "flute condition", "certainty": "knows", "evidence_numbers": [2]},
    ]
    entries, rejected = narrative_ledger.validate(proposals, source)
    with tempfile.TemporaryDirectory(prefix="storyteller-projection-") as folder:
        project = {"alias": "projection", "folder": folder}
        narrative_ledger.record(project, "explicit-correction", entries)
        current = narrative_ledger.character_view(project, "Oren")
    return {"source": source, "proposals": proposals, "rejected": rejected,
            "current": current, "expected_current": ["The flute had never been destroyed"],
            "passed": not rejected and [r["claim"] for r in current] ==
            ["The flute had never been destroyed"]}


def compare(endpoint, replay=None):
    report = {"source": SOURCE,
              "scope": "Actual Catch Up; focused and episode passes live; base 2B mocked empty",
              "expected": ["Dain locks flute in cabinet", "Sera witnesses placement",
                           "Dain tells Oren flute burned", "Oren believes flute burned",
                           "Oren later discovers flute in cabinet"],
              "execution": "replay" if replay else "live", "calls": {}}
    report["commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
        text=True).strip()
    focused_call = (lambda *args: replay["calls"]["focused"]["raw"]) if replay else import_modes.request_perspectives
    episode_call = (lambda *args: replay["calls"]["episodes"]["raw"]) if replay else narrative_ledger.propose

    def capture(name, function, *args):
        started = time.monotonic()
        result = function(*args)
        report["calls"][name] = {"seconds": round(time.monotonic() - started, 2),
                                 "raw": result}
        print(f"{name} completed in {report['calls'][name]['seconds']} seconds", flush=True)
        return result

    with tempfile.TemporaryDirectory(prefix="storyteller-comparison-") as folder:
        root = Path(folder)
        source = root / "scene.txt"
        source.write_text(SOURCE, encoding="utf-8")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        project = {"alias": "comparison", "name": "Disposable comparison",
                   "folder": str(root / "project")}
        env = {"STORYTELLER_PERSPECTIVE_ENDPOINT": endpoint,
               "STORYTELLER_LEDGER_SHADOW_ENDPOINT": endpoint}
        with (patch.dict(os.environ, env),
              patch.object(memory_worker, "is_healthy", return_value=True),
              patch.object(import_modes, "request_model", return_value={
                  "changes": [], "knowledge_changes": [], "summary": ""}),
              patch.object(import_modes, "request_perspectives",
                           side_effect=lambda *args: capture("focused", focused_call, *args)),
              patch.object(narrative_ledger, "propose",
                           side_effect=lambda *args: capture("episodes", episode_call, *args))):
            result = import_modes.run_import(project, "catchup", source)
        report["import_state"] = result["state"]
        report["source_unchanged"] = source_hash == hashlib.sha256(source.read_bytes()).hexdigest()
        focused, focused_rejected = character_knowledge.validate(
            report["calls"]["focused"]["raw"], SOURCE)
        broad, broad_rejected = narrative_ledger.validate(
            report["calls"]["episodes"]["raw"], SOURCE)
        report["focused_validated"] = focused
        report["focused_rejected"] = focused_rejected
        report["baseline_with_literal"] = character_knowledge.combine(
            focused, character_knowledge.explicit_perspectives(SOURCE))
        report["broad_validated"] = broad
        report["broad_rejected"] = broad_rejected
        report["combined_persisted"] = narrative_ledger.timeline(project)
        report["baseline_current"] = character_knowledge.current(project)
        report["ledger_current"] = narrative_ledger.character_view(project, "Oren")
        report["ledger_history"] = narrative_ledger.character_view(project, "Oren", historical=True)
        report["markdown_exported"] = (
            root / "project" / ".storyteller" / "memory" / "narrative-ledger.md").is_file()
    report["explicit_correction_probe"] = projection_probe()
    if replay:
        report["original_live_seconds"] = {
            name: call["seconds"] for name, call in replay["calls"].items()}
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:18986/v1/chat/completions")
    parser.add_argument("--replay", type=Path, help="Replay captured output with zero model calls")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    replay = json.loads(args.replay.read_text()) if args.replay else None
    result = compare(args.endpoint, replay)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
