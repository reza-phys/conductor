"""Shared plumbing: project paths, config, and the append-only event log.

Stdlib only, Python 3.9+, so hooks start fast with the system interpreter.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as _dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

STATE_DIR = ".conductor"

DEFAULT_CONFIG: dict[str, Any] = {
    "program": None,  # defaults to the project directory name
    "dispatch": {
        "enforce_envelope": True,
        # Built-in read-only helpers may be dispatched without an envelope (still logged, never gated).
        "exempt_types": ["Explore", "Plan", "claude-code-guide", "statusline-setup"],
        "auditor_types": ["conductor:auditor", "auditor"],
        "read_only_types": ["conductor:auditor", "conductor:analyst"],
        "max_depth": 3,  # mirror of CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH
        "force_foreground_nested": True,  # sub-agents wait for their children (nested agents default to background)
        # Model per agent type, applied at dispatch, e.g. {"conductor:auditor": "haiku"}. Empty = inherit.
        "models": {},
    },
    "orchestrator": {
        "allow_project_edits": False,  # main thread may only write under .conductor/
        "inject_protocol": True,  # SessionStart adds the orchestrator brief to context (soft mode)
    },
    "provenance": {"commit_trailers": True},
    "gate": {
        "enabled": True,
        "require_report": True,
        "require_claims": True,  # a "done" report must assert at least one claim
        "check_evidence": True,  # cited files/commands/urls must appear in the agent's own observations
        "require_verdicts": True,  # every final claim needs an independent auditor verdict
        "max_blocks": 3,  # then allow, mark task `unverified`, open a review item
        "allow_review_skip": True,  # honour review="skip" on a task envelope (claims and evidence are still checked)
    },
    "render": {"enabled": True, "log_limit": 300, "log_visible": 8, "max_sessions": 30, "task_log_limit": 300},
    "ledger": {"store_prompts": True, "prompt_clip": 4000},  # dispatch prompt text in events.jsonl (local only)
    # Opt-in: publish the dashboard as a claude.ai Artifact (one fixed URL per project). Uploads ledger content.
    "artifact": {"enabled": False, "refresh": "task_done", "include_prompts": False, "include_commands": True,
                 "redact_paths": False},
}


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def sha256_file(path: str | Path, limit: int = 50_000_000) -> str | None:
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size > limit:
            return None
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()


def project_dir(payload: dict | None = None) -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    if payload and payload.get("cwd"):
        return Path(payload["cwd"])
    return Path.cwd()


def find_state_dir(start: Path) -> Path | None:
    """Nearest .conductor/ at or above `start`; None means Conductor is not initialised here."""
    for d in [start, *start.parents]:
        if (d / STATE_DIR).is_dir():
            return d / STATE_DIR
    return None


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(state: Path) -> dict:
    cfg = DEFAULT_CONFIG
    try:
        cfg = _merge(cfg, json.loads((state / "config.json").read_text()))
    except (OSError, ValueError):
        pass
    if not cfg.get("program"):
        cfg = {**cfg, "program": state.parent.name}
    return cfg


@contextlib.contextmanager
def locked(path: Path, mode: str = "a") -> Iterator[Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield f
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def append_event(state: Path, event: dict) -> dict:
    event = {"ts": utcnow(), **event}
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    with locked(state / "events.jsonl") as f:
        f.write(line + "\n")
    return event


def read_events(state: Path) -> list[dict]:
    p = state / "events.jsonl"
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # a torn line never breaks the reader
    return out


def clip(s: Any, n: int) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 1] + "…"
