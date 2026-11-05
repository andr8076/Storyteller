"""Small, deterministic profile relevance and bounded source excerpts."""


def profile_score(title, body, query, terms):
    """Favor named profiles, while allowing a stored detail to find its profile."""
    title_hits = len(query & terms(title))
    body_hits = len(query & terms(body))
    return 3 * title_hits + body_hits


def profile_excerpt(body, query, terms, limit):
    """Keep matching facts from anywhere in a long Markdown profile."""
    if len(body) <= limit:
        return body
    lines = body.splitlines()
    candidates = []
    for index, line in enumerate(lines):
        hits = len(query & terms(line))
        if hits:
            candidates.append((-hits, index))
    if not candidates:
        return body[:limit]
    candidates.sort()
    chosen = set()
    remaining = limit
    if lines and lines[0].startswith("# "):
        chosen.add(0)
        remaining -= len(lines[0]) + 1
    for _, index in candidates:
        if index in chosen or len(lines[index]) + 1 > remaining:
            continue
        chosen.add(index)
        remaining -= len(lines[index]) + 1
    if len(chosen) <= 1:
        return body[:limit]
    return "\n".join(lines[index] for index in sorted(chosen))[:limit]
