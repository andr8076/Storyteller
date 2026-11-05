"""Conservative source-statement grounding for explicit reference imports."""
import re


def source_fact(text):
    text = re.sub(r'^\s*(?:[-*]\s+|\d+[.)]\s+)', '', text)
    return ' '.join(text.split())


def statement(text):
    # Only formatting and the final full stop are optional. Qualifiers,
    # negation, numbers, and all source words remain part of the claim.
    return source_fact(text).rstrip('.')


def heading_name(line):
    match = re.match(r'^\s*(#{1,6})\s+(.+?)\s*#*\s*$', line)
    if not match:
        return None
    title = re.sub(r'^\d+(?:\.\d+)*[.)]?\s+', '', match[2]).strip()
    return len(match[1]), title


def ground(entity, fact, sentences, indexes, *, generic=False):
    """Return original evidence only for a complete quoted source statement.

    A heading can supply the subject of a bullet, but only while that heading
    is in scope. A sibling heading ends that scope. No model-generated summary
    or previous section is eligible as evidence.
    """
    subject = re.compile(r'^(?:the\s+)?' + re.escape(entity) + r'(?!\w)', re.I)
    wanted = statement(fact)
    if wanted.endswith("?"):
        return None
    stack = []
    for number, source in enumerate(sentences, 1):
        for line in source.splitlines():
            heading = heading_name(line)
            if heading:
                level, title = heading
                stack = [item for item in stack if item[0] < level]
                stack.append((level, title, line.strip()))
        if (number not in indexes or statement(source) != wanted
                or source.lstrip().startswith('#')):
            continue
        if generic or subject.search(source_fact(source)):
            return source
        # Exact entity headings, not substring matches, provide ownership.
        # Case and the conventional leading article are only presentation.
        canonical = lambda value: re.sub(r'^the\s+', '', value.strip(), flags=re.I).casefold()
        for _, title, line in reversed(stack):
            if canonical(title) == canonical(entity):
                return line + '\n' + source
    return None
