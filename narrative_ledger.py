"""Experimental evidence-first event ledger; existing memory remains authoritative.

Every record has a source span and an epistemic kind. A character's statement
or belief is never silently promoted into world canon. This module can run in
an isolated project without migrating or rewriting existing user memory.
"""
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import character_knowledge
import memory_worker as mem

KINDS = {"action", "state", "assertion", "belief", "observation"}
SPEECH = r"(?:said|told|claimed|warned|whispered|reported)"
EXTRACTION_PROMPT = """Extract source-backed narrative episodes from fiction.
Return JSON with an events array in source order. Each event has kind
(action, state, assertion, belief, observation), actor, claim, topic,
recipient (for assertion), certainty (for belief/observation), and
evidence_numbers (1-3 original sentence numbers). Use empty strings for
unused fields. An assertion records what a named speaker told a named listener;
it does NOT establish that the statement is true. A belief belongs to the
named believer, never automatically to the world. An observation records
something a named character directly witnessed or found. Actions and states
are narrator-established, not merely said or imagined. Keep claims explicit,
self-contained, and close to the exact source wording. Cite the sentence with
the speaker or observer and the actual content. Do not infer knowledge from
presence or from another character knowing something. Return valid JSON only.
/no_think"""


def propose(text, endpoint):
    """One local inference call for a reversible comparison, never cloud I/O."""
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "http" or parsed.hostname not in
            {"127.0.0.1", "localhost", "::1"} or not parsed.port or
            parsed.path != "/v1/chat/completions" or parsed.query or parsed.fragment or
            parsed.username or parsed.password):
        raise ValueError("Narrative ledger requires a local chat endpoint")
    numbered = "\n".join(f"[{i}] {sentence}" for i, sentence in
                         enumerate(mem.split_sentences(text), 1))
    payload = {"model": "local", "messages": [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": "SOURCE:\n" + numbered}],
        "stream": False, "temperature": 0, "max_tokens": 1350,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False}}
    request = Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=120) as response:
        output = json.load(response)
    proposed = json.loads(output["choices"][0]["message"]["content"])
    if not isinstance(proposed, dict) or not isinstance(proposed.get("events"), list):
        raise ValueError("Narrative model returned no events array")
    return proposed["events"]


def _source_span(text, sentence, from_offset=0):
    start = text.find(sentence, from_offset)
    if start < 0:
        raise ValueError("Numbered sentence not present in source")
    return start, start + len(sentence)


def _explicit_action(actor, claim, cited):
    """An actor's verb must occur in narration, not merely inside a quote."""
    proposed = re.search(rf"(?<!\w){re.escape(actor)}\s+(?:\w+ly\s+)?([a-z]+)\b",
                         claim, re.I)
    if not proposed:
        return False
    for sentence in cited:
        narrative = re.sub(r"['\"“][^'\"”]*['\"”]", "", sentence)
        source = re.search(rf"(?<!\w){re.escape(actor)}\s+(?:\w+ly\s+)?([a-z]+)\b",
                           narrative, re.I)
        if source and character_knowledge._content_supported(proposed.group(1),
                                                             source.group(1)):
            return True
    return False


