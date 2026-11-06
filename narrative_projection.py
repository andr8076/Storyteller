"""Deterministic views over validated episodes; never rewrite source history.

Topic labels are search hints, not proposition identities. Reconciliation here
recognizes equivalent copular statements and explicit negation of the same
predicate. Semantic opposites, entity aliases, and implied corrections remain
unresolved rather than being guessed from shared words.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Proposition:
    subject: str
    predicate: str
    negation: str
    complete: bool

    @property
    def identity(self):
        return self.subject, self.predicate

    @property
    def equivalent(self):
        return self.subject, self.predicate, self.negation


def proposition(claim, actor=""):
    """Identify a narrow, unambiguous proposition without discarding qualifiers."""
    value = " ".join(re.findall(r"[\w]+(?:['’][\w]+)?", claim.casefold()))
    # A discovery episode can also be expressed as its observed location.
    if actor:
        value = re.sub(rf"^{re.escape(actor.casefold())} (?:found|saw) (?=the )",
                       "", value)
    match = re.fullmatch(
        r"(.+?) (?:is|are|was|were|(?:has|have|had) (?:(not|never) )?been) (.+)", value)
    complete = bool(match)
    if match:
        subject, negation, predicate = match.groups()
        negation = negation or ""
        negative = re.match(r"(not|never) (.+)", predicate)
        if negative:
            if negation:
                return None  # Multiple scopes of negation are not equivalent.
            negation, predicate = negative.groups()
    else:
        fragment = re.fullmatch(
            r"(the .+?) ((?:in|inside|under|beneath|behind|above|near|on|at|outside|within) .+)",
            value)
        if not fragment:
            return None
        subject, predicate = fragment.groups()
        negation = ""
    # Coordination, embedded clauses, and residual negation need a richer
    # interpretation. Never collapse these into a single proposition slot.
    if re.search(r"\b(?:and|or|if|unless|because|but|that|not|never|no)\b",
                 subject + " " + predicate):
        return None
    subject = re.sub(r"^the ", "", subject)
    if not subject or not predicate:
        return None
    return Proposition(subject, predicate, negation, complete)


def equivalent(left, right):
    """Same statement, preserving negation, word order, and spatial qualifiers."""
    a = proposition(left["claim"], left["actor"])
    b = proposition(right["claim"], right["actor"])
    if a and b:
        return a.equivalent == b.equivalent
    normalize = lambda s: " ".join(re.findall(r"\w+", s.casefold()))
    return normalize(left["claim"]) == normalize(right["claim"])


def prefer_complete(existing, candidate):
    a = proposition(existing["claim"], existing["actor"])
    b = proposition(candidate["claim"], candidate["actor"])
    return bool(a and b and a.equivalent == b.equivalent and
                b.complete and not a.complete)


def project(rows):
    """Return chronological perspectives with explainable supersession links.

    Indices refer to this returned view. Corrections require a later supported
    observation; a suspicion or another character's statement cannot retract
    knowledge. Equal sentence order cannot establish which claim came later.
    """
    result = []
    for row in rows:
        if row["kind"] not in {"belief", "observation"}:
            continue
        incoming = dict(row, status="current", superseded_by=None, supersedes=[])
        parsed = proposition(row["claim"], row["actor"])
        for index, earlier in enumerate(result):
            if earlier["status"] != "current" or earlier["actor"].casefold() != row["actor"].casefold():
                continue
            if (earlier["source_id"] == row["source_id"] and
                    row["source_order"] <= earlier["source_order"]):
                continue
            prior = proposition(earlier["claim"], earlier["actor"])
            reason = ""
            if (parsed and prior and parsed.complete and prior.complete and
                    parsed.identity == prior.identity and
                    bool(parsed.negation) != bool(prior.negation) and
                    row["kind"] == "observation" and row["certainty"] == "knows"):
                reason = "Later observed evidence explicitly contradicts this proposition."
            elif equivalent(earlier, row) and (
                    row["certainty"] == earlier["certainty"] or
                    (row["kind"] == "observation" and row["certainty"] == "knows")):
                reason = "Later evidence establishes the same proposition."
            if reason:
                earlier["status"] = "historical"
                earlier["superseded_by"] = len(result)
                earlier["supersession_reason"] = reason
                incoming["supersedes"].append(index)
        result.append(incoming)
    return result
