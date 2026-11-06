"""Incremental, source-backed retrieval of established historical events.

The append-only events.jsonl remains authoritative. This disposable SQLite FTS
index is rebuilt if the log is replaced or truncated and never changes source.
"""
import hashlib
import json
import re
import sqlite3
from pathlib import Path


def _open_index(path):
    if path.is_symlink():
        raise ValueError("Refusing symbolic link for event index")
    db = sqlite3.connect(path, timeout=2)
    db.execute("CREATE TABLE IF NOT EXISTS indexed (event_id TEXT PRIMARY KEY)")
    db.execute("CREATE TABLE IF NOT EXISTS position (offset INTEGER NOT NULL)")
    db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS event_search USING fts5(
        event_id UNINDEXED, entity, fact, evidence, source_id UNINDEXED,
        tokenize='porter unicode61')""")
    return db


def sync(project):
    """Index complete appended JSON lines, including records from older versions."""
    root = Path(project["folder"]) / ".storyteller" / "memory"
    log = root / "events.jsonl"
    if not log.is_file() or log.is_symlink():
        return None
    index = root / "event-search.sqlite3"
    with _open_index(index) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT offset FROM position").fetchone()
        offset = row[0] if row else 0
        with log.open("rb") as source:
            source.seek(0, 2)
            size = source.tell()
            if size < offset:
                db.execute("DELETE FROM event_search")
                db.execute("DELETE FROM indexed")
                offset = 0
            source.seek(offset)
            while True:
                line = source.readline()
                if not line or not line.endswith(b"\n"):
                    break
                offset = source.tell()
                try:
                    item = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(item, dict) or item.get("category") != "event":
                    continue
                # A Dump is timeless reference information, not a past scene.
                if str(item.get("source_exchange", "")).startswith("import:dump:"):
                    continue
                entity, fact, evidence = (item.get(key) for key in
                                          ("entity", "fact", "evidence"))
                if not all(isinstance(v, str) and v.strip() for v in
                           (entity, fact, evidence)):
                    continue
                event_id = str(item.get("event_id") or hashlib.sha256(line).hexdigest())
                added = db.execute("INSERT OR IGNORE INTO indexed VALUES (?)", (event_id,))
                if added.rowcount:
                    db.execute("INSERT INTO event_search VALUES (?,?,?,?,?)",
                               (event_id, entity, fact, evidence,
                                str(item.get("source_exchange", "unknown"))))
        db.execute("DELETE FROM position")
        db.execute("INSERT INTO position VALUES (?)", (offset,))
    return index


def relevant(project, query, terms, *, limit=1600):
    """Return a few matched past events with original evidence and source ID."""
    words = lambda text: terms(text) | set(re.findall(r"\b\d{1,2}\b", text))
    valid = words(query)
    ordered = [word for word in re.findall(r"[a-z0-9]+", query.lower())
               if word in valid]
    tokens = list(dict.fromkeys(reversed(ordered)))[:16]
    if not tokens or limit < 100:
        return ""
    index = sync(project)
    if index is None:
        return ""
    expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
    with sqlite3.connect(f"file:{index}?mode=ro", uri=True, timeout=2) as db:
        rows = db.execute("""SELECT entity, fact, evidence, source_id
            FROM event_search WHERE event_search MATCH ?
            ORDER BY bm25(event_search, 0, 4, 2) LIMIT 16""",
            (expression,)).fetchall()
    ranked = []
    query_words = set(tokens)
    for position, (entity, fact, evidence, source) in enumerate(rows):
        matched = query_words & words(entity + " " + fact + " " + evidence)
        score = 3 * len(query_words & words(entity)) + len(matched)
        ranked.append((score, position, entity, fact, evidence, source))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    lines = []
    best = ranked[0][0] if ranked else 0
    for score, _, entity, fact, evidence, source in ranked:
        # A specific event match should not drag in other scenes merely
        # because they share the same character name.
        if best >= 5 and score < best:
            continue
        entry = f"- {entity}: {fact} [source: {source}; evidence: {evidence}]"
        if len(entry) + sum(len(line) + 1 for line in lines) <= limit:
            lines.append(entry)
        if len(lines) == 4:
            break
    return "\n".join(lines)
