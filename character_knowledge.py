"""Evidence-backed, project-local history of character perspectives.

Inspired by Narra's append-only knowledge states; no data migration is needed.
"""
import hashlib
from contextlib import contextmanager, closing
import os
import re
import sqlite3
from pathlib import Path

CERTAINTIES = {"knows", "suspects", "believes", "believes_wrongly", "denies", "forgotten"}
UNNAMED_SUBJECTS = {"he", "she", "they", "him", "her", "them", "his", "hers",
                    "their", "theirs", "it", "its", "someone", "nobody"}
TOPIC_FILLER = {"the", "a", "an", "of", "in", "on", "at", "where", "what", "is", "was"}
MENTAL_VERBS = (r"knows?|knew|learned|discovered|realized|recognised|recognized|"
                r"suspects?|suspected|believes?|believed|assumes?|assumed|"
                r"overheard|heard|saw|read|was told|forgot|denies?|denied")
STATE_VERBS = {
    "knows": r"knows?|knew|learned|discovered|realized|recognised|recognized|saw|read|found",
    "suspects": r"suspects?|suspected",
    "believes": r"believes?|believed|assumes?|assumed|was told",
    "believes_wrongly": r"believes?|believed|assumes?|assumed",
    "denies": r"denies?|denied",
    "forgotten": r"forgot|forgotten",
}
PROPOSITION_FILLER = {"the", "a", "an", "is", "was", "are", "were", "has", "had",
                      "that", "this", "it", "its", "in", "on", "at", "of", "to",
                      "from", "with", "by", "and", "but", "as", "there"}


def _content_supported(fact, source):
    """Require every stated detail, not merely a majority of shared words."""
    source_words = set(re.findall(r"[a-z0-9]+", source.casefold()))
    claim_words = [word for word in re.findall(r"[a-z0-9]+", fact.casefold())
                   if word not in PROPOSITION_FILLER]
    def covered(word):
        if word in source_words:
            return True
        stem = word[:-3] if word.endswith("ing") else (
            word[:-2] if word.endswith("ed") else word[:-1] if word.endswith("s") else word)
        return len(stem) >= 4 and any(other.startswith(stem) for other in source_words)
    return bool(claim_words) and all(covered(word) for word in claim_words)


def _grounded_claim(character, certainty, fact, sentence):
    """Tie a proposed belief to the character's clause or one clear quotation."""
    name = r"(?<!\w)" + re.escape(character) + r"(?!\w)"
    cue = re.search(rf"{name}(?:\s+\w+){{0,3}}\s+(?:{STATE_VERBS[certainty]})\b",
                    sentence, re.I)
    if not cue:
        # Witnessing an explicit action can establish knowledge of that
        # action, even without the narrator saying "Mira knew". A vague
        # "Mira watched" does not supply the proposed event's details.
        if certainty == "knows":
            witnessed = re.search(
                rf"{name}\s+(?:watched|witnessed|observed|saw)\s+"
                r"([A-Z][a-z]+\s+[^.!?;]+)", sentence)
            if witnessed and not re.search(
                    r"\b(?:did not|could not|couldn't|without)\s+(?:see|seeing|observe|observing)\b",
                    sentence, re.I):
                return _content_supported(fact, witnessed.group(1))
        return False
    following = re.split(r"\b(?:but\s+in\s+fact|in\s+fact|but\s+actually|actually|but)\b|;",
                         sentence[cue.end():], maxsplit=1, flags=re.I)[0]
    if _content_supported(fact, following):
        return bool(re.search(r"\b(?:not|never|no)\b", fact, re.I)) == bool(
            re.search(r"\b(?:not|never|no)\b", following, re.I))
    # "She told Joss, 'The map burned.' Joss believed her." The named
    # mental clause is anaphoric, but a single quoted statement to that
    # listener can still ground the belief. Competing quotes stay ambiguous.
    if certainty not in {"believes", "believes_wrongly"} or not re.match(
            r"\s+(?:her|him|them|it)\b", following, re.I):
        return False
    preceding = sentence[:cue.start()]
    addressed = re.search(rf"\btold\s+{name}\b|\bsaid\s+to\s+{name}\b", preceding, re.I)
    unnamed_listener = (re.search(r"\btold\s+(?:him|her|them)\b", preceding, re.I)
                        and not (set(re.findall(r"\b[A-Z][a-z]+\b", preceding)) -
                                 {"She", "He", "They", "The", "A", "An"}))
    if not (addressed or unnamed_listener):
        return False
    quotations = re.findall(r"['\"“]([^'\"”]{7,260})['\"”]", preceding)
    return len(quotations) == 1 and _content_supported(fact, quotations[0])


