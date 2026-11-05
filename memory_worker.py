#!/usr/bin/env python3
"""Private CPU memory extraction for Storyteller.

Writes only within each chosen project's .storyteller directory.
A structured model response is treated as untrusted input and validated
against an exact quote from the completed assistant scene.
"""
import hashlib
import fcntl
from contextlib import contextmanager
from itertools import combinations
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import story_state

HOME = Path(__file__).resolve().parent
MODEL_FILE = HOME / "models" / "qwen3.5-2b-Q4_K_M.gguf"
MEMORY_PORT = int(os.environ.get("STORYTELLER_MEMORY_PORT", "18885"))
if not 1 <= MEMORY_PORT <= 65535:
    raise ValueError("STORYTELLER_MEMORY_PORT must be between 1 and 65535")
ENDPOINT = f"http://127.0.0.1:{MEMORY_PORT}/v1/chat/completions"
MODEL_NAME = "qwen3.5-2b-Q4_K_M.gguf"
CHARACTER_CATEGORIES = {"appearance", "injury", "identity", "status",
                        "relationship", "location", "possession"}
WORLD_CATEGORIES = {"world", "technology", "place"}
ALLOWED = CHARACTER_CATEGORIES | WORLD_CATEGORIES | {"event"}

PROMPT = """You are a continuity-database fact extractor for fiction.
Read a COMPLETED scene and identify NEW, EXPLICITLY ESTABLISHED changes to story canon.
This is extraction, not creative writing. Return a JSON object with a 'changes' array.

Extract durable facts such as permanent scars, amputations, new physical features,
established relationships, permanent changes in possessions, jobs, and world rules.
You may also record an important completed event with category 'event'.
Skip generic actions, scene atmosphere, transient emotions, hypothetical plans,
questions, rumors, guesses, denials, dialogues about what might happen, and
events that have not happened in the provided scene.
Do NOT infer lasting consequences from temporary injuries without explicit evidence.

Allowed categories: appearance, injury, identity, status, relationship, location,
possession, world, technology, place, event.
For each change return:
{"entity": "person/place name", "category": "injury",
 "fact": "Permanent scar on the left knee",
 "evidence_numbers": [1],
 "confidence": "high"}
ONLY output changes supported by specific numbered sentences from the provided scene.
The evidence_numbers MUST include a sentence that explicitly NAMES the claimed person
or object AND states the supporting fact. If the source only says "she" or "they",
cite an additional numbered sentence that clearly identifies that person.
Do not attribute an unnamed stranger's species, motives, message or actions to
Demi, the Director, or another character simply because their name occurs elsewhere.
Do not turn suspicion, apparent guilt or a character's accusation into established fact.
Prefer an empty changes array over an unsupported or misattributed claim.
The evidence_numbers list must contain the numbers of the original sentences supporting
this fact, and can use two sentences if a later sentence confirms permanence.
Do not output evidence text, only numeric sentence indexes. If unsure, output [].
You are not allowed to rewrite old canon or make up names.
Examples:
Scene: 'Demi caught her left knee on metal. It left a permanent scar.'
{"changes":[{"entity":"Demi","category":"injury","fact":"Permanent scar on left knee","evidence_numbers":[1,2],"confidence":"high"}]}
Scene: 'Mara wondered whether a fall could leave a scar.'
{"changes":[]}
Scene: 'Demi smiled and walked across the bridge.'
{"changes":[]}
Return valid JSON only. /no_think"""

def iso_now():
    return datetime.now(timezone.utc).isoformat()

def log(message):
    print(f"[Memory AI] {message}", flush=True)

def slug(value):
    v = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return v[:64]

