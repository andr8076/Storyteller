#!/usr/bin/env python3
"""Optional per-project prefix/suffix inserted into a Jan user message.

Unlike memory/context system instructions, these become part of the last
outgoing USER message, just as if the author had typed them in Jan. The
visible Jan conversation and the source captured for analysis are untouched.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import story_state

TEXT_LIMIT = 32768
SETTINGS = "prompt-affixes.json"
KINDS = {"prefix", "suffix"}


def folder(project):
    return story_state.location(project)


def text_file(project, kind):
    if kind not in KINDS:
        raise ValueError("Unknown affix")
    return folder(project) / (kind + ".txt")


def get_settings(project):
    path = folder(project) / SETTINGS
    if not path.is_file() or path.is_symlink():
        return {"prefix_enabled": False, "suffix_enabled": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid prompt affix settings")
    return {key: data.get(key, False) is True for key in (
        "prefix_enabled", "suffix_enabled")}


def get_text(project, kind):
    path = text_file(project, kind)
    if not path.is_file() or path.is_symlink():
        return ""
    if path.stat().st_size > TEXT_LIMIT * 4:
        raise ValueError("Prompt text file is too large")
    return path.read_text(encoding="utf-8")


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    try:
        with temp.open("w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def set_text(project, kind, value, enable=True):
    if kind not in KINDS:
        raise ValueError("Unknown affix")
    if not isinstance(value, str) or len(value.encode("utf-8")) > TEXT_LIMIT:
        raise ValueError("Prefix/suffix must be text of at most 32 KiB")
    _atomic(text_file(project, kind), value)
    if enable is not None:
        set_enabled(project, kind, bool(enable and value.strip()))


def set_enabled(project, kind, enabled):
    if kind not in KINDS:
        raise ValueError("Unknown affix")
    if enabled and not get_text(project, kind).strip():
        raise ValueError("Edit the " + kind + " before enabling it")
    settings = get_settings(project)
    settings[kind + "_enabled"] = bool(enabled)
    _atomic(folder(project) / SETTINGS,
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n")


def effective(project):
    settings = get_settings(project)
    prefix = get_text(project, "prefix") if settings["prefix_enabled"] else ""
    suffix = get_text(project, "suffix") if settings["suffix_enabled"] else ""
    return prefix, suffix


def wrap_user_message(messages, project):
    """Return a shallow copy with ONLY the final user message decorated.

    For text messages, wrap within the existing content string. For rich
    messages containing text/image parts, insert text parts in the same user
    message, preserving existing image/media blocks. Return untouched
    messages if the final message isn't a user message or both affixes are off.
    """
    prefix, suffix = effective(project)
    if not prefix and not suffix:
        return messages, False
    if not isinstance(messages, list) or not messages:
        return messages, False
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        return messages, False
    content = last.get("content")
    if isinstance(content, str):
        updated = (prefix + "\n\n" if prefix else "") + content + (
            "\n\n" + suffix if suffix else "")
    elif isinstance(content, list):
        updated = []
        if prefix:
            updated.append({"type": "text", "text": prefix + "\n\n"})
        updated.extend(content)
        if suffix:
            updated.append({"type": "text", "text": "\n\n" + suffix})
    else:
        return messages, False
    result = list(messages)
    result[-1] = {**last, "content": updated}
    return result, True
