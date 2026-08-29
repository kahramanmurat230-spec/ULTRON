"""Secret redaction for logs, audit trail, prompts and tool output.

Every value that looks like a credential is masked BEFORE it reaches disk
(audit.log), the LLM prompt, or a tool result. The vault additionally passes
its concrete secret values via `extra_values` so stored secrets can never leak
even when they do not match a pattern.
"""
import re

_MASK = "***REDACTED***"

# key[:= ]value  (also Turkish keys: şifre/parola; separator may be ':', '='
# or a plain space before a quoted/bare value)
_PAIR_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_\-]?key|apikey|secret|client[_\-]?secret|şifre|şifrem|parola)\b"
    r"(['\"]?\s*(?:[:=]\s*['\"]?|\s+['\"]?))(\S{3,})")
# Bearer <jwt/opaque>
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+([a-z0-9\-._~+/]{8,})")
# credentials in URLs: scheme://user:pass@host
_URLCREDS_RE = re.compile(r"(?i)(https?://)([^/\s:@]{1,64}):([^/\s@]{3,64})@")

SECRET_VALUE_PATTERNS = (_PAIR_RE, _BEARER_RE, _URLCREDS_RE)


def redact(text: str, extra_values=()) -> str:
    """Return `text` with credential-looking values (and any concrete values
    listed in `extra_values`) replaced by ***REDACTED***."""
    if not text:
        return text or ""
    out = str(text)
    for val in extra_values:
        if val and len(str(val)) >= 4:
            out = out.replace(str(val), _MASK)
    out = _PAIR_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_MASK}", out)
    out = _BEARER_RE.sub(lambda m: f"{m.group(1)} {_MASK}", out)
    out = _URLCREDS_RE.sub(lambda m: f"{m.group(1)}{_MASK}@", out)
    return out