def normalize(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def canonical_name(value):
    return re.sub(r"^(?:Captain|Commander|Dr\.?|Doctor|Professor|Sergeant|Lieutenant|Admiral)\s+",
                  "", value, flags=re.IGNORECASE)

def complete_character_name(short_name, scene):
    """Match a first name to a single explicit full name in the source scene.

    This avoids creating a separate 'Lena.md' when the scene names Lena Ashford.
    Ambiguous matches are left unchanged.
    """
    if len(short_name.split()) != 1:
        return short_name
    pattern = r"\b" + re.escape(short_name) + r"[ \t]+([A-Z][a-z]{1,30})\b"
    matches = set(re.findall(pattern, scene))
    return short_name + " " + next(iter(matches)) if len(matches) == 1 else short_name


def speculative_evidence(evidence):
    """Reject uncertainty without rejecting definitive 'would never heal' phrasing."""
    text = evidence.lower()
    if re.search(r"\b(might|perhaps|hypothetical|maybe|possibly|if)\b", text):
        return True
    if re.search(r"\bcould\b(?!\s+not\b|\s+never\b)", text):
        return True
    if re.search(r"\bwould\b", text):
        if not (re.search(r"\bwould\s+(?:be\s+permanent|never|not)\b", text)
                or ("scar" in text and "permanent" in text)):
            return True
    return False


def minimal_evidence(sentences, refs, fact):
    """Reduce overlong model citations to <=3 source sentences, or reject.

    Selection is deterministic; candidates remain restricted to sentences the
    small model actually cited. The claim must retain literal anatomical and
    permanence support in the selected source text.
    """
    if not isinstance(refs, list) or len(refs) < 1 or len(refs) > 200:
        return None
    if any(type(i) is not int or i < 1 or i > len(sentences) for i in refs):
        return None
    nums = sorted(set(refs))
    if len(nums) <= 3:
        return nums
    # A small model occasionally cites dozens of sentences. Exhaustive
    # 3-sentence combinations become expensive on very long chapters;
    # shortlist the strongest citations before looking for 1-3 matching ones.
    if len(nums) > 27:
        key_terms = set(re.findall(r"[a-z]{3,}", fact.lower()))
        def importance(n):
            words = set(re.findall(r"[a-z]{3,}", sentences[n-1].lower()))
            return (len(words & key_terms), int(any(term in words for term
                    in ("permanent", "permanently", "never", "cannot",
                        "scar", "scarring"))), -len(sentences[n-1]))
        best = sorted(nums, key=importance, reverse=True)[:23]
        nums = sorted(set(best + nums[:2] + nums[-2:]))
    words = set(re.findall(r"[a-z]{3,}", fact.lower()))
    words -= {"the", "and", "from", "with", "that", "this", "just", "below",
              "corner", "extending", "resulting", "through", "above", "into"}
    def lexical_match(word, evidence):
        tokens = set(re.findall(r"[a-z]+", evidence.lower()))
        if word in tokens:
            return True
        if word == "cheek":
            return "cheekbone" in tokens
        if word == "permanent":
            return "permanently" in tokens
        if word == "scar":
            return any(t.startswith("scar") for t in tokens)
        return False
    def score(selected):
        evidence = " ".join(sentences[i - 1] for i in selected)
        if len(evidence) > 900 or speculative_evidence(evidence):
            return None
        if not confirm_anchors(fact, evidence):
            return None
        matches = sum(1 for word in words if lexical_match(word, evidence))
        # Favor evidence that explicitly supports the claim, with fewer words.
        return (matches, -len(selected), -len(evidence))
    best = None
    best_score = None
    for count in (1, 2, 3):
        for indexes in combinations(nums, count):
            candidate = score(indexes)
            if candidate is not None and (best_score is None or candidate > best_score):
                best = list(indexes)
                best_score = candidate
    return best


def is_healthy():
    try:
        with urlopen(f"http://127.0.0.1:{MEMORY_PORT}/health", timeout=1) as r:
            return r.status == 200
    except (URLError, OSError, ValueError):
        return False

def request_json(numbered_scene):
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": "Completed fictional scene (numbered sentences):\n" + numbered_scene}
        ],
        "temperature": 0,
        "max_tokens": 750,
        "stream": False,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False}
    }
    req = Request(ENDPOINT, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=150) as response:
        data = json.load(response)
    reply = data["choices"][0]["message"]["content"]
    if not isinstance(reply, str):
        raise ValueError("Memory AI returned no text")
    result = json.loads(reply)
    if not isinstance(result, dict) or not isinstance(result.get("changes"), list):
        raise ValueError("Memory AI did not return a changes array")
    return result["changes"]


