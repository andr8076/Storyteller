"""Project-local history of explicit relationship states.

Relationship facts already remain in the append-only event log. This small
ledger adds a current-state view so a later, explicit change can supersede an
earlier relationship without deleting its evidence. It only accepts a narrow
set of unambiguous relationship clauses; ordinary actions such as meeting or
speaking are left in the event history instead.
"""
from contextlib import closing, contextmanager
import hashlib
import re
import sqlite3
from pathlib import Path

import character_knowledge


TEMPORAL_QUERY = re.compile(
    r"\b(?:before|earlier|previously|initially|formerly|used to|at the time|"
    r"what did .* believe|what was .* relationship)\b", re.I)

_NAME = r"[A-Z][A-Za-z0-9'’\-]*(?:\s+[A-Z][A-Za-z0-9'’\-]*){0,2}"

# Keep the name fragment case-sensitive.  Applying ``re.I`` to the whole
# expression makes lower-case grammar words such as ``no longer`` eligible to
# be swallowed into a multi-word subject (``Mara no longer``), which breaks the
# state transition key.  Only the grammar/verb portions need case folding.
_DIRECT = re.compile(
    rf"^(?P<subject>{_NAME})\s+(?P<negative>(?i:no longer\s+|not\s+|stopped\s+|does not\s+|did not\s+|never\s+)*)"
    r"(?P<verb>(?i:trusts?|trusted|loves?|loved|hates?|hated|fears?|feared|"
    r"owes?|owed|is allied with|was allied with|is enemies with|was enemies with|"
    r"is friends with|was friends with|works with|worked with|"
    r"is married to|was married to))\s+(?i:the\s+)?"
    rf"(?P<object>{_NAME})\b")
_PAIR = re.compile(
    rf"^(?P<subject>{_NAME})\s+(?i:and)\s+(?P<object>{_NAME})\s+(?i:are)\s+"
    r"(?P<negative>(?i:not\s+|no longer\s+)*)(?P<verb>(?i:married|allies|allied|enemies|siblings))\b")
_POSSESSIVE = re.compile(
    rf"^(?P<subject>{_NAME})\s+(?i:is)\s+(?P<negative>(?i:not\s+|no longer\s+)*)"
    rf"(?P<object>{_NAME})['’](?i:s)\s+"
    r"(?P<verb>(?i:sister|brother|mother|father|spouse|partner))\b")

_VERB_MAP = {
    "trust": "trust", "trusts": "trust", "trusted": "trust",
    "love": "love", "loves": "love", "loved": "love",
    "hate": "hate", "hates": "hate", "hated": "hate",
    "fear": "fear", "fears": "fear", "feared": "fear",
    "owe": "owe", "owes": "owe", "owed": "owe",
    "is allied with": "allied", "was allied with": "allied",
    "is friends with": "friends", "was friends with": "friends",
    "works with": "works_with", "worked with": "works_with",
    "is enemies with": "enemies", "was enemies with": "enemies",
    "is married to": "married", "was married to": "married",
    "married": "married", "allies": "allied", "allied": "allied",
    "enemies": "enemies", "siblings": "siblings",
    "sister": "sibling", "brother": "sibling", "mother": "parent",
    "father": "parent", "spouse": "married", "partner": "partner",
}


def _clean_name(value):
    return re.sub(r"\s+", " ", value.strip(" \t\"'“”‘’.,;:!?"))


def _key(value):
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def parse(item):
    """Parse one validated relationship fact into a stable state key."""
    if not isinstance(item, dict) or item.get("category") != "relationship":
        return None
    fact = item.get("fact")
    if not isinstance(fact, str):
        return None
    text = fact.strip().rstrip(".!?")
    # English commonly places the transition marker between a copula and its
    # relational complement ("is no longer allied with"). Normalize only those
    # narrow forms so the state parser remains literal and does not infer from
    # arbitrary prose.
    text = re.sub(r"\b(is|was)\s+(no longer|not)\s+(?=(?:allied|enemies|friends|married)\b)",
                  r"\2 \1 ", text, flags=re.I)
    match = _DIRECT.fullmatch(text) or _PAIR.fullmatch(text) or _POSSESSIVE.fullmatch(text)
    if not match:
        return None
    subject = _clean_name(match.group("subject"))
    target = _clean_name(match.group("object"))
    verb = match.group("verb").casefold()
    negative = bool(match.groupdict().get("negative"))
    relation = _VERB_MAP.get(verb)
    if not relation or not subject or not target or _key(subject) == _key(target):
        return None
    return {"subject": subject, "object": target, "relation": relation,
            "polarity": 0 if negative else 1, "fact": fact}