def _adjacent_pronoun_clause(character, certainty, previous, sentence):
    """Bind an immediately following pronoun only to one named prior subject."""
    if certainty != "knows":
        return None
    other_names = set(re.findall(r"\b[A-Z][a-z]+\b", previous)) - {
        character, "She", "He", "They", "The", "A", "An", "Later", "Then", "At"}
    if other_names or not re.search(rf"(?<!\w){re.escape(character)}\s+[a-z]+\b", previous):
        return None
    if re.search(rf"(?<!\w){re.escape(character)}\b[^.!?]*\b(?:guard|man|woman|boy|girl|person|stranger)\b",
                 previous, re.I):
        return None
    match = re.match(r"\s*(?:(?:Later|Then|Soon|Afterward),?\s+)?(he|she|they)\s+"
                     r"(found|saw|learned|discovered|realized|recognized|knew)\b", sentence, re.I)
    if not match:
        return None
    return sentence[:match.start(1)] + character + sentence[match.end(1):]


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
                and 7 <= len(fact) <= 260 and mem.slug(character)
                and character.casefold() not in UNNAMED_SUBJECTS):
            rejected.append("invalid knowledge length")
            continue
        if (re.match(r"^(?:he|she|it|they|him|her|them|his|their)\b", fact, re.I)
                or mem.perspective_statement(fact)):
            rejected.append("knowledge fact must be a self-contained proposition")
            continue
        cited = [sentences[i - 1] for i in refs]
        evidence = " ".join(cited)
        grounded = any(_grounded_claim(character, certainty, fact, sentence)
                       for sentence in cited)
        if not grounded and len(refs) == 1 and refs[0] > 1:
            prior = sentences[refs[0] - 2]
            resolved = _adjacent_pronoun_clause(character, certainty, prior, cited[0])
            if resolved and mem.evidence_supports_claim(character, fact, resolved,
                                                        observer_guard=False):
                grounded = True
                evidence = prior + " " + cited[0]
        if not grounded or len(evidence) > 900:
            rejected.append("perspective or content not explicit in cited evidence")
            continue
        if certainty == "knows" and re.search(rf"(?<!\w){re.escape(character)}\s+(?:did not|never|could not)\s+know", evidence, re.I):
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
                         "certainty": certainty, "evidence": evidence,
                         "source_order": max(refs)})
    accepted.sort(key=lambda item: item["source_order"])
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
    observation = re.compile(
        r"\b([A-Z][a-z]+)\s+(?:saw|found)\s+(the|a|an)\s+"
        r"([a-z][\w-]*(?:\s+[a-z][\w-]*){0,2})\s+"
        r"(behind|beneath|under|inside|in|at)\s+([^,.!?;]+)", re.I)
    for number, sentence in enumerate(mem.split_sentences(text), 1):
        seen = observation.search(sentence)
        if seen:
            character, article, object_name, relation, location = seen.groups()
            location = re.split(r"\s+and\s+(?:knew|realized|recognized|learned)\b",
                                location, maxsplit=1, flags=re.I)[0].strip()
            if 2 <= len(location) <= 100:
                rows.append({"character": character,
                             "topic": object_name.split()[-1].lower() + " location",
                             "fact": f"{article.capitalize()} {object_name} was {relation} {location}",
                             "certainty": "knows", "evidence_numbers": [number]})
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
        # In "the brass key was in the attic", the key is the subject;
        # "brass" alone creates a second topic beside "key location".
        noun_phrase = re.match(
            r"(?:the|a|an)\s+([a-z][\w-]*(?:\s+[a-z][\w-]*){0,3})\s+"
            r"(?:is|was|are|were|has|had)\b", proposition, re.I)
        if noun_phrase:
            anchor = noun_phrase.group(1).split()[-1].lower()
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
    """Prefer literal extraction only for the same state in the same sentence."""
    selected = list(explicit_entries)
    explicit_keys = {(row["character"].casefold(), row["topic"].casefold(),
                      row["source_order"]) for row in selected}
    for row in model_entries:
        key = (row["character"].casefold(), row["topic"].casefold(),
               row["source_order"])
        if key not in explicit_keys and not any(
                row["character"].casefold() == other["character"].casefold()
                and row["source_order"] == other["source_order"]
                and row["certainty"] == other["certainty"]
                and _claim_words(row["fact"]) == _claim_words(other["fact"])
                for other in selected):
            selected.append(row)
    selected.sort(key=lambda item: item.get("source_order", 0))
    return selected


def _claim_words(fact):
    return tuple("be" if word in {"is", "was", "are", "were"} else word
                 for word in re.findall(r"[a-z0-9]+", fact.casefold()))