def split_scene(text, max_size=4400):
    """Divide arbitrarily long prose without discarding or changing characters.

    Prefer splitting between paragraphs, then sentences, then words. Only cut
    within a word if an unusually long unbroken token makes it unavoidable.
    Each chunk is <= max_size and joining the chunks reproduces the exact input.
    """
    if max_size < 100:
        raise ValueError("max_size must be at least 100 characters")
    if len(text) <= max_size:
        return [text] if text else []
    result = []
    position = 0
    while position < len(text):
        limit = min(position + max_size, len(text))
        cut = limit
        if limit < len(text):
            window = text[position:limit]
            earliest = max(0, len(window) // 2)
            paragraph = window.rfind("\n\n", earliest)
            if paragraph >= 0:
                cut = position + paragraph + 2
            else:
                # Use sentence boundaries when there is no paragraph break.
                sentence_ends = list(re.finditer(r'[.!?][\"”’\)\]]?\s+', window))
                plausible = [match.end() for match in sentence_ends
                             if match.end() >= earliest]
                if plausible:
                    cut = position + plausible[-1]
                else:
                    whitespace = [match.end() for match in re.finditer(r'\s+', window)
                                  if match.end() >= earliest]
                    if whitespace:
                        cut = position + whitespace[-1]
        if cut <= position:
            cut = limit
        result.append(text[position:cut])
        position = cut
    assert "".join(result) == text
    return result


def split_sentences(chunk):
    # In addition to ordinary prose, handle data-dump bullet lists and
    # "Character: description" lines without requiring full stops.
    pattern = (r'(?<![0-9]\.)(?<=[.!?])\s+(?=[A-Z"“])'
               r'|\n(?=\s*(?:[-*] +|[0-9]+[.)] +|#{1,6} +|[A-Z][A-Za-z ]{1,38}:))')
    parts = re.split(pattern, chunk)
    return [part.strip() for part in parts if part.strip()]


def confirm_anchors(fact, evidence):
    """Reject mismatched left/right, finger names, and unsupported permanence claims."""
    f = fact.lower()
    e = evidence.lower()
    for anchor in ("left", "right", "index", "middle", "ring", "thumb", "pinky", "knee", "eye", "cheek", "jaw"):
        if re.search(r"\b" + anchor + r"\b", f):
            if anchor == "cheek" and "cheekbone" in e:
                continue
            if not re.search(r"\b" + anchor + r"\b", e):
                return False
    if re.search(r"\b(permanent|permanently|never|irreversible)\b", f):
        if not any(phrase in e for phrase in (
            "permanent", "never", "no hope", "cannot", "could not", "will not",
            "irreversible", "unrecoverable", "for life", "not regenerate",
            "couldn't", "can't", "irreparably")):
            return False
    return True


def evidence_supports_claim(entity, fact, evidence):
    """Only accept facts directly supported by a passage naming the entity.

    The small model sometimes attributes a quoted sentence about another
    character to Demi or the Director. A free-floating quotation cannot be
    trusted to establish someone's identity or motives.
    """
    source = evidence.casefold()
    named = entity.casefold().strip()
    if not named or not re.search(r"(?<!\w)" + re.escape(named) + r"(?!\w)", source):
        return False
    # When the named character is the OBSERVER, an injury or appearance
    # belonging to the person they watch must not be assigned to them.
    # Example: "The Director sized him up: a laser scar on his forearm."
    observer = (r"(?<!\w)" + re.escape(named) +
                r"\s+(?:sized|watched|observed|studied|looked|"
                r"examined|noticed|saw|scanned)\b")
    if (re.search(observer, source) and
            re.search(r"\b(?:him|her|them|his|their)\b", source)):
        return False
    filler = {
        "the", "and", "but", "for", "are", "was", "were", "has", "have",
        "had", "his", "her", "their", "this", "that", "from", "with",
        "into", "onto", "about", "after", "before", "being", "been",
        "who", "which", "because", "there", "where", "then", "they",
        "she", "him", "them", "one", "two", "its", "not", "can",
        "character", "person", "known", "said"
    }
    names = set(re.findall(r"[a-z]{3,}", named))
    claim_words = set(re.findall(r"[a-z]{3,}", fact.casefold())) - filler - names
    evidence_words = set(re.findall(r"[a-z]{3,}", source))
    def supported(word):
        if word in evidence_words:
            return True
        if word == "permanent" and "permanently" in evidence_words:
            return True
        if word == "scar" and any(x.startswith("scar") for x in evidence_words):
            return True
        root = word[:-3] if word.endswith("ing") else (
            word[:-2] if word.endswith("ed") else
            word[:-1] if word.endswith("s") else word)
        return len(root) >= 4 and any(x.startswith(root) for x in evidence_words)
    return not claim_words or sum(supported(word) for word in claim_words) >= max(
        1, (len(claim_words) * 3 + 4) // 5)


def extract(scene):
    validated, rejected = [], []
    if not isinstance(scene, str) or not scene.strip():
        return validated, rejected
    chunks = split_scene(scene)
    for chunk_index, raw_chunk in enumerate(chunks):
        # Reintroduce the preceding short tail for cross-boundary references.
        # Long chapters remain incremental, not a single context-window request.
        overlap = chunks[chunk_index - 1][-480:] if chunk_index else ""
        chunk = overlap + raw_chunk
        if len(chunks) > 1:
            log(f"Analyzing chapter section {chunk_index + 1}/{len(chunks)}")
        sentences = split_sentences(chunk)
        numbered = "\n".join(f"[{i}] {part}" for i, part in enumerate(sentences, 1))
        results = request_json(numbered)
        for row in results[:18]:
            if not isinstance(row, dict):
                rejected.append(("not a JSON object", str(row)[:120]))
                continue
            name = row.get("entity")
            category = row.get("category")
            fact = row.get("fact")
            evidence_numbers = row.get("evidence_numbers")
            confidence = row.get("confidence")
            if not all(isinstance(v, str) for v in (name, category, fact)):
                rejected.append(("missing fields", str(row)[:150]))
                continue
            name, category, fact = [s.strip() for s in (name, category, fact)]
            name = complete_character_name(canonical_name(name), scene)
            evidence_numbers = minimal_evidence(sentences, evidence_numbers, fact)
            if evidence_numbers is None:
                rejected.append(("no verifiable 1-3 sentence evidence", fact[:150]))
                continue
            evidence = " ".join(sentences[i-1] for i in evidence_numbers)
            if (category not in ALLOWED or len(name) < 2 or len(name) > 80
                or not slug(name) or len(fact) < 8 or len(fact) > 260
                or len(evidence) < 12 or len(evidence) > 900
                or confidence != "high"):
                rejected.append(("invalid category/length/confidence", fact[:150]))
                continue
            if not confirm_anchors(fact, evidence):
                rejected.append(("fact missing anatomical/permanence anchors", fact[:150]))
                continue
            if not evidence_supports_claim(name, fact, evidence):
                rejected.append(("named entity or factual detail unsupported by cited evidence",
                                 fact[:150]))
                continue
            if name.lower() not in scene.lower():
                rejected.append(("entity not in scene", fact[:150]))
                continue
            if speculative_evidence(evidence):
                rejected.append(("potentially speculative evidence", evidence[:150]))
                continue
            validated.append({"entity": name, "category": category,
                              "fact": fact, "evidence": evidence, "confidence": confidence})
    # Deduplicate across overlapping mentions in the same response.
    unique = {}
    for item in validated:
        key = (normalize(item["entity"]), normalize(item["fact"]))
        unique.setdefault(key, item)
    return list(unique.values()), rejected


def archive_generated_scene(project, exchange_id, scene):
    """Keep the actual written scene, even when fact extraction rejects it.

    Reference excerpts are retrievable by the Jan gateway. They are source
    material, not unverified AI-generated factual summaries.
    """
    root = (Path(project["folder"]) / ".storyteller" / "memory" /
            "reference-notes" / ("generated-" + exchange_id[:20]))
    root.mkdir(parents=True, exist_ok=True)
    for index, chunk in enumerate(split_scene(scene, max_size=2500), 1):
        path = root / f"section-{index:06d}.md"
        if path.exists():
            continue
        temp = path.with_suffix(".tmp")
        temp.write_text(
            "# Completed fictional scene / section " + str(index) + "\n\n" +
            chunk, encoding="utf-8")
        os.replace(temp, path)


@contextmanager
def memory_lock(folder):
    """Serialize read/modify/write operations across gateway/import processes."""
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.memory-write.lock').open('a+b') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def append_event(memory, project_alias, exchange_id, item):
    target = memory / "events.jsonl"
    stored = []
    if target.exists():
        for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
            try: stored.append(json.loads(line))
            except json.JSONDecodeError: continue
    event_id = hashlib.sha256((exchange_id + "|" + item["entity"] +
                               "|" + item["fact"]).encode()).hexdigest()[:20]
    if any(row.get("event_id") == event_id for row in stored):
        return False
    if exchange_id.startswith("import:dump:") and any(
            str(row.get("source_exchange", "")).startswith("import:dump:")
            and normalize(row.get("entity", "")) == normalize(item["entity"])
            and normalize(row.get("fact", "")) == normalize(item["fact"])
            for row in stored):
        return False
    entry = {"event_id": event_id, "source_exchange": exchange_id,
             "project": project_alias, "utc": iso_now(), **item}
    with open(target, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return True


def add_fact_to_profile(memory, exchange_id, item):
    category = item["category"]
    if category == "event":
        return None
    group = "characters" if category in CHARACTER_CATEGORIES else "world"
    name = item["entity"]
    base = memory / group
    base.mkdir(exist_ok=True)
    path = base / (slug(name) + ".md")
    # Restrict updates to project-owned regular files only, never symlinks.
    if path.is_symlink():
        raise ValueError("Refusing to modify symbolic link " + str(path))
    if path.exists() and not path.is_file():
        raise ValueError("Expected a regular Markdown profile")
    previous = path.read_text(encoding="utf-8") if path.exists() else "# " + name + "\n"
    marker = hashlib.sha256((exchange_id + "|" + name + "|" + item["fact"]).encode()).hexdigest()[:16]
    if f"storyteller:{marker}" in previous:
        return None
    # Identical canonical fact should not be added over and over.
    existing_auto_facts = re.findall(r"^\- (.*?)\s*<!-- storyteller:", previous, re.M)
    if normalize(item["fact"]) in {normalize(f) for f in existing_auto_facts}:
        return None
    if "## Automatically recorded facts" not in previous:
        previous = previous.rstrip() + "\n\n## Automatically recorded facts\n"
    new_text = previous.rstrip() + (
        f"\n- {item['fact']} <!-- storyteller:{marker} -->\n")
    temp = path.with_suffix(".tmp")
    temp.write_text(new_text, encoding="utf-8")
    os.replace(temp, path)
    return str(path.relative_to(memory))


def refresh_recent_events(memory, limit=25):
    events = memory / "events.jsonl"
    if not events.exists():
        return
    recents = []
    for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
        try: recents.append(json.loads(line))
        except json.JSONDecodeError: continue
    # Dump Info is timeless reference material, NOT a story scene.
    # Keep it in profiles, canon, and event history but not the narrative
    # recent-events feed that could imply a temporal progression.
    recents = [row for row in recents if not str(
        row.get("source_exchange", "")).startswith("import:dump:")]
    recents = recents[-limit:]
    content = "# Recent confirmed story changes (latest facts last)\n\n"
    for ev in recents:
        content += f"- {ev['entity']}: {ev['fact']}\n"
    tmp = memory / "recent-events.tmp"
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, memory / "recent-events.md")


def apply_changes(project, exchange_id, valid, *, timeline=True):
    memory = Path(project["folder"]) / ".storyteller" / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    with memory_lock(memory):
        return apply_changes_unlocked(project, exchange_id, valid, timeline=timeline)


def apply_changes_unlocked(project, exchange_id, valid, *, timeline=True):
    """Caller must hold memory_lock; used for atomic Data Dump review/commit."""
    memory = Path(project["folder"]) / ".storyteller" / "memory"
    changed = []
    for item in valid:
        if append_event(memory, project["alias"], exchange_id, item):
            changed.append(item)
        saved_path = add_fact_to_profile(memory, exchange_id, item)
        if saved_path:
            log(f"{project['alias']}: updated {saved_path}: {item['fact']}")
    if changed and timeline:
        refresh_recent_events(memory)
    return len(changed)


class Worker:
    def __init__(self, projects):
        self.projects = projects
        self.stop_event = threading.Event()
        self.thread = None
        self.server = None
        self.server_log = None

    def start(self):
        if not MODEL_FILE.is_file():
            log("Model not found. Memory analysis disabled: " + str(MODEL_FILE))
            return False
        if is_healthy():
            # Refuse to hijack an existing service whose owner we do not know.
            log(f"Port {MEMORY_PORT} already occupied. Stop the other model server first.")
            return False
        cmd = ["nice", "-n", "10", "/usr/bin/llama-server", "-m", str(MODEL_FILE),
               "--host", "127.0.0.1", "--port", str(MEMORY_PORT),
               "--ctx-size", "8192", "--n-gpu-layers", "0", "--threads", "4",
               "--threads-batch", "4", "--no-webui"]
        self.server_log = open(HOME / "memory-model.log", "a", encoding="utf-8")
        self.server = subprocess.Popen(cmd, stdout=self.server_log, stderr=subprocess.STDOUT)
        log("Starting Qwen3.5-2B on CPU (4 threads, GPU offloading disabled)...")
        for i in range(150):
            if is_healthy():
                log("CPU model ready. Automatic memory analysis enabled.")
                self.thread = threading.Thread(target=self.run, name="storyteller-memory", daemon=True)
                self.thread.start()
                return True
            if self.server.poll() is not None:
                break
            time.sleep(0.2)
        log("CPU model failed to start. Check memory-model.log")
        self.stop()
        return False

    def run(self):
        while not self.stop_event.is_set():
            # Projects are reloaded between scans: updates made in the TUI take
            # effect without restarting this CPU model.
            projects = self.projects() if callable(self.projects) else self.projects
            for project in projects:
                if self.stop_event.is_set():
                    break
                try:
                    self.process_project(project)
                except Exception as exc:
                    log(f"{project['alias']}: processing error: {type(exc).__name__}: {str(exc)[:160]}")
            self.stop_event.wait(2)

    def process_project(self, project):
        base = Path(project["folder"]) / ".storyteller"
        queue = base / "pending-exchanges.jsonl"
        if not queue.exists():
            return
        database = base / "processing.sqlite3"
        with sqlite3.connect(database, timeout=20) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS processed
                         (id TEXT PRIMARY KEY, status TEXT NOT NULL,
                          changes INTEGER DEFAULT 0, reason TEXT, processed_at TEXT)""")
            # Never discard unprocessed entries because of an old checkpoint.
            for line in queue.read_text(encoding="utf-8", errors="replace").splitlines():
                if self.stop_event.is_set():
                    break
                try: exchange = json.loads(line)
                except ValueError: continue
                if not isinstance(exchange, dict):
                    continue
                if exchange.get("project") != project["alias"]:
                    continue
                scene = exchange.get("assistant_response", "")
                if not isinstance(scene, str) or not scene:
                    continue
                identifier = hashlib.sha256(line.encode()).hexdigest()
                prior = db.execute("SELECT status FROM processed WHERE id=?",
                                   (identifier,)).fetchone()
                # Previously skipped scenes may have been misclassified by an
                # older version. Recheck ONLY skipped rows using the current
                # classifier; already processed canon is never duplicated.
                if prior and prior[0] != "skipped":
                    continue
                user_prompt = exchange.get("last_user_message", "")
                if not isinstance(user_prompt, str):
                    user_prompt = ""
                if not story_state.is_narrative_exchange(user_prompt, scene):
                    if not prior:
                        db.execute("INSERT INTO processed VALUES (?, ?, ?, ?, ?)",
                                   (identifier, "skipped", 0, "short or non-narrative response", iso_now()))
                        db.commit()
                    continue
                if prior:
                    db.execute("DELETE FROM processed WHERE id=?", (identifier,))
                    db.commit()
                    log(f"{project['alias']}: recovering previously skipped narrative scene")
                log(f"{project['alias']}: examining completed scene ({len(scene)} characters)")
                try:
                    # Archive the exact scene first: even imperfect 2B analysis
                    # cannot cause the written source material to be lost.
                    archive_generated_scene(project, identifier, scene)
                    changes, rejects = extract(scene)
                    count = apply_changes(project, identifier, changes)
                    if rejects:
                        # A human can inspect untrusted or uncertain suggestions.
                        with open(base / "review.jsonl", "a", encoding="utf-8") as f:
                            for reason, detail in rejects:
                                f.write(json.dumps({"source": identifier, "reason": reason,
                                                    "detail": detail}) + "\n")
                    db.execute("INSERT INTO processed VALUES (?, ?, ?, ?, ?)",
                               (identifier, "done", count, str(len(rejects)) + " rejected", iso_now()))
                    db.commit()
                    log(f"{project['alias']}: {count} fact(s) saved; {len(rejects)} for review")
                except Exception as exc:
                    db.execute("INSERT OR REPLACE INTO processed VALUES (?, ?, ?, ?, ?)",
                               (identifier, "error", 0, str(exc)[:250], iso_now()))
                    db.commit()
                    log(f"{project['alias']}: extraction failed: {type(exc).__name__}: {str(exc)[:120]}")

    def stop(self):
        self.stop_event.set()
        if self.server and self.server.poll() is None:
            self.server.terminate()
            try: self.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=3)
        if self.server_log:
            self.server_log.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", help="Read scene from the given UTF-8 file and print validated extraction")
    args = parser.parse_args()
    if args.scene:
        scene = Path(args.scene).read_text(encoding="utf-8")
        changes, rejected = extract(scene)
        print(json.dumps({"changes": changes, "rejected": rejected}, indent=2, ensure_ascii=False))

