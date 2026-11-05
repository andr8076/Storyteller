#!/usr/bin/env python3
"""Explicit, resumable project imports for Storyteller.

Catch Up: analyse existing fiction as established story history.
Dump Info: catalogue supplied character and world facts as canon.
Never alter source documents; only write inside <project>/.storyteller.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import memory_worker as mem
import storyteller
import story_state
import character_knowledge

ROOT = Path(__file__).resolve().parent
EXTENSIONS = {".txt", ".md", ".markdown"}
SKIP_DIRS = {".storyteller", ".git", ".venv", "__pycache__", "node_modules"}
MAX_FILES = 1500
MAX_FILE_BYTES = 60 * 1024 * 1024
CHUNK_LIMIT = 2400
FORMAT_VERSION = "import-v2"
HALT = False


IMPORT_MEMORY_PROMPT = """You are the continuity librarian for the author's fictional universe.
You are CATALOGUING ESTABLISHED SOURCE MATERIAL, not generating a new scene.
Extract all explicitly supported, significant facts:
- people: names, identities, appearance, personality, injuries, abilities,
  relationships, roles, possessions, and locations;
- world: technology, places, rules, organizations, ships, species and systems;
- important completed events that have actually happened.
A factual reference note and an existing story chapter are equally valid
sources of canonical information. Treat them with the SAME extraction rules.
Do not infer uncertain facts from rumors, beliefs, theoretical possibilities,
questions, or plans that never occurred. Never invent missing details.
Use an entity's actual name and cite only ORIGINAL numbered source sentences.
For each fact give 1-3 sentence numbers. Each statement must be self-contained.
Use categories: appearance, injury, identity, status, relationship,
location, possession, world, technology, place, event.
Return JSON only, like:
{"changes":[{"entity":"Demi","category":"identity",
"fact":"Demi was created by the Director",
"evidence_numbers":[2],"confidence":"high"}],
"summary":"Brief synopsis of the source section.",
"knowledge_changes":[{"character":"Ivo","topic":"vault location",
"fact":"The vault is beneath the lake","certainty":"suspects",
"evidence_numbers":[3]}]}
Knowledge changes are character perspectives, not world canon. Include only
when a named character explicitly knows, suspects, believes, denies, or forgets
a proposition in the original source. A rumor must never become a world fact.
Use the same topic for later changes in certainty about the same proposition.
Being told a claim does not establish its truth; use believes unless the text
confirms it. Mark believes_wrongly only with an explicit correction.
An explicit suspicion or mistaken belief belongs in knowledge_changes even
when changes is empty; never promote it to established world canon.
Capture up to 12 important distinct facts per section.
Do not omit explicit appearance, equipment, personality or world rules.
The surrounding summaries are context only, NEVER evidence.
Output valid JSON only; no Markdown, no analysis. /no_think"""


def stamp():
    return datetime.now(timezone.utc).isoformat()


def say(message):
    print("[Import] " + message, flush=True)


def stop_handler(_signum, _frame):
    global HALT
    HALT = True
    say("Stopping after the current section; progress is saved.")


def natural_sort_key(value):
    return [int(v) if v.isdigit() else v.casefold()
            for v in re.split(r"(\d+)", str(value))]


def read_source_file(path):
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"File exceeds 60 MiB: {path}")
    if b"\x00" in data[:4096] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ValueError(f"Binary file is unsupported: {path}")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    return data.decode("utf-8-sig", errors="replace")


def scan_source(source):
    source = Path(source).expanduser().resolve()
    if not source.exists():
        raise ValueError(f"Input path not found: {source}")
    if source.is_file():
        if source.suffix.lower() not in EXTENSIONS:
            raise ValueError("Only plain text and Markdown files are supported (.txt/.md/.markdown).")
        files = [source]
    elif source.is_dir():
        files = [p for p in source.rglob("*") if p.is_file() and not p.is_symlink()
                 and p.suffix.lower() in EXTENSIONS
                 and not any(part.startswith(".") or part in SKIP_DIRS
                             for part in p.relative_to(source).parts[:-1])
                 and not p.name.startswith(".")]
        files.sort(key=lambda p: natural_sort_key(p.relative_to(source)))
        if len(files) > MAX_FILES:
            raise ValueError(f"Folder has over {MAX_FILES} text files. Choose a smaller folder.")
    else:
        raise ValueError("Input must be a regular file or a folder.")
    if not files:
        raise ValueError("No supported text or Markdown files found.")
    docs = []
    for path in files:
        text = read_source_file(path)
        if not text.strip():
            continue
        data_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunks = mem.split_scene(text, max_size=CHUNK_LIMIT)
        docs.append({"path": path, "text": text, "hash": data_hash,
                     "chunks": chunks, "characters": len(text)})
    if not docs:
        raise ValueError("The selected input contained no text.")
    return docs


def prepare(project, mode, source):
    if mode not in ("catchup", "dump"):
        raise ValueError("Expected catchup or dump")
    docs = scan_source(source)
    manifest = "\n".join(str(d["path"]) + ":" + d["hash"] for d in docs)
    jobid = hashlib.sha256((FORMAT_VERSION + "|" + project["alias"] + "|" +
                            mode + "|" + manifest).encode()).hexdigest()[:20]
    total = sum(len(d["chunks"]) for d in docs)
    return {"job_id": jobid, "docs": docs, "sections": total,
            "characters": sum(d["characters"] for d in docs),
            "file_count": len(docs)}


def status_path(project, jobid):
    return Path(project["folder"]) / ".storyteller" / "imports" / "jobs" / (jobid + ".json")


def save_status(project, jobid, status):
    target = status_path(project, jobid)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    os.replace(temporary, target)


def write_status(project, jobid, **values):
    target = status_path(project, jobid)
    status = json.loads(target.read_text()) if target.exists() else {}
    status.update(values)
    status["updated_at"] = stamp()
    save_status(project, jobid, status)
    return status


def database(project):
    path = Path(project["folder"]) / ".storyteller" / "imports" / "progress.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=25)
    conn.execute("""CREATE TABLE IF NOT EXISTS sections (
        job_id TEXT NOT NULL, section_num INTEGER NOT NULL,
        source TEXT NOT NULL, chunk_index INTEGER NOT NULL,
        status TEXT NOT NULL, facts INTEGER NOT NULL DEFAULT 0,
        rejected INTEGER NOT NULL DEFAULT 0,
        summary TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(job_id,section_num))""")
    return conn


def request_model(prompt, numbered, previous_summary="", known_entities=""):
    context = []
    if previous_summary:
        context.append("Previous section's plot summary (context only, not evidence):\n"
                       + previous_summary[:550])
    if known_entities:
        context.append("Previously identified names (context only): " + known_entities[:350])
    user_text = ("\n\n".join(context) + "\n\n" if context else "") + (
        "SOURCE SECTION (only these numbered sentences may be cited):\n" + numbered)
    data = {
        "model": mem.MODEL_NAME,
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": user_text}],
        "stream": False, "temperature": 0, "max_tokens": 1350,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False}
    }
    req = Request(mem.ENDPOINT, data=json.dumps(data).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=190) as response:
        raw = json.load(response)
    choices = raw.get("choices", [])
    reply = choices[0]["message"]["content"]
    result = json.loads(reply)
    if not isinstance(result, dict) or not isinstance(result.get("changes"), list):
        raise ValueError("Memory model returned invalid JSON changes array")
    return result


def validate_records(response, text, whole_file, mode):
    sentences = mem.split_sentences(text)
    good, rejected = [], []
    changes = response.get("changes", [])
    for item in changes[:26]:
        if not isinstance(item, dict):
            rejected.append({"reason": "non-object", "detail": str(item)[:150]})
            continue
        entity = item.get("entity", "")
        category = item.get("category", "")
        fact = item.get("fact", "")
        references = item.get("evidence_numbers")
        # Some small models omit this optional self-reported field while
        # providing valid quoted evidence. Code validation is authoritative.
        confidence = item.get("confidence", "high")
        if confidence is None:
            confidence = "high"
        if not all(isinstance(v, str) for v in (entity, category, fact)):
            rejected.append({"reason": "invalid structure", "detail": str(item)[:150]})
            continue
        entity = mem.complete_character_name(mem.canonical_name(entity.strip()), whole_file)
        fact, category = fact.strip(), category.strip().lower()
        category = {"organization":"world", "role":"status", "profession":"status",
                    "personality":"identity", "species":"identity",
                    "abilities":"status", "ability":"status", "power":"status",
                    "rule":"world", "rules":"world", "equipment":"possession",
                    "history":"event", "plot":"event", "physical":"appearance",
                     "description":"appearance", "trait":"appearance",
                     "ship":"technology", "vehicle":"technology", "spacecraft":"technology",
                     "organization":"world", "species":"identity"}.get(category,category)
        # Small models occasionally classify a named ship as a person.
        # The explicit source wording takes precedence over that classification.
        object_words = ("spacecraft", "starship", "spaceship", "ship", "vessel",
                        "reactor", "cockpit", "engine", "thruster")
        fact_words = set(re.findall(r"[a-z]+", fact.lower()))
        if entity.lower().startswith("the ") and fact_words.intersection(object_words):
            entity = entity[4:]
            if category in mem.CHARACTER_CATEGORIES:
                category = "technology"
        elif category in ("world","technology","place") and entity.lower().startswith("the "):
            entity = entity[4:]
        indexes = mem.minimal_evidence(sentences, references, fact)
        if indexes is None:
            rejected.append({"reason": "unsupported sentence references", "detail": fact[:180]})
            continue
        evidence = " ".join(sentences[i-1] for i in indexes)
        if (category not in mem.ALLOWED or not mem.slug(entity) or
            len(entity) > 80 or len(entity) < 2 or not 7 <= len(fact) <= 300 or
            not 10 <= len(evidence) <= 900 or confidence != "high"):
            rejected.append({"reason": "invalid category, length or confidence",
                             "detail": fact[:180]})
            continue
        # The model may use generic subject labels for universe rules.
        generic = category in ("world", "technology", "place", "event") and entity.lower() in (
            "universe", "world", "setting", "story", "timeline")
        if entity.casefold() not in whole_file.casefold() and not generic:
            rejected.append({"reason": "entity not found in original source",
                             "detail": f"{entity}: {fact}"[:180]})
            continue
        if not mem.confirm_anchors(fact, evidence):
            rejected.append({"reason": "detail not supported by cited sentences",
                             "detail": fact[:180]})
            continue
        if not generic and not mem.evidence_supports_claim(entity, fact, evidence):
            rejected.append({"reason": "named entity or fact unsupported by source",
                             "detail": fact[:180]})
            continue
        # Protect common role/relationship assertions: boarding a ship does
        # not itself make someone its commander or stationed crew.
        unsupported_roles = ("commander", "stationed", "married", "husband",
                             "wife", "daughter", "son", "sister", "brother",
                             "creator", "created", "founder", "founded")
        for role in unsupported_roles:
            if (re.search(r"\b" + role + r"\b", fact, re.I) and
                not re.search(r"\b" + role + r"\b", evidence, re.I)):
                rejected.append({"reason": "unsupported role or relationship",
                                 "detail": fact[:180]})
                break
        else:
            role = None
        if role is not None:
            continue
        relation = re.search(
            r"\b(captain|leader|director|head|owner) of (?:the )?([A-Za-z0-9-]+)",
            fact, re.I)
        if relation and not re.search(
            r"\b" + re.escape(relation.group(1)) +
            r"\s+of\s+(?:the\s+)?" + re.escape(relation.group(2)) + r"\b",
            evidence, re.I):
            rejected.append({"reason": "relationship inferred without source support",
                             "detail": fact[:180]})
            continue
        if mem.speculative_evidence(evidence):
            rejected.append({"reason": "speculative or uncertain source",
                             "detail": fact[:180]})
            continue
        good.append({"entity": entity, "category": category, "fact": fact,
                     "evidence": evidence, "confidence": confidence})
    unique = {}
    for fact in good:
        key = (mem.normalize(fact["entity"]), mem.normalize(fact["fact"]))
        unique.setdefault(key, fact)
    summary = response.get("summary", "")
    if not isinstance(summary, str):
        summary = ""
    return list(unique.values()), rejected, summary.strip()[:520]


def append_rejections(project, jobid, section, source, rejected):
    if not rejected:
        return
    path = Path(project["folder"]) / ".storyteller" / "imports" / "review.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for item in rejected:
            f.write(json.dumps({"job_id": jobid, "section": section,
                                "source": source, "at": stamp(), **item},
                               ensure_ascii=False) + "\n")


def safe_label(value):
    return re.sub(r"[\r\n\t]+", " ", str(value)).strip()[:110]


def persist_reference_chunks(project, jobid, docs, mode):
    """Write verbatim source excerpts so no supplied information is discarded.

    Cached references are intentionally in memory/reference-notes/; the gateway
    loads ONLY relevant chunks on future requests, not all source text at once.
    """
    memory = Path(project["folder"]) / ".storyteller" / "memory"
    target = memory / "reference-notes" / jobid
    target.mkdir(parents=True, exist_ok=True)
    index = 0
    for doc in docs:
        for i, chunk in enumerate(doc["chunks"]):
            index += 1
            destination = target / f"section-{index:06d}.md"
            if destination.exists():
                continue
            header = (f"# {mode.upper()} / {safe_label(doc['path'].name)} "
                      f"/ section {i+1}\n\n")
            temp = destination.with_suffix(".tmp")
            temp.write_text(header + chunk, encoding="utf-8")
            os.replace(temp, destination)
    say(f"Preserved {index} exact source excerpts for future memory retrieval.")


def write_catchup_summary(project, jobid, document_sections, mode, source):
    if mode != "catchup" or not document_sections:
        return
    memory = Path(project["folder"]) / ".storyteller" / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    title = safe_label(Path(source).name)
    full = ["# Catch Up — " + title, "",
            "Automatically compiled plot notes. Check for accuracy before relying on them.",
            ""]
    for doc_name, index, summary in document_sections:
        if summary:
            full.append(f"- {safe_label(doc_name)} / section {index + 1}: {summary}")
    folder = memory / "imported-summaries"
    folder.mkdir(exist_ok=True)
    report = folder / f"catchup-{jobid}.md"
    report.write_text("\n".join(full) + "\n", encoding="utf-8")

    # The always-injected summary is compact and source-labelled. Unverified model
    # summaries are clearly identified; the per-section archive remains available.
    recent = [s for _, _, s in document_sections if s]
    lead = recent[:2]
    ending = recent[-5:] if len(recent) > 2 else []
    selected = list(dict.fromkeys(lead + ending))
    teaser = "\n".join("- " + text[:300] for text in selected)
    block = (f"<!-- storyteller-catchup:{jobid}:start -->\n"
             f"## Catch Up: {title}\n"
             f"_AI-generated plot summary. Verify; detailed section notes in "
             f"imported-summaries/catchup-{jobid}.md._\n"
             f"{teaser}\n"
             f"<!-- storyteller-catchup:{jobid}:end -->")
    target = memory / "summary.md"
    existing = target.read_text(encoding="utf-8") if target.exists() else "# Story continuity\n"
    start_marker = f"<!-- storyteller-catchup:{jobid}:start -->"
    end_marker = f"<!-- storyteller-catchup:{jobid}:end -->"
    if start_marker in existing and end_marker in existing:
        i = existing.index(start_marker)
        j = existing.index(end_marker, i) + len(end_marker)
        existing = existing[:i] + block + existing[j:]
    else:
        existing = existing.rstrip() + "\n\n" + block + "\n"
    temp = target.with_suffix(".tmp")
    temp.write_text(existing, encoding="utf-8")
    os.replace(temp, target)


def append_dump_global_facts(project, jobid, facts, source):
    global_facts = [f for f in facts if f["category"] in ("world", "technology", "place")]
    if not global_facts:
        return
    memory = Path(project["folder"]) / ".storyteller" / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    target = memory / "canon.md"
    previous = target.read_text(encoding="utf-8") if target.exists() else "# Established canon\n"
    # The ID is stable; do not create duplicate global facts on retry.
    add = []
    for row in global_facts:
        tag = hashlib.sha256((jobid + row["entity"] + row["fact"]).encode()).hexdigest()[:14]
        marker = f"storyteller-dump:{tag}"
        if marker in previous:
            continue
        text = f"- {row['entity']}: {row['fact']} <!-- {marker} -->"
        add.append(text)
        previous += "\n" + text
    if add:
        if "## Catalogued reference facts" not in previous:
            previous = previous.replace(add[0], "## Catalogued reference facts\n" + add[0], 1)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(previous.rstrip() + "\n", encoding="utf-8")
        os.replace(tmp, target)


def run_import(project, mode, source, plan=None):
    global HALT
    HALT = False
    plan = plan or prepare(project, mode, source)
    jobid = plan["job_id"]
    docs = plan["docs"]
    sections = plan["sections"]
    if not mem.is_healthy():
        raise RuntimeError("CPU memory model is offline. Start Storyteller first.")
    old = status_path(project, jobid)
    state = json.loads(old.read_text()) if old.exists() else {}
    if state.get("state") == "complete":
        # Older versions completed Catch Ups without storing a continuation
        # point. Recover it once, but NEVER rewind an already advanced story.
        if mode == "catchup" and story_state.current(project) is None:
            with database(project) as db:
                summaries = db.execute(
                    "SELECT source,chunk_index,summary FROM sections "
                    "WHERE job_id=? AND status='done' ORDER BY section_num",
                    (jobid,)).fetchall()
            story_state.advance(project,
                story_state.build_catchup_state(project, jobid, docs, summaries))
            say("Reconstructed previously missing story continuation point.")
        say("This exact source is already imported. No duplicated facts.")
        return state
    say(f"{mode.upper()} / {project['name']}: {plan['file_count']} file(s), "
        f"{sections} sections, {plan['characters']:,} characters")
    with database(project) as db:
        previous = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(facts),0),COALESCE(SUM(rejected),0) "
            "FROM sections WHERE job_id=? AND status='done'", (jobid,)).fetchone()
        done, facts_total, rejected_total = previous
        write_status(project, jobid, state="running", mode=mode,
                     source=str(Path(source).resolve()), total=sections,
                     completed=done, facts=facts_total, rejected=rejected_total,
                     project_alias=project["alias"], file_count=plan["file_count"],
                     started_at=state.get("started_at", stamp()))
        section_number = 0
        previous_summary = ""
        known = []
        for doc in docs:
            for i, chunk in enumerate(doc["chunks"]):
                section_number += 1
                existing = db.execute(
                    "SELECT status,summary FROM sections WHERE job_id=? AND section_num=?",
                    (jobid, section_number)).fetchone()
                if existing and existing[0] == "done":
                    previous_summary = existing[1] or ""
                    continue
                if HALT:
                    write_status(project, jobid, state="paused")
                    return {"state": "paused", "completed": done, "total": sections}
                past = doc["chunks"][i-1][-390:] if i else ""
                # Cross-section context helps pronouns, but only current text can be cited.
                numbered = "\n".join(
                    f"[{index}] {sentence}" for index, sentence in
                    enumerate(mem.split_sentences(chunk), 1))
                say(f"Section {section_number}/{sections} / {safe_label(doc['path'].name)}")
                write_status(project, jobid, current_section=section_number,
                             current_source=str(doc["path"]), state="running")
                prompt = IMPORT_MEMORY_PROMPT
                try:
                    context = (previous_summary + "\n" + past).strip()[:850]
                    raw = request_model(prompt, numbered, context,
                                        ", ".join(dict.fromkeys(known[-14:])))
                    valid, rejected, summary = validate_records(
                        raw, chunk, doc["text"], mode)
                    model_perspectives, knowledge_rejected = character_knowledge.validate(raw, chunk)
                    perspectives = character_knowledge.combine(
                        model_perspectives, character_knowledge.explicit_perspectives(chunk))
                    source_ref = f"import:{mode}:{jobid}:section:{section_number}"
                    count = mem.apply_changes(project, source_ref, valid,
                                              timeline=(mode == "catchup"))
                    count += character_knowledge.record(project, source_ref, perspectives,
                                                        timeline=(mode == "catchup"))
                    rejected.extend({"reason": "knowledge: " + reason, "detail": ""}
                                    for reason in knowledge_rejected)
                    if mode == "dump":
                        append_dump_global_facts(project, jobid, valid, source)
                    append_rejections(project, jobid, section_number,
                                      str(doc["path"]), rejected)
                    db.execute(
                        "INSERT OR REPLACE INTO sections "
                        "(job_id,section_num,source,chunk_index,status,facts,rejected,summary) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (jobid, section_number, str(doc["path"]), i, "done",
                         count, len(rejected), summary))
                    db.commit()
                    done += 1
                    facts_total += count
                    rejected_total += len(rejected)
                    previous_summary = summary
                    known.extend(x["entity"] for x in valid)
                    write_status(project, jobid, state="running", completed=done,
                                 facts=facts_total, rejected=rejected_total)
                    say(f"  saved {count} facts, {len(rejected)} needing review")
                except Exception as exc:
                    db.rollback()
                    write_status(project, jobid, state="error",
                                 error=f"{type(exc).__name__}: {exc}"[:240],
                                 completed=done, facts=facts_total,
                                 rejected=rejected_total)
                    raise
        # Import completion archives the full original text as searchable
        # memory chunks, even where the 2B extractor missed some details.
        persist_reference_chunks(project, jobid, docs, mode)
        if mode == "catchup":
            summaries = db.execute(
                "SELECT source,chunk_index,summary FROM sections "
                "WHERE job_id=? AND status='done' ORDER BY section_num",
                (jobid,)).fetchall()
            write_catchup_summary(project, jobid, summaries, mode, source)
            # This is the ONE semantic difference between Catch Up and Dump:
            # the last imported story section becomes the continuation point.
            story_state.advance(project,
                story_state.build_catchup_state(project, jobid, docs, summaries))
            say("Continuation point advanced to the END of the imported story.")
        else:
            say("Dump Info catalogued facts; story continuation point unchanged.")
    return write_status(project, jobid, state="complete", completed=done,
                        facts=facts_total, rejected=rejected_total)


def get_project(alias):
    return next((p for p in storyteller.load_config()["projects"]
                 if p.get("alias") == alias), None)


def main():
    p = argparse.ArgumentParser(description="Storyteller Catch Up / Dump Info imports")
    sub = p.add_subparsers(dest="action", required=True)
    scan = sub.add_parser("scan")
    run = sub.add_parser("run")
    for obj in (scan, run):
        obj.add_argument("--project", required=True, help="Storyteller model alias")
        obj.add_argument("--mode", choices=("catchup", "dump"), required=True)
        obj.add_argument("--source", required=True, help="File or folder")
    args = p.parse_args()
    project = get_project(args.project)
    if not project:
        raise SystemExit("Unknown project. Choose one in Storyteller's control panel.")
    try:
        plan = prepare(project, args.mode, args.source)
        if args.action == "scan":
            state_file = status_path(project, plan["job_id"])
            state = json.loads(state_file.read_text()) if state_file.exists() else {}
            print(json.dumps({"job_id": plan["job_id"], "files": plan["file_count"],
                              "characters": plan["characters"], "sections": plan["sections"],
                              "previously_completed": state.get("completed", 0),
                              "state": state.get("state", "new")}, indent=2))
            return
        signal.signal(signal.SIGINT, stop_handler)
        signal.signal(signal.SIGTERM, stop_handler)
        result = run_import(project, args.mode, args.source, plan)
        say(f"Finished: {result.get('state')}, {result.get('completed', 0)}/"
            f"{result.get('total', plan['sections'])} sections, "
            f"{result.get('facts', 0)} saved facts, "
            f"{result.get('rejected', 0)} needing review")
    except (ValueError, OSError, RuntimeError, sqlite3.Error, json.JSONDecodeError) as exc:
        say(f"ERROR: {type(exc).__name__}: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
