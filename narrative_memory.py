"""Budgeted narrative retrieval from one authoritative character history.

Broad episode extraction and ordinary conversation learning both publish
validated perspectives to that history. Its order, rather than file mtime or
which extractor ran, determines which later evidence can supersede a belief.
"""
import re

import character_knowledge
import narrative_projection


def snapshot(project):
    rows = [{"kind": "observation" if r["certainty"] == "knows" else "belief",
             "actor": r["character"], "claim": r["fact"], "topic": r["topic"],
             "certainty": r["certainty"], "source_id": r["source_id"],
             "source_order": r["source_order"], "evidence": r["evidence"],
             "scope": r["scope"], "sequence": r["sequence"],
             "aliases": r.get("aliases", [])}
            for r in character_knowledge.history(project)]
    return narrative_projection.project(rows)


def relevant(project, query, terms, limit=1700, *, temporal_query=None):
    """Select before formatting: historical questions favor earlier evidence."""
    if limit <= 0:
        return ""
    rows = snapshot(project)
    query_terms = terms(query)
    temporal = bool(re.search(r"\b(?:before|earlier|previously|initially|formerly|"
                              r"used to|at the time|what did .* believe|what did .* know)\b",
                              temporal_query if temporal_query is not None else query, re.I))
    candidates = []
    for index, row in enumerate(rows):
        if row["status"] == "historical" and not temporal:
            continue
        score = 3 * len(query_terms & terms(row["actor"] + " " +
                                             " ".join(row.get("aliases", []))))
        score += len(query_terms & terms(row["topic"] + " " + row["claim"]))
        if score:
            priority = int(temporal and row["status"] == "historical")
            candidates.append((priority, score, index, row))
    candidates.sort(key=lambda r: (-r[0], -r[1], -r[2]))
    lines, used = [], 0
    for _, _, index, row in candidates:
        label = "Earlier" if row["status"] == "historical" else "Current"
        correction = ""
        if row["superseded_by"] is not None:
            correction = f"; superseded by source: {rows[row['superseded_by']]['source_id']}"
        actor = row["actor"]
        if row.get("aliases"):
            actor += " (also known as " + ", ".join(row["aliases"]) + ")"
        line = (f"- {label}, {actor} {row['certainty'].replace('_', ' ')}: "
                f"{row['claim']} [source: {row['source_id']}; evidence: {row['evidence']}"
                f"{correction}]")
        cost = len(line) + bool(lines)
        if used + cost > limit:
            continue
        lines.append(line)
        used += cost
    return "\n".join(lines)
