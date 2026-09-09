"""Deterministic shell safety policy for ULTRON (cross-platform).

This module only classifies commands; it never executes them and never grants
approval. The caller must still perform server-side approval and sandbox
checks immediately before execution.

HARD SECURITY INVARIANT
-----------------------
A command classified as ``blocked`` can NEVER be made executable by approval.
Both shell execution boundaries (``tools.ToolRegistry.execute("terminal", ...)``
and ``app.tools.shell.ShellExecutor.execute``) evaluate this policy immediately
before subprocess creation and reject blocked commands regardless of any
approval state. Approval and policy are independent gates.

Canonicalization / robust parsing (pure string analysis, no execution):
  1. whitespace normalization
  2. splitting into segments on shell operators (``&&``, ``||``, ``;``, ``|``,
     newlines)
  3. per segment: privilege-prefix stripping (sudo/doas), leading environment
     assignment stripping, bounded recursion into quoted command strings
     (``bash -c "rm -rf /"`` style wrappers)
  4. token-level structural analysis for ``rm`` / ``chmod`` / ``chown``:
     recursive flags are detected in any position, and destructive targets
     (root, home, system directories, bare globs, parent escapes) are blocked
  5. regex-level hard blocks for the remaining destructive families:
     mkfs/wipefs/blkdiscard, dd to raw devices, redirects to block devices,
     fork bombs, shutdown/reboot family, ``xargs rm``, ``find -delete/-exec rm``,
     ``kill -1``/``killall5``, and the Windows set (format/diskpart/reg/sc/
     netsh/del /s/rmdir /s/Remove-Item -Recurse/wevtutil/vssadmin/...).

Known conservative over-blocks (deliberate, safety first): ``echo rm -rf /``
inside a quoted string, ``find <anywhere> -delete`` and harmless ``kill -1
<pid>`` (SIGHUP) are refused. An AI-assistant terminal should err on the
side of refusal; legitimate equivalents (``rm -rf ./build``) still pass.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ShellDecision:
    allowed: bool
    risk: str
    reason: str
    normalized: str


# --------------------------------------------------------------------------
# Regex-level hard blocks (Windows + generic Unix destructive families).
# These are matched against the whole (normalized, case-insensitive) command.
# --------------------------------------------------------------------------
_BLOCKED = (
    # --- Windows destructive set (kept from Level 52) ---
    r"\bformat\s+[a-z]:",
    r"\bdiskpart\b",
    r"\bshutdown(?:\.exe)?\b",
    r"\brestart-computer\b",
    r"\bstop-computer\b",
    r"\bremove-item\b[^\n]*\s-recurse\b",
    r"\bdel\b[^\n]*\s/(?:s|q)\b",
    r"\brmdir\b[^\n]*\s/(?:s|q)\b",
    r"\brd\s+/s\b",
    r"\breg\s+(?:delete|add)\b",
    r"\bsc(?:\.exe)?\s+(?:delete|stop|config)\b",
    r"\bnetsh\b",
    r"\bnet\s+(?:user|localgroup|stop|start)\b",
    r"\bwevtutil\b",
    r"\b(?:powershell|pwsh)\b[^\n]*\b-enc(?:odedcommand)?\b",
    r"\bvssadmin\s+delete\s+shadows\b",
    # --- Unix power state (destructive, approval cannot authorize) ---
    r"\b(?:reboot|halt|poweroff)\b",
    r"\binit\s+[06]\b",
    r"\bsystemctl\s+(?:reboot|poweroff|halt)\b",
    # --- filesystem / device destruction ---
    r"\bmkfs(?:\.\w+)?\b",
    r"\bmkswap\b",
    r"\bwipefs\b",
    r"\bblkdiscard\b",
    r"\bsfdisk\b",
    r"\bparted\b[^\n|;&]*\b(?:mklabel|mkfs|resizepart|\brm\b)\b",
    # dd writing to a raw device (of=/dev/null is harmless and stays allowed)
    r"\bdd\b[^\n|;&]*\bof=/dev/(?!null\b)",
    # raw redirect to a block device
    r">{1,2}\s*/dev/(?:sd|hd|nvme|mmcblk|vd|xvd|disk|loop|sg|md)",
    # pipe-to-rm footguns
    r"\bxargs\s+(?:-\S+\s+)*rm\b",
    r"\bfind\b[^\n|;&]*\s-delete\b",
    r"\bfind\b[^\n|;&]*\s-exec\s+rm\b",
    # process-table destruction
    r"\bkill(?:\s+-\w+)*\s+-1\b",
    r"\bkillall5\b",
    # fork bombs: classic `:(){ :|:& };:` and named variants `f(){ f|f& };f`
    r":\s*\(\s*\)\s*\{[^}]*\|[^}]*&[^}]*\}\s*;\s*:",
    r"\b(\w+)\s*\(\s*\)\s*\{[^}]*\b\1\b[^}]*\|[^}]*&[^}]*\}\s*;\s*\1\b",
)

_HIGH_RISK = (
    r"\b(?:curl|wget|bitsadmin|certutil)\b",
    r"\b(?:invoke-webrequest|invoke-restmethod)\b",
    r"\b(?:set-itemproperty|new-itemproperty)\b",
    r"\b(?:start-process|invoke-expression|iex)\b",
    r"\b(?:git\s+(?:push|reset|clean))\b",
)

# --------------------------------------------------------------------------
# Structural analysis helpers (rm / chmod / chown token parsing).
# --------------------------------------------------------------------------
_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n|\r")
_QUOTED_TOKEN = re.compile(r'"[^"]*"|\'[^\']*\'|[^\s]+')
_PRIVILEGE_PREFIX = re.compile(r"^(?:sudo|doas)\b\s*", re.I)
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Commands that may legitimately precede another command without changing it
# (timeout 10 rm ..., nice rm ..., xargs rm ... is blocked separately above).
_WRAPPERS = {"nice", "nohup", "timeout", "stdbuf", "env", "time", "ionice", "flock"}

# Top-level system directories: recursive rm/chmod/chown on these (or anything
# inside them) is considered destructive and blocked.
_SYSTEM_DIRS = (
    "/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/lib64", "/lib32",
    "/libx32", "/boot", "/dev", "/proc", "/sys", "/root", "/run", "/opt",
    "/srv", "/mnt", "/media", "/lost+found", "/efi",
)

# Single critical files blocked for rm/chmod/chown even without -r.
_CRITICAL_FILES = {
    "/etc/passwd", "/etc/shadow", "/etc/gshadow", "/etc/sudoers",
    "/etc/sudoers.d", "/etc/fstab", "/etc/hosts", "/boot/vmlinuz",
    "/bin/sh", "/bin/bash", "/usr/bin/env", "/usr/bin/sudo",
}


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _base_name(token: str) -> str:
    return token.rsplit("/", 1)[-1] if "/" in token else token


def _destructive_target(target: str) -> bool:
    """True when the path/glob target represents system-level destruction."""
    s = _strip_quotes(str(target)).strip()
    low = s.lower()
    if not s:
        return False
    # root / glob-root / cwd-self / parent / bare glob
    if low in ("/", "/*", "/**", ".", "..", "*", ".*", "*/", "*/*", "./", "../"):
        return True
    if low.startswith("/*/") or low.startswith(".."):
        return True
    # whole-home destruction (deeper user paths like ~/Downloads/x stay allowed)
    if low in ("~", "~/", "~/*", "~/**", "$home", "${home}", "$home/*",
               "$home/**", "/home", "/home/*", "/home/**"):
        return True
    if low.startswith("~/") or low.startswith("$home/") or low.startswith("${home}/"):
        return False
    parts = [p for p in low.split("/") if p]
    if len(parts) == 2 and parts[0] == "home":
        return True  # /home/<user> — an entire user home
    for d in _SYSTEM_DIRS:
        if low == d or low.startswith(d + "/"):
            return True
    # windows drive roots and UNC roots
    if re.fullmatch(r"[a-z]:[\\/](?:\*)?", low):
        return True
    if low.startswith("\\\\"):
        return True
    return False


def _token_pairs(segment: str) -> list[tuple[str, bool]]:
    """Tokenize a segment; returns (text, was_quoted) pairs."""
    out = []
    for m in _QUOTED_TOKEN.finditer(segment):
        raw = m.group(0)
        out.append((_strip_quotes(raw), raw[:1] in ("\"", "'")))
    return out


def _has_recursive_flag(flags: list[str]) -> bool:
    for f in flags:
        if f.startswith("--"):
            if "recursive" in f:
                return True
        elif len(f) > 1:
            if "r" in f[1:].lower():
                return True
    return False


def _is_command_position(tokens: list[str], i: int) -> bool:
    """True when tokens[i] is used as a command (segment start, or after a
    wrapper token such as ``nice``/``timeout 10``/``env X=y``)."""
    if i == 0:
        return True
    j = i - 1
    # skip wrapper arguments: flags, numbers, env assignments
    while j >= 0 and (tokens[j].startswith("-") or tokens[j].isdigit()
                      or _ENV_ASSIGN.match(tokens[j])):
        j -= 1
    return j >= 0 and _base_name(tokens[j]) in _WRAPPERS


_REDIRECT_OPS = (">", ">>", ">|", "&>", "&>>")


def _analyze_rm_like(tokens: list[str], kind: str, workspace: str | None = None) -> str | None:
    """rm: block recursive destruction of system targets; chmod/chown: block
    recursive system operations and any operation on critical files."""
    i = 0
    while i < len(tokens):
        if _base_name(tokens[i]) != kind:
            i += 1
            continue
        if not _is_command_position(tokens, i):
            i += 1
            continue
        flags: list[str] = []
        targets: list[str] = []
        for tok in tokens[i + 1:]:
            if tok == "--":
                continue
            if tok.startswith("-") and tok != "-":
                flags.append(tok)
            else:
                targets.append(tok)
        recursive = _has_recursive_flag(flags)
        if kind == "rm":
            if recursive and any(_destructive_target(t) for t in targets):
                return "destructive rm target blocked by policy"
            if recursive and workspace:
                ws = str(workspace).rstrip("/")
                for t in targets:
                    tgt = _strip_quotes(t).rstrip("/")
                    # the workspace root itself or ANY ANCESTOR of it (the
                    # repo that contains the agent's own source + security
                    # core) — self-destruction, blocked, not approvable
                    if tgt == ws or (tgt and ws.startswith(tgt + "/")):
                        return "workspace root destruction blocked by policy"
            if any(_strip_quotes(t).lower() in _CRITICAL_FILES for t in targets):
                return "critical system file blocked by policy"
        else:  # chmod / chown
            # first non-flag token is the mode/owner spec; the rest are targets
            tgt = targets[1:] if targets else []
            if recursive and any(_destructive_target(t) for t in tgt):
                return f"recursive {kind} on system path blocked by policy"
            if any(_strip_quotes(t).lower() in _CRITICAL_FILES for t in tgt):
                return f"{kind} on critical system file blocked by policy"
        i += 1
    return None


def _analyze_segment(segment: str, depth: int, workspace: str | None = None) -> str | None:
    seg = segment.strip()
    if not seg:
        return None
    # strip privilege prefixes (sudo/doas) — the wrapped command is analyzed
    while True:
        m = _PRIVILEGE_PREFIX.match(seg)
        if not m:
            break
        seg = seg[m.end():].strip()
    pairs = _token_pairs(seg)
    # drop leading environment assignments (FOO=bar rm ...)
    while pairs and _ENV_ASSIGN.match(pairs[0][0]) and not pairs[0][0].startswith("-"):
        pairs.pop(0)
    if not pairs:
        return None
    # recurse into quoted command-looking strings (bash -c "rm -rf /" ...)
    for tok, quoted in pairs:
        if quoted and depth < 3 and (" " in tok or ";" in tok or "&&" in tok):
            reason = _analyze_command(tok, depth + 1, workspace=workspace)
            if reason:
                return reason
    tokens = [tok for tok, _ in pairs]
    for kind in ("rm", "chmod", "chown"):
        reason = _analyze_rm_like(tokens, kind, workspace=workspace)
        if reason:
            return reason
    # redirection clobbering of protected paths: `echo x > /etc/passwd`,
    # `cat f >> /etc/sudoers`, `> /boot/grub.cfg` — bypasses the file-tool
    # sandbox otherwise; blocked regardless of approval
    for i, tok in enumerate(tokens):
        if tok in _REDIRECT_OPS and i + 1 < len(tokens):
            target = _strip_quotes(tokens[i + 1])
            if (_destructive_target(target)
                    or target.lower() in _CRITICAL_FILES):
                return "redirection to protected path blocked by policy"
    return None


# Interpreter first-tokens: piping a DECODED payload into these is an
# obfuscation vector for otherwise-blocked commands (echo cm0... | base64 -d | sh).
_INTERPRETERS = {"sh", "bash", "zsh", "dash", "ksh", "python", "python3", "perl", "ruby"}
_DECODE_HINT = re.compile(
    r"\b(?:base64|base32|openssl|xxd|uudecode|certutil)\b[^|;&]*(?:-d|--decode|-D|-r)\b",
    re.IGNORECASE)


def _analyze_command(cmd: str, depth: int = 0, workspace: str | None = None) -> str | None:
    normalized = " ".join(str(cmd or "").strip().split())
    if not normalized:
        return "empty command"
    for pattern in _BLOCKED:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return "command matches blocked operation policy"
    # decoded/obfuscated payload piped into an interpreter: block regardless
    # of what the encoded bytes contain (approval cannot make it safe)
    # NOTE: `>|` (clobber-redirect) contains the pipe metachar, so normalize
    # it on the ANALYSIS copy before segment splitting (analysis only — the
    # command itself is never executed by the policy).
    segments = _SEGMENT_SPLIT.split(normalized.replace(">|", ">"))
    interpreter_pipe = False
    for seg in segments:
        toks = seg.strip().split()
        while toks and (_PRIVILEGE_PREFIX.match(toks[0]) or toks[0] in _WRAPPERS):
            toks.pop(0)
        if toks and _strip_quotes(toks[0]).lower() in _INTERPRETERS:
            interpreter_pipe = True
            break
    if interpreter_pipe and _DECODE_HINT.search(normalized):
        return "decoded payload piped to an interpreter is blocked by policy"
    for segment in segments:
        reason = _analyze_segment(segment, depth, workspace=workspace)
        if reason:
            return reason
    return None


class ShellPolicy:
    """Classify a command without executing it or changing security state."""

    def evaluate(self, command: str, workspace: str | None = None) -> ShellDecision:
        normalized = " ".join(str(command or "").strip().split())
        if not normalized:
            return ShellDecision(False, "invalid", "empty command", normalized)

        blocked_reason = _analyze_command(normalized, workspace=workspace)
        if blocked_reason:
            return ShellDecision(False, "blocked", blocked_reason, normalized)

        for pattern in _HIGH_RISK:
            if re.search(pattern, normalized, flags=re.IGNORECASE):
                return ShellDecision(
                    True, "high",
                    "command requires explicit approval and sandbox enforcement",
                    normalized)

        return ShellDecision(
            True, "standard", "command requires normal execution controls",
            normalized)


def evaluate_shell(command: str) -> dict[str, object]:
    """JSON-friendly policy result for API/tool boundaries."""
    d = ShellPolicy().evaluate(command)
    return {
        "allowed": d.allowed,
        "risk": d.risk,
        "reason": d.reason,
        "normalized": d.normalized,
    }