@contextmanager
def database(project):
    folder = Path(project["folder"]) / ".storyteller" / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "character-knowledge.sqlite3"
    if path.is_symlink():
        raise ValueError("Refusing symbolic link for character knowledge")
    db = sqlite3.connect(path, timeout=20)
    try:
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
        if "source_order" not in columns:
            db.execute("ALTER TABLE knowledge ADD COLUMN source_order INTEGER NOT NULL DEFAULT 0")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


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
                (id,character,topic,fact,certainty,evidence,source_id,scope,source_order)
                VALUES (?,?,?,?,?,?,?,?,?)""", (identifier, item["character"], topic,
                item["fact"], item["certainty"], item["evidence"], source_id,
                "timeline" if timeline else "baseline", item.get("source_order", 0)))
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
    words = lambda text: (set(re.findall(r"[\w]+", re.sub(
        r"(?<=\w)['’]s\b", "", text.casefold()))) - TOPIC_FILLER)
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
        elif ("location" in incoming and "location" in existing
              and _subject_head(item["fact"]) == _subject_head(fact)
              and _subject_head(fact)):
            matches.append(topic)
        elif (_subject_head(item["fact"]) and
              _subject_head(item["fact"]) == _subject_head(fact) and
              (incoming <= words(_subject_head(fact)) or
               existing <= words(_subject_head(fact))) and
              incoming & existing):
            # A model's bare "the star map" topic can describe an earlier
            # belief about the same object's fate or location. Never merge
            # two differently qualified topics on the object alone.
            matches.append(topic)
        elif (incoming & existing and
              re.sub(r"\W+", " ", fact.casefold()).strip() ==
              re.sub(r"\W+", " ", item["fact"].casefold()).strip()):
            matches.append(topic)
    return matches[0] if len(matches) == 1 else item["topic"]


def _subject_head(fact):
    match = re.match(r"(?:the|a|an)\s+([a-z][\w-]*(?:\s+[a-z][\w-]*){0,3})\s+"
                     r"(?:is|was|are|were|has|had|burned|broke|fell|vanished)\b", fact, re.I)
    return match.group(1).casefold() if match else ""


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
    if project.get("narrative_memory", False):
        import narrative_memory
        return [{"character": row["actor"], "topic": row["topic"], "fact": row["claim"],
                 "certainty": row["certainty"], "evidence": row["evidence"],
                 "source_id": row["source_id"]}
                for row in narrative_memory.snapshot(project) if row["status"] == "current"]
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


def relevant(project, query, terms, limit=1400, *, temporal_query=None):
    """Attributed perspective snippets for an outgoing writing request."""
    if project.get("narrative_memory", False):
        import narrative_memory
        return narrative_memory.relevant(project, query, terms, limit=limit,
                                         temporal_query=temporal_query)
    query_terms = terms(query)
    temporal = bool(re.search(r"\b(?:before|earlier|previously|initially|formerly|"
                              r"used to|at the time|what did .* believe|what did .* know)\b",
                              temporal_query if temporal_query is not None else query, re.I))
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
        earlier = []
        if temporal:
            for past in _earlier_states(project, row):
                history = (f"- Earlier, {row['character']} {past['certainty'].replace('_', ' ')}: "
                           f"{past['fact']} [source: {past['source_id']}; "
                           f"evidence: {past['evidence']}]")
                if len("\n".join(lines + earlier + [history, line])) <= limit:
                    earlier.append(history)
        lines.extend(earlier + [line])
    return "\n".join(lines)


def history(project):
    """One read-only SQLite snapshot, shared by imports and conversation learning.

    Baseline reference knowledge precedes narrative updates even when a Data
    Dump was imported later. Old databases are read without modifying them.
    """
    path = Path(project["folder"]) / ".storyteller" / "memory" / "character-knowledge.sqlite3"
    if not path.is_file() or path.is_symlink():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge)")}
        scope = "scope" if "scope" in columns else "'timeline'"
        order = "CASE WHEN source_order>0 THEN source_order ELSE sequence END" if "source_order" in columns else "sequence"
        fields = ("sequence", "character", "topic", "fact", "certainty", "evidence", "source_id",
                  "scope", "source_order")
        return [dict(zip(fields, row)) for row in db.execute(
            f"SELECT sequence,character,topic,fact,certainty,evidence,source_id,{scope},{order} "
            f"FROM knowledge ORDER BY ({scope}='timeline'),sequence")]


def _earlier_states(project, current_row):
    path = Path(project["folder"]) / ".storyteller" / "memory" / "character-knowledge.sqlite3"
    if not path.is_file() or path.is_symlink():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge)")}
        timeline = "AND scope='timeline'" if "scope" in columns else ""
        rows = db.execute(f"""SELECT fact,certainty,evidence,source_id FROM knowledge
            WHERE lower(character)=lower(?) AND lower(topic)=lower(?) {timeline}
            ORDER BY sequence DESC LIMIT 4""",
            (current_row["character"], current_row["topic"])).fetchall()
    history = [dict(zip(("fact", "certainty", "evidence", "source_id"), values))
               for values in reversed(rows)]
    return [row for row in history if not (
        row["source_id"] == current_row["source_id"] and
        row["fact"] == current_row["fact"] and
        row["certainty"] == current_row["certainty"])][-3:]
