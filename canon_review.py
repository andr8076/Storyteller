"""Conservative conflict review for new Data Dump statements.

Existing canon is retained when the local reviewer finds a conflict or cannot
establish compatibility. Source imports and review records preserve the new
statement; nothing here silently overwrites an older author's statement.
"""
import json
from difflib import SequenceMatcher
import re
from pathlib import Path

import memory_worker as mem


def key(text):
    return ' '.join(text.casefold().split()).rstrip('.')


def existing_facts(project, entities=()):
    memory = Path(project['folder']) / '.storyteller/memory'
    path = memory / 'events.jsonl'
    rows = []
    if path.is_symlink():
        raise ValueError('Refusing a symbolic-link canon log')
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            # Corrupt canon must not silently disappear from conflict checks.
            row = json.loads(line)
            if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ('entity', 'fact')):
                raise ValueError('Invalid canonical event while checking Data Dump conflicts')
            rows.append(row)
    seen = {(key(row['entity']), key(row['fact'])) for row in rows}
    for entity in sorted(set(entities)):
        paths = [memory / group / (mem.slug(entity) + '.md') for group in ('characters', 'world')]
        paths.append(memory / 'canon.md')
        for path in paths:
            if path.is_symlink():
                raise ValueError('Refusing a symbolic-link canon profile')
            if not path.is_file():
                continue
            for line in path.read_text(encoding='utf-8').splitlines():
                text = re.sub(r'<!--.*?-->', '', line).strip()
                if not text or text.startswith('#'):
                    continue
                text = re.sub(r'^[-*]\s+', '', text)
                if path.name == 'canon.md':
                    prefix = re.match(r'^(?:the\s+)?' + re.escape(entity) + r'\s*:\s*(.*)', text, re.I)
                    if not prefix:
                        continue
                    text = prefix[1]
                identity = (key(entity), key(text))
                if identity not in seen:
                    rows.append({'entity': entity, 'fact': text, 'evidence': text,
                                 'source_profile': str(path.relative_to(memory))})
                    seen.add(identity)
    return rows


def possible_revision(entity, old, new):
    """Recognize a changed slot/negation in otherwise matching statements.

    This flags a POSSIBLE revision, not a semantic proof of contradiction.
    The small reviewer must not wave away a directly changed source clause.
    """
    def words(text):
        text = re.sub(r'^(?:the\s+)?' + re.escape(entity) + r'\b', '', text, flags=re.I).strip()
        text = re.sub(r"\bcannot\b|\bcan't\b", 'can not', text, flags=re.I)
        return re.findall(r"\w+|[+-]", text.casefold())
    left, right = words(old), words(new)
    ops = SequenceMatcher(None, left, right, autojunk=False).get_opcodes()
    changes = [op for op in ops if op[0] != 'equal']
    if len(changes) != 1:
        return False
    _, a, b, c, d = changes[0]
    if max(b - a, d - c) > 4:
        return False
    prefix = min(a, c)
    suffix = min(len(left) - b, len(right) - d)
    return ((prefix >= 1 and suffix >= 1)
            or (prefix >= 1 and left[0] in ('is', 'are', 'was', 'were')
                and max(b - a, d - c) <= 2))


def review(candidates, established, classify):
    """Pure boundary: require complete high-confidence compatibility results."""
    accepted, rejected = [], []
    known = list(established)
    for candidate in candidates:
        prior = [row for row in known if key(row['entity']) == key(candidate['entity'])]
        # An identical fact is already safe and the persistence layer dedupes it.
        if any(key(row['fact']) == key(candidate['fact']) for row in prior):
            accepted.append(candidate)
            continue
        comparisons = [
            {'reason': 'possible changed reference clause', 'prior': old}
            for old in prior if possible_revision(candidate['entity'], old['fact'], candidate['fact'])
        ]
        batches = () if comparisons else range(0, len(prior), 12)
        for offset in batches:
            batch = prior[offset:offset + 12]
            response = classify(candidate, batch)
            decisions = response.get('decisions') if isinstance(response, dict) else None
            valid = isinstance(decisions, list) and len(decisions) == len(batch)
            by_index = {}
            for decision in decisions if isinstance(decisions, list) else []:
                if (not isinstance(decision, dict)
                        or type(decision.get('index')) is not int
                        or decision['index'] not in range(len(batch))
                        or decision['index'] in by_index
                        or decision.get('relation') not in ('compatible', 'conflict', 'uncertain')
                        or decision.get('confidence') not in ('high', 'low')):
                    valid = False
                    break
                by_index[decision['index']] = decision
            if not valid or len(by_index) != len(batch):
                comparisons.append({'reason': 'incomplete conflict assessment', 'prior': batch})
                break
            for index, old in enumerate(batch):
                decision = by_index[index]
                if decision['relation'] != 'compatible' or decision['confidence'] != 'high':
                    comparisons.append({'reason': decision['relation'], 'prior': old,
                                        'confidence': decision['confidence']})
        if comparisons:
            rejected.append({'reason': 'reference conflict requires review',
                             'detail': candidate['fact'], 'candidate': candidate,
                             'comparisons': comparisons})
        else:
            accepted.append(candidate)
            known.append(candidate)
    return accepted, rejected


def apply(project, source_ref, candidates, classify):
    memory = Path(project['folder']) / '.storyteller/memory'
    # Review and commit use one snapshot. A concurrent memory writer cannot
    # insert a contradictory fact between compatibility checking and saving.
    with mem.memory_lock(memory):
        accepted, rejected = review(candidates, existing_facts(project, [row['entity'] for row in candidates]), classify)
        count = mem.apply_changes_unlocked(project, source_ref, accepted, timeline=False)
    return count, accepted, rejected