def validate(proposals, text):
    """Validate model proposals against original numbered source sentences."""
    sentences = mem.split_sentences(text)
    offsets, cursor = [], 0
    for sentence in sentences:
        start, end = _source_span(text, sentence, cursor)
        offsets.append((start, end))
        cursor = end
    accepted, rejected = [], []
    if not isinstance(proposals, list):
        return [], ["events must be an array"]
    for item in proposals[:40]:
        if not isinstance(item, dict):
            rejected.append("event is not an object")
            continue
        kind, actor, claim = (item.get(k) for k in ("kind", "actor", "claim"))
        refs = item.get("evidence_numbers")
        recipient = item.get("recipient", "")
        topic = item.get("topic", "")
        certainty = item.get("certainty", "")
        if (kind not in KINDS or not all(isinstance(v, str) for v in
                (actor, claim, recipient, topic, certainty)) or
                not isinstance(refs, list) or not 1 <= len(refs) <= 3 or
                any(type(i) is not int or i < 1 or i > len(sentences) for i in refs)):
            rejected.append("invalid event fields or citations")
            continue
        actor, claim, recipient, topic = (v.strip() for v in
                                          (actor, claim, recipient, topic))
        if (not 2 <= len(actor) <= 80 or not 5 <= len(claim) <= 260 or
                actor.casefold() in character_knowledge.UNNAMED_SUBJECTS or
                (recipient and recipient.casefold() in character_knowledge.UNNAMED_SUBJECTS)):
            rejected.append("unnamed or invalid participant or claim")
            continue
        cited = [sentences[i-1] for i in refs]
        evidence = " ".join(cited)
        if kind in {"belief", "observation"}:
            proposed = {"character": actor, "topic": topic, "fact": claim,
                        "certainty": certainty or "knows", "evidence_numbers": refs}
            if kind == "belief" and proposed["certainty"] == "knows":
                rejected.append("knowledge observation must use observation kind")
                continue
            valid, errors = character_knowledge.validate(
                {"knowledge_changes": [proposed]}, text)
            if errors:
                rejected.append("perspective: " + errors[0])
                continue
            evidence = valid[0]["evidence"]
        elif kind == "assertion":
            if not recipient or not any(
                    re.search(rf"(?<!\w){re.escape(actor)}\b[^.!?]{{0,65}}\b{SPEECH}\b",
                              sentence, re.I) and
                    re.search(rf"(?<!\w){re.escape(recipient)}\b", sentence, re.I) and
                    any(character_knowledge._content_supported(claim, quoted)
                        for quoted in re.findall(r"['\"“]([^'\"”]{5,260})['\"”]", sentence))
                    for sentence in cited):
                rejected.append("assertion lacks named speaker, listener, or matching quote")
                continue
        else:
            if (not any(re.search(rf"(?<!\w){re.escape(actor)}\b", s, re.I) for s in cited)
                    or not character_knowledge._content_supported(claim, evidence)
                    or (kind == "action" and not _explicit_action(actor, claim, cited))
                    or (kind == "action" and re.search(r"\b(?:said|told|believed|suspected)\b",
                                                       claim, re.I))):
                rejected.append("world event not explicitly supported")
                continue
        start = min(offsets[i-1][0] for i in refs)
        end = max(offsets[i-1][1] for i in refs)
        accepted.append({"kind": kind, "actor": actor, "recipient": recipient,
                         "claim": claim, "topic": topic, "certainty": certainty,
                         "source_order": max(refs), "start": start, "end": end,
                         "evidence": evidence})
    accepted.sort(key=lambda row: row["source_order"])
    return accepted, rejected


@contextmanager
def database(project):
    folder = Path(project["folder"]) / ".storyteller" / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "narrative-ledger.sqlite3"
    if path.is_symlink():
        raise ValueError("Refusing symbolic link for narrative ledger")
    db = sqlite3.connect(path)
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS episodes (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
            source_id TEXT NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL,
            recipient TEXT NOT NULL, claim TEXT NOT NULL, topic TEXT NOT NULL,
            certainty TEXT NOT NULL, source_order INTEGER NOT NULL,
            start INTEGER NOT NULL, end INTEGER NOT NULL, evidence TEXT NOT NULL)""")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def record(project, source_id, entries):
    """Append idempotently; a retry never rewrites prior evidence."""
    count = 0
    with database(project) as db:
        db.execute("BEGIN IMMEDIATE")
        for row in entries:
            key = json.dumps([source_id, row["kind"], row["actor"], row["recipient"],
                              row["claim"], row["source_order"]], ensure_ascii=False)
            identifier = hashlib.sha256(key.encode()).hexdigest()
            result = db.execute("""INSERT OR IGNORE INTO episodes
                (id,source_id,kind,actor,recipient,claim,topic,certainty,
                 source_order,start,end,evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (identifier, source_id, row["kind"], row["actor"], row["recipient"],
                 row["claim"], row["topic"], row["certainty"], row["source_order"],
                 row["start"], row["end"], row["evidence"]))
            count += result.rowcount
    return count


def timeline(project):
    with database(project) as db:
        columns = ("source_id", "kind", "actor", "recipient", "claim", "topic",
                   "certainty", "source_order", "start", "end", "evidence")
        return [dict(zip(columns, row)) for row in db.execute(
            "SELECT " + ",".join(columns) + " FROM episodes ORDER BY sequence")]


def character_view(project, actor, *, historical=False):
    """Project current attributed knowledge, keeping prior states on demand."""
    rows = [r for r in timeline(project) if r["actor"].casefold() == actor.casefold()
            and r["kind"] in {"belief", "observation"}]
    if historical:
        return rows
    latest = {}
    for row in rows:
        latest[row["topic"].casefold()] = row
    return list(latest.values())
