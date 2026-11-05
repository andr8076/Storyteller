"""Evidence-backed, project-local history of character perspectives.

Inspired by Narra's append-only knowledge states; no data migration is needed.
"""
import hashlib
import os
import re
import sqlite3
from pathlib import Path

CERTAINTIES = {"knows", "suspects", "believes", "believes_wrongly", "denies", "forgotten"}
MENTAL_VERBS = (r"knows?|knew|learned|discovered|realized|recognised|recognized|"
                r"suspects?|suspected|believes?|believed|assumes?|assumed|"
                r"overheard|heard|saw|read|was told|forgot|denies?|denied")


def validate(response, text, *, limit=12):
    """Accept only explicit, named mental states tied to source sentences."""
    import memory_worker as mem
    sentences = mem.split_sentences(text)
    accepted, rejected = [], []
    for row in response.get("knowledge_changes", [])[:limit]:
        if not isinstance(row, dict):
            rejected.append("non-object knowledge suggestion")
            continue
        character, topic, fact = (row.get(k) for k in ("character", "topic", "fact"))
        certainty = row.get("certainty")
        refs = row.get("evidence_numbers")
        if (not all(isinstance(v, str) for v in (character, topic, fact, certainty))
                or certainty not in CERTAINTIES or not isinstance(refs, list)
                or not 1 <= len(refs) <= 3 or any(type(i) is not int or i < 1 or i > len(sentences) for i in refs)):
            rejected.append("invalid knowledge fields or evidence references")
            continue
        character, topic, fact = character.strip(), topic.strip(), fact.strip()
        if not (2 <= len(character) <= 80 and 3 <= len(topic) <= 100
                and 7 <= len(fact) <= 260 and mem.slug(character)):
            rejected.append("invalid knowledge length")
            continue
        evidence = " ".join(sentences[i - 1] for i in refs)
        name = re.escape(character)
        named = r"(?<!\w)" + name + r"(?!\w)"
        # The owner of a belief must be explicit; mere presence in a scene is
        # insufficient. A second sentence may establish its content.
        cue = (rf"{named}(?:\s+\w+){{0,3}}\s+(?:{MENTAL_VERBS})\b|"
               rf"\btold\s+{named}\b")
        if (not re.search(cue, evidence, re.I)
                or not mem.evidence_supports_claim(character, fact, evidence)
                or len(evidence) > 900):
            rejected.append("perspective or content not explicit in cited evidence")
            continue
        if certainty == "knows" and re.search(rf"{named}\s+(?:did not|never|could not)\s+know", evidence, re.I):
            rejected.append("negated knowledge")
            continue
        if certainty == "believes_wrongly" and not re.search(
                r"\b(wrongly|mistaken(?:ly)?|in fact|actually|false)\b", evidence, re.I):
            rejected.append("incorrect belief lacks explicit correction")
            continue
        accepted.append({"character": character, "topic": topic, "fact": fact,
                         "certainty": certainty, "evidence": evidence})
    return accepted, rejected


def database(project):
    folder = Path(project["folder"]) / ".storyteller" / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "character-knowledge.sqlite3"
    if path.is_symlink():
        raise ValueError("Refusing symbolic link for character knowledge")
    db = sqlite3.connect(path, timeout=20)
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT UNIQUE NOT NULL, character TEXT NOT NULL,
        topic TEXT NOT NULL, fact TEXT NOT NULL, certainty TEXT NOT NULL,
        evidence TEXT NOT NULL, source_id TEXT NOT NULL)""")
    return db


def record(project, source_id, entries):
    """Append states; replaying a source never duplicates its states."""
    if not entries:
        return 0
    inserted = 0
    with database(project) as db:
        for item in entries:
            key = "|".join((source_id, item["character"].casefold(),
                            item["topic"].casefold(), item["fact"].casefold(),
                            item["certainty"]))
            identifier = hashlib.sha256(key.encode()).hexdigest()
            result = db.execute("""INSERT OR IGNORE INTO knowledge
                (id,character,topic,fact,certainty,evidence,source_id)
                VALUES (?,?,?,?,?,?,?)""", (identifier, item["character"], item["topic"],
                item["fact"], item["certainty"], item["evidence"], source_id))
            inserted += result.rowcount
    if inserted:
        export_markdown(project)
    return inserted


def export_markdown(project):
    """Readable current perspectives with evidence and source identifiers."""
    folder = Path(project["folder"]) / ".storyteller" / "memory"
    target = folder / "character-knowledge.md"
    if target.is_symlink():
        raise ValueError("Refusing symbolic link for character knowledge export")
    lines = ["# Character perspectives", "", "Beliefs and suspicions below are attributed to characters; they are not world canon.", ""]
    for row in current(project):
        lines.extend((f"## {row['character']} — {row['topic']}",
                      f"- {row['certainty'].replace('_', ' ')}: {row['fact']}",
                      f"- Source: {row['source_id']}",
                      f"- Evidence: {row['evidence']}", ""))
    temp = target.with_suffix(".tmp")
    temp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temp, target)


def current(project):
    """Latest state per character/topic; older states remain queryable in SQLite."""
    path = Path(project["folder"]) / ".storyteller" / "memory" / "character-knowledge.sqlite3"
    if not path.is_file() or path.is_symlink():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        return [dict(zip(("character", "topic", "fact", "certainty", "evidence", "source_id"), row))
                for row in db.execute("""SELECT character,topic,fact,certainty,evidence,source_id
                    FROM knowledge AS k WHERE sequence = (
                      SELECT MAX(sequence) FROM knowledge AS newer
                      WHERE lower(newer.character)=lower(k.character)
                      AND lower(newer.topic)=lower(k.topic)) ORDER BY sequence DESC""")]


def relevant(project, query, terms, limit=1400):
    """Attributed perspective snippets for an outgoing writing request."""
    query_terms = terms(query)
    ranked = []
    for row in current(project):
        score = len(query_terms & terms(row["character"])) * 3
        score += len(query_terms & terms(row["topic"] + " " + row["fact"]))
        if score:
            ranked.append((score, row))
    ranked.sort(key=lambda value: -value[0])
    lines = []
    for _, row in ranked:
        line = (f"- {row['character']} {row['certainty'].replace('_', ' ')}: {row['fact']} "
                f"[source: {row['source_id']}; evidence: {row['evidence']}]")
        if len("\n".join(lines + [line])) > limit:
            continue
        lines.append(line)
    return "\n".join(lines)