@contextmanager
def database(project):
    folder = Path(project["folder"]) / ".storyteller" / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "relationship-history.sqlite3"
    if path.is_symlink():
        raise ValueError("Refusing symbolic link for relationship history")
    db = sqlite3.connect(path, timeout=20)
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS relationship_states (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT UNIQUE NOT NULL, subject TEXT NOT NULL, object TEXT NOT NULL,
            relation TEXT NOT NULL, polarity INTEGER NOT NULL, fact TEXT NOT NULL,
            evidence TEXT NOT NULL, source_id TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'timeline', source_order INTEGER NOT NULL DEFAULT 0)""")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def record(project, source_id, facts, *, timeline=True):
    """Append explicit relationship states idempotently."""
    if not isinstance(source_id, str) or not source_id.strip():
        return 0
    parsed = []
    for item in facts or []:
        state = parse(item)
        if not state:
            continue
        evidence = item.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            # A relationship without its source sentence is not safe to make
            # retrievable as established canon.
            continue
        state["subject"] = character_knowledge.canonical_character(project, state["subject"])
        state["object"] = character_knowledge.canonical_character(project, state["object"])
        state["evidence"] = evidence
        state["source_order"] = (item.get("source_order", 0)
                                  if type(item.get("source_order", 0)) is int else 0)
        parsed.append(state)
    if not parsed:
        return 0
    inserted = 0
    with database(project) as db:
        db.execute("BEGIN IMMEDIATE")
        for state in parsed:
            key = "|".join((source_id, _key(state["subject"]), _key(state["object"]),
                            state["relation"], str(state["polarity"]), state["fact"].casefold()))
            identifier = hashlib.sha256(key.encode()).hexdigest()
            result = db.execute("""INSERT OR IGNORE INTO relationship_states
                (id,subject,object,relation,polarity,fact,evidence,source_id,scope,source_order)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (identifier, state["subject"], state["object"],
                state["relation"], state["polarity"], state["fact"], state["evidence"],
                source_id, "timeline" if timeline else "baseline", state["source_order"]))
            inserted += result.rowcount
    if inserted:
        export_markdown(project)
    return inserted


def history(project):
    path = Path(project["folder"]) / ".storyteller" / "memory" / "relationship-history.sqlite3"
    if not path.is_file() or path.is_symlink():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return [dict(zip(("sequence", "subject", "object", "relation", "polarity", "fact",
                          "evidence", "source_id", "scope", "source_order"), row))
                for row in db.execute("""SELECT sequence,subject,object,relation,polarity,fact,
                    evidence,source_id,scope,source_order FROM relationship_states
                    ORDER BY (scope='timeline'), sequence""")]


def _resolved_history(project):
    """Resolve aliases for a read without rewriting the source-backed rows."""
    rows = history(project)
    cache = {}
    resolved = []
    for row in rows:
        item = dict(row)
        for field in ("subject", "object"):
            name = item[field]
            if name not in cache:
                cache[name] = character_knowledge.canonical_character(project, name)
            item[field] = cache[name]
        resolved.append(item)
    return resolved


def current(project):
    selected = {}
    for row in _resolved_history(project):
        key = _relationship_key(row)
        selected[key] = row
    return list(reversed(list(selected.values())))


SYMMETRIC_RELATIONS = {"allied", "enemies", "friends", "married", "siblings",
                       "sibling", "partner"}


def _relationship_key(row):
    names = (_key(row["subject"]), _key(row["object"]))
    if row["relation"] in SYMMETRIC_RELATIONS:
        names = tuple(sorted(names))
    return names + (row["relation"],)


def relevant(project, query, terms, *, limit=1200, temporal_query=None):
    if limit <= 0:
        return ""
    rows = _resolved_history(project)
    if not rows:
        return ""
    temporal = bool(TEMPORAL_QUERY.search(temporal_query if temporal_query is not None else query))
    current_rows = current(project)
    current_ids = {row["sequence"] for row in current_rows}
    query_terms = terms(query)
    ranked = []
    for index, row in enumerate(rows):
        if not temporal and row["sequence"] not in current_ids:
            continue
        text = f"{row['subject']} {row['object']} {row['relation']} {row['fact']}"
        score = 3 * len(query_terms & terms(row["subject"] + " " + row["object"]))
        score += len(query_terms & terms(text))
        if score:
            ranked.append((int(temporal and row["sequence"] not in current_ids), score, index, row))
    ranked.sort(key=lambda value: (-value[0], -value[1], -value[2]))
    lines, used = [], 0
    for _, _, _, row in ranked:
        label = "Earlier" if row["sequence"] not in current_ids else "Current"
        line = (f"- {label} relationship: {row['fact']} [source: {row['source_id']}; "
                f"evidence: {row['evidence']}]")
        if used + len(line) + bool(lines) > limit:
            continue
        lines.append(line)
        used += len(line) + bool(lines)
    return "\n".join(lines)


def export_markdown(project):
    folder = Path(project["folder"]) / ".storyteller" / "memory"
    target = folder / "relationship-history.md"
    if target.is_symlink():
        raise ValueError("Refusing symbolic link for relationship history export")
    lines = ["# Relationship history", "", "Current explicit relationship states with source evidence.", ""]
    for row in current(project):
        lines.extend((f"## {row['subject']} ↔ {row['object']} — {row['relation']}",
                      f"- {'active' if row['polarity'] else 'ended or denied'}: {row['fact']}",
                      f"- Source: {row['source_id']}", f"- Evidence: {row['evidence']}", ""))
    temporary = target.with_suffix(".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(target)
