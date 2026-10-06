"""Incremental replay: keep the hook's Tree between events instead of re-reading the whole log.

The cache stores the Tree (without raw events) plus how far into events.jsonl it has read.
Each hook loads it, folds in only the new complete lines, and saves it back. Any doubt
(missing, unreadable, other code version, log truncated or replaced) falls back to a full
replay, so the cache can only make things faster, never different.
"""
from __future__ import annotations

import json
import os
import pickle
from pathlib import Path

from conductor.tree import Tree

VERSION = 2  # bump when Tree's internal state changes shape
HEAD_BYTES = 256  # fingerprint of the log's start, to notice a replaced or rewritten log


def _paths(state: Path) -> tuple[Path, Path]:
    return state / "events.jsonl", state / "state" / "tree.cache"


def _head(log: Path) -> bytes:
    try:
        with log.open("rb") as f:
            return f.read(HEAD_BYTES)
    except OSError:
        return b""


def _read_from(log: Path, offset: int) -> tuple[list[dict], int]:
    """Complete lines after `offset`; a half-written last line is left for the next reader."""
    try:
        with log.open("rb") as f:
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return [], offset
    end = chunk.rfind(b"\n") + 1
    events = []
    for line in chunk[:end].splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events, offset + end


def load(state: Path) -> tuple[Tree, int]:
    log, cache = _paths(state)
    tree, offset = None, 0
    try:
        with cache.open("rb") as f:
            blob = pickle.load(f)
        size = log.stat().st_size
        if blob.get("version") == VERSION and blob["offset"] <= size and blob["head"] == _head(log)[:len(blob["head"])]:
            tree, offset = blob["tree"], blob["offset"]
    except Exception:  # missing, corrupt or stale cache: rebuild
        tree, offset = None, 0
    if tree is None:
        tree = Tree(keep_events=False)
    new, offset = _read_from(log, offset)
    tree.apply(new)
    return tree, offset


def save(state: Path, tree: Tree, offset: int) -> None:
    log, cache = _paths(state)
    cache.parent.mkdir(exist_ok=True)
    tmp = cache.with_name(f"tree.cache.{os.getpid()}.tmp")
    try:
        with tmp.open("wb") as f:
            pickle.dump({"version": VERSION, "offset": offset, "head": _head(log), "tree": tree}, f,
                        protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, cache)  # atomic: concurrent hooks each leave a consistent snapshot
    except OSError:
        tmp.unlink(missing_ok=True)
