"""Which files does a shell command write? A small, conservative tokenizer shared by the orchestrator's write
guard and by the observation log (files an agent produced with cp, tar -C, redirects…).

It respects quotes and heredoc bodies, splits on ; && || | & ( ), follows `cd` within the command, and knows the
usual writers. It is best effort: it reports what a command visibly writes, not everything a program could do.
"""
from __future__ import annotations

import posixpath
import re
import shlex

SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", ";;", "|&", "\n"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
PREFIXES = {"sudo", "command", "nohup", "time", "env", "exec", "builtin"}
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][\w-]*)\1")


def _strip_heredocs(cmd: str) -> str:
    """Remove heredoc bodies (their text is data, not shell syntax)."""
    lines, out, i = cmd.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        delims = [m.group(2) for m in _HEREDOC.finditer(line)]
        i += 1
        for d in delims:
            while i < len(lines) and lines[i].strip() != d:
                i += 1
            i += 1  # skip the terminator line
    return "\n".join(out)


def _tokens(cmd: str) -> list[str]:
    text = _strip_heredocs(cmd).replace("\n", " ; ")
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    lex.commenters = "#"
    return list(lex)


def _commands(tokens: list[str]):
    cur: list[str] = []
    for t in tokens:
        if t in SEPARATORS:
            if cur:
                yield cur
            cur = []
        else:
            cur.append(t)
    if cur:
        yield cur


def _args(words: list[str]) -> list[str]:
    return [w for w in words if not w.startswith("-")]


def write_targets(cmd: str) -> list[tuple[str, str]]:
    """[(path, cwd)] the command writes; cwd is the directory a relative path is relative to ("." = start dir).
    A path "?" means: an in-place edit whose files could not be named."""
    out: list[tuple[str, str]] = []
    cwd = "."
    for words in _commands(_tokens(cmd)):
        # redirections anywhere in the command
        clean: list[str] = []
        i = 0
        while i < len(words):
            w = words[i]
            if w in REDIRECTS or (w == ">&" and i + 1 < len(words) and not words[i + 1].isdigit()):
                if i + 1 < len(words):
                    out.append((words[i + 1], cwd))
                i += 2
                continue
            if w in ("<", "<<", "<<<", "<&", ">&", "<>"):
                i += 2
                continue
            clean.append(w)
            i += 1
        # drop "2" left over from 2>file, VAR=value assignments and wrapper commands
        while clean and (re.fullmatch(r"[A-Za-z_]\w*=.*", clean[0]) or clean[0] in PREFIXES):
            clean = clean[1:]
        if not clean:
            continue
        verb, rest = posixpath.basename(clean[0]), clean[1:]
        rest = [w for w in rest if not re.fullmatch(r"\d", w)]
        args = _args(rest)
        if verb == "cd":
            target = args[0] if args else "~"
            cwd = target if target.startswith("/") else posixpath.normpath(posixpath.join(cwd, target))
        elif verb == "tee":
            out += [(a, cwd) for a in args]
        elif verb in ("touch", "mkdir", "rm", "rmdir", "truncate", "shred", "unlink"):
            out += [(a, cwd) for a in args]
        elif verb in ("cp", "mv", "ln", "install", "rsync", "scp"):
            t_opt = next((rest[j + 1] for j, w in enumerate(rest[:-1]) if w in ("-t", "--target-directory")), None)
            if t_opt:
                out.append((t_opt, cwd))
            elif len(args) >= 2:
                out.append((args[-1], cwd))
        elif verb == "dd":
            out += [(w[3:], cwd) for w in rest if w.startswith("of=")]
        elif verb in ("sed", "gsed", "perl"):
            short = [w for w in rest if w.startswith("-") and not w.startswith("--")]
            inplace = any(w == "--in-place" or w.startswith("--in-place=") for w in rest) or \
                any(re.fullmatch(r"-[a-zA-Z]*i\S*", w) for w in short)  # -i, -i.bak, -ni, -pi (perl)
            if inplace:
                files = args[1:] if verb != "perl" else [a for a in args if not a.startswith(("s/", "y/"))][1:]
                out += [(f, cwd) for f in files] or [("?", cwd)]
        elif verb == "tar":
            flags = "".join(w for w in rest[:1] if not w.startswith("-")) + "".join(w for w in rest if w.startswith("-") and not w.startswith("--"))
            extracting = "x" in flags or "--extract" in rest or "--get" in rest
            dest = next((rest[j + 1] for j, w in enumerate(rest[:-1]) if w in ("-C", "--directory")), None) or \
                next((w.split("=", 1)[1] for w in rest if w.startswith("--directory=")), None)
            if extracting:
                out.append((dest or ".", cwd))
            elif "c" in flags:
                f = next((rest[j + 1] for j, w in enumerate(rest[:-1]) if w in ("-f", "--file")), None) or \
                    next((rest[j + 1] for j, w in enumerate(rest[:-1]) if re.fullmatch(r"-?[a-zA-Z]*f", w) and "c" in w), None)
                if f:
                    out.append((f, cwd))
        elif verb == "unzip":
            dest = next((rest[j + 1] for j, w in enumerate(rest[:-1]) if w == "-d"), None)
            out.append((dest or ".", cwd))
        elif verb == "git" and rest[:1] == ["clone"]:
            pos = _args(rest[1:])
            if pos:
                out.append((pos[1] if len(pos) > 1 else re.sub(r"\.git$", "", posixpath.basename(pos[0].rstrip("/"))), cwd))
        elif verb in ("curl", "wget"):
            for j, w in enumerate(rest[:-1]):
                if w in ("-o", "--output", "-O", "--output-document") and not (verb == "curl" and w == "-O"):
                    out.append((rest[j + 1], cwd))
                elif verb == "wget" and w in ("-P", "--directory-prefix"):
                    out.append((rest[j + 1], cwd))
    return out
