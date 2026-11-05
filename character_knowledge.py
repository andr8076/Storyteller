"""Evidence-backed, project-local history of character perspectives.

Inspired by Narra's append-only knowledge states; no data migration is needed.
"""
import hashlib
import os
import re
import sqlite3
from pathlib import Path

CERTAINTIES = {"knows", "suspects", "believes", "believes_wrongly", "denies", "forgotten"}
TOPIC_FILLER = {"the", "a", "an", "of", "in", "on", "at", "where", "what", "is", "was"}
MENTAL_VERBS = (r"knows?|knew|learned|discovered|realized|recognised|recognized|"
                r"suspects?|suspected|believes?|believed|assumes?|assumed|"
                r"overheard|heard|saw|read|was told|forgot|denies?|denied")


def validate(response, text, *, limit=12):
    """Accept only explicit, named mental states tied to source sentences."""
    import memory_worker as mem
    sentences = mem.split_sentences(text)
    accepted, rejected = [], []
    rows = response.get("knowledge_changes", [])
    if not isinstance(rows, list):
        return [], ["knowledge_changes must be an array"]
    for row in rows[:limit]:
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
        if (re.match(r"^(?:he|she|it|they|him|her|them|his|their)\b", fact, re.I)
                or mem.perspective_statement(fact)):
            rejected.append("knowledge fact must be a self-contained proposition")
            continue
        evidence = " ".join(sentences[i - 1] for i in refs)
        name = re.escape(character)
        named = r"(?<!\w)" + name + r"(?!\w)"
        # The owner of a belief must be explicit; mere presence in a scene is
        # insufficient. A second sentence may establish its content.
        cue = (rf"{named}(?:\s+\w+){{0,3}}\s+(?:{MENTAL_VERBS})\b|"
               rf"\btold\s+{named}\b")
        if (not re.search(cue, evidence, re.I)
                or not mem.evidence_supports_claim(character, fact, evidence,
                                                   observer_guard=False)
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
        if certainty == "believes_wrongly":
            belief_clause = re.split(r"\b(?:but\s+in\s+fact|in\s+fact|but\s+actually|actually)\b",
                                     evidence, maxsplit=1, flags=re.I)[0]
            if not mem.evidence_supports_claim(character, fact, belief_clause,
                                               observer_guard=False):
                rejected.append("belief content taken from the correction rather than the character")
                continue
        accepted.append({"character": character, "topic": topic, "fact": fact,
                         "certainty": certainty, "evidence": evidence})
    return accepted, rejected


def explicit_perspectives(text):
    """Recover simple named mental states when the small model omits them.

    This deliberately handles only a single explicit declarative clause. More
    complex attributions are left to the model and evidence validator.
    """
    import memory_worker as mem
    rows = []
    pattern = re.compile(
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+"
        r"(?:(wrongly|mistakenly)\s+)?"
        r"(suspected|suspects|discovered|learned|realized|recognised|recognized|"
        r"knew|knows|believed|believes)\s+(?:that\s+)?(.+)$")
    for number, sentence in enumerate(mem.split_sentences(text), 1):
        match = pattern.search(sentence.strip())
        if not match:
            continue
        character, wrong, verb, proposition = match.groups()
        proposition = re.split(r"\s*,?\s*but\b|\s*;",
                               proposition, maxsplit=1, flags=re.I)[0].strip(" .!?\"'")
        if not 7 <= len(proposition) <= 260:
            continue
        if re.match(r"^(?:he|she|it|they|him|her|them|his|their)\b", proposition, re.I):
            continue
        fact = proposition[0].upper() + proposition[1:]
        subject = re.match(r"(?:the|a|an)\s+([A-Za-z][\w-]*)|([A-Za-z][\w-]*)", proposition, re.I)
        if not subject:
            continue
        anchor = (subject.group(1) or subject.group(2)).lower()
        kind = ("source" if re.search(r"\b(?:came\s+from|originated\s+from)\b", proposition, re.I) else
                "location" if re.search(r"\b(beneath|under|inside|behind|located|hidden|in|at)\b",
                                        proposition, re.I) else "state")
        certainty = ("suspects" if verb.startswith("suspect") else
                     "believes_wrongly" if wrong or re.search(r"\b(?:in fact|actually)\b", sentence, re.I) else
                     "believes" if verb.startswith("believ") else "knows")
        rows.append({"character": character, "topic": f"{anchor} {kind}",
                     "fact": fact, "certainty": certainty, "evidence_numbers": [number]})
    return validate({"knowledge_changes": rows}, text)[0]


def combine(model_entries, explicit_entries):
    """Prefer literal clause extraction for the same character and topic."""
    selected = list(explicit_entries)
    explicit_keys = {(row["character"].casefold(), row["topic"].casefold()) for row in selected}
    for row in model_entries:
        key = (row["character"].casefold(), row["topic"].casefold())
        if key not in explicit_keys:
            selected.append(row)
    return selected


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
        evidence TEXT NOT NULL, source_id TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT 'timeline')""")
    # Additive compatibility for an experimental ledger already created.
    columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge)")}
    if "scope" not in columns:
        db.execute("ALTER TABLE knowledge ADD COLUMN scope TEXT NOT NULL DEFAULT 'timeline'")
    return db


def record(project, source_id, entries, *, timeline=True):
    """Append states; replaying a source never duplicates its states."""
    if not entries:
        return 0
    inserted = 0
    with database(project) as db:
        db.execute("BEGIN IMMEDIATE")
        for item in entries:
            topic = reconcile_topic(db, item)
            key = "|".join((source_id, item["character"].casefold(),
                            topic.casefold(), item["fact"].casefold(),
                            item["certainty"]))
            identifier = hashlib.sha256(key.encode()).hexdigest()
            result = db.execute("""INSERT OR IGNORE INTO knowledge
                (id,character,topic,fact,certainty,evidence,source_id,scope)
                VALUES (?,?,?,?,?,?,?,?)""", (identifier, item["character"], topic,
                item["fact"], item["certainty"], item["evidence"], source_id,
                "timeline" if timeline else "baseline"))
            inserted += result.rowcount
    if inserted:
        export_markdown(project)
    return inserted


def reconcile_topic(db, item):
    """Link obvious topic variants; ambiguous matches remain separate.

    LoreGraph uses lexical blocking followed by a semantic judge. This first
    stage deliberately accepts only near-identical topic words, or identical
    proposition text with a shared topic anchor; no model judgment is guessed.
    """
    words = lambda text: set(re.findall(r"[\w]+", text.casefold())) - TOPIC_FILLER
    incoming = words(item["topic"])
    candidates = db.execute("""SELECT topic,fact FROM knowledge AS k
        WHERE lower(character)=lower(?) AND sequence=(
            SELECT MAX(sequence) FROM knowledge AS newer
            WHERE lower(newer.character)=lower(k.character)
            AND lower(newer.topic)=lower(k.topic)) ORDER BY sequence DESC""",
        (item["character"],)).fetchall()
    matches = []
    for topic, fact in candidates:
        existing = words(topic)
        if incoming and incoming == existing:
            matches.append(topic)
        elif (incoming & existing and
              re.sub(r"\W+", " ", fact.casefold()).strip() ==
              re.sub(r"\W+", " ", item["fact"].casefold()).strip()):
            matches.append(topic)
    return matches[0] if len(matches) == 1 else item["topic"]


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
        columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge)")}
        order = (("(newer.scope='timeline') DESC, newer.sequence DESC")
                 if "scope" in columns else "newer.sequence DESC")
        return [dict(zip(("character", "topic", "fact", "certainty", "evidence", "source_id"), row))
                for row in db.execute(f"""SELECT character,topic,fact,certainty,evidence,source_id
                    FROM knowledge AS k WHERE sequence = (
                      SELECT sequence FROM knowledge AS newer
                      WHERE lower(newer.character)=lower(k.character)
                      AND lower(newer.topic)=lower(k.topic)
                      ORDER BY {order} LIMIT 1)
                    ORDER BY sequence DESC""")]


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
