"""Cryptographic receipt-bound goal release state machine and 5-gate release-intent parser.

Implements Slice 3 (Module C) of session-failure-postmortem-and-concurrency-resilience:
- VerificationReceipt frozen dataclass
- Multi-repo working-tree digest (compute_multi_repo_digest)
- Authoritative receipt serialization & parity verification (serialize_verification_receipt, verify_receipt_parity)
- 5-gate fail-closed release-intent parser (release_action_message)
- Unconditional new-prompt reset & receipt-gated fast-path re-promotion (on_new_user_prompt_authorize_release)
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from hermes_cli._subprocess_compat import noninteractive_git_env

VERIFICATION_RECEIPT_TTL_S: float = 3600.0
_GIT_PROBE_TIMEOUT_S: int = 15

CONTINUATION_PROMPT_RELEASE_READY_TEMPLATE: str = (
    "[Continuing toward your standing goal]\n"
    "PHASE: RELEASE_READY (receipt={receipt_digest_short})\n"
    "Goal: {goal}\n\n"
    "All required verification suites and quality gates have passed with cryptographic "
    "receipt parity (tree_sha={tree_sha}, porcelain_baseline_digest={porcelain_baseline_digest}). "
    "Execute the release fast-path in the isolated worktree "
    "(git status --porcelain=v2 -> git add -> git commit -> git push origin HEAD:main) "
    "without re-running pytest or vitest."
)

# ── Gate 2: Negation tokens (EN & VI) ─────────────────────────────────
RELEASE_NEGATION_TOKENS: tuple[str, ...] = (
    "do not",
    "don't",
    "dont",
    "never",
    "not",
    "no",
    "stop",
    "avoid",
    "skip",
    "without",
    "hold off",
    "wait before",
    "không",
    "đừng",
    "chưa",
    "khoan",
    "không được",
    "chớ",
)

# ── Gate 3: Interrogative, hypothetical & explanatory tokens (EN & VI) ─
RELEASE_INTERROGATIVE_HYPOTHETICAL_TOKENS: tuple[str, ...] = (
    "why",
    "why did",
    "how",
    "how to",
    "what",
    "when",
    "where",
    "should",
    "could",
    "would",
    "can we",
    "is it",
    "is it safe",
    "is it safe to",
    "explain",
    "describe",
    "if",
    "suppose",
    "assuming",
    "whether",
    "tại sao",
    "vì sao",
    "như thế nào",
    "làm sao",
    "có nên",
    "giải thích",
    "nếu",
    "giả sử",
)

# ── Gate 4: Modification & task-expansion verbs (EN & VI) ─────────────
NON_RELEASE_MODIFICATION_VERBS: tuple[str, ...] = (
    "fix",
    "bug",
    "edit",
    "modify",
    "refactor",
    "implement",
    "add",
    "add test",
    "update",
    "update code",
    "investigate",
    "debug",
    "revert",
    "change",
    "create",
    "write",
    "delete",
    "remove",
    "sửa",
    "thêm",
    "cập nhật",
    "viết",
    "xóa",
    "kiểm tra lại",
)

# ── Gate 5: Affirmative imperative release command grammar ────────────
_LEAD_IN_PART = (
    r"(?:proceed|please|now|go\s+ahead(?:\s+and)?|ok|okay|approved|"
    r"tiến\s+hành|hãy|bây\s+giờ|đồng\s+ý|vui\s+lòng)"
)
_DIRECTIVE_PART = (
    r"(?:git\s+commit\s+and\s+(?:git\s+)?push|commit\s+and\s+(?:git\s+)?push|"
    r"commit\s*&\s*(?:git\s+)?push|git\s+push|push\s+to\s+origin|fast-forward|fast\s+forward|"
    r"đóng\s+gói\s+commit(?:\s+và\s+đồng\s+bộ)?|đóng\s+gói\s+và\s+đồng\s+bộ|"
    r"tiến\s+hành\s+đóng\s+gói(?:\s+commit)?(?:\s+và\s+đồng\s+bộ)?|"
    r"commit\s+và\s+đồng\s+bộ|commit\s+và\s+push|push\s+lên\s+origin|openspec\s+archive)"
)
_QUALIFIER_PART = (
    r"(?:to\s+origin(?:\s+main)?|origin\s+main|origin\s+head:main|head:main|origin|main|"
    r"vào\s+main|lên\s+origin(?:\s+main)?|và\s+đồng\s+bộ|and\s+push)"
)

AFFIRMATIVE_RELEASE_COMMAND_RE: re.Pattern[str] = re.compile(
    rf"^\s*(?:{_LEAD_IN_PART}[\s,]+)*"
    rf"{_DIRECTIVE_PART}"
    rf"(?:[\s,]+(?:(?:and|&|và)\s+)?{_DIRECTIVE_PART})*"
    rf"(?:[\s,]+{_QUALIFIER_PART})*"
    rf"[\s.!]*$",
    re.IGNORECASE | re.UNICODE,
)

_CONTRACTION_MAP: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdon['’]t\b", re.IGNORECASE), "do not"),
    (re.compile(r"\bcan['’]t\b", re.IGNORECASE), "cannot"),
    (re.compile(r"\bwon['’]t\b", re.IGNORECASE), "will not"),
    (re.compile(r"\bshouldn['’]t\b", re.IGNORECASE), "should not"),
    (re.compile(r"\bwouldn['’]t\b", re.IGNORECASE), "would not"),
    (re.compile(r"\bcouldn['’]t\b", re.IGNORECASE), "could not"),
    (re.compile(r"\bisn['’]t\b", re.IGNORECASE), "is not"),
    (re.compile(r"\baren['’]t\b", re.IGNORECASE), "are not"),
    (re.compile(r"\bdoesn['’]t\b", re.IGNORECASE), "does not"),
    (re.compile(r"\bdidn['’]t\b", re.IGNORECASE), "did not"),
    (re.compile(r"\bhaven['’]t\b", re.IGNORECASE), "have not"),
    (re.compile(r"\bhasn['’]t\b", re.IGNORECASE), "has not"),
    (re.compile(r"\blet['’]s\b", re.IGNORECASE), "let us"),
    (re.compile(r"\bit['’]s\b", re.IGNORECASE), "it is"),
    (re.compile(r"\bthat['’]s\b", re.IGNORECASE), "that is"),
)

_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_BACKTICK_RE = re.compile(r"`[^`]*`")
_DOUBLE_QUOTE_RE = re.compile(r'"[^"]*"|“[^”]*”')
_SINGLE_QUOTE_RE = re.compile(r"'[^']*'|‘[^’]*’")
_BLOCKQUOTE_LINE_RE = re.compile(r"(?m)^\s*>.*$")
_UNCLOSED_QUOTE_OR_TICK_RE = re.compile(r'[`"“”\'‘’]')

_RELEASE_MARKER_KEYWORDS_RE = re.compile(
    r"(?<!\w)(?:commit|push|fast-forward|fast\s+forward|archive|đóng\s+gói|đồng\s+bộ)(?!\w)",
    re.IGNORECASE | re.UNICODE,
)


@dataclass(frozen=True)
class VerificationReceipt:
    """Cryptographic multi-repo verification receipt bound to GoalState."""

    repo_roots: tuple[str, ...] = ()
    commit_shas: dict[str, str] = field(default_factory=dict)
    submodule_shas: dict[str, str] = field(default_factory=dict)
    tree_sha: str = ""
    porcelain_baseline_digest: str = ""
    proof_command_signature: tuple[str, ...] = ()
    proof_exit_code: int = 0
    proof_output_digest: str = ""
    verification_timestamp: float = 0.0
    governed_artifacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "repo_roots",
            tuple(str(r) for r in (self.repo_roots or ())),
        )
        object.__setattr__(
            self,
            "commit_shas",
            dict(self.commit_shas or {}),
        )
        object.__setattr__(
            self,
            "submodule_shas",
            dict(self.submodule_shas or {}),
        )
        object.__setattr__(
            self,
            "proof_command_signature",
            tuple(str(c) for c in (self.proof_command_signature or ())),
        )
        object.__setattr__(
            self,
            "governed_artifacts",
            tuple(str(a) for a in (self.governed_artifacts or ())),
        )
        object.__setattr__(self, "tree_sha", str(self.tree_sha or ""))
        object.__setattr__(
            self,
            "porcelain_baseline_digest",
            str(self.porcelain_baseline_digest or ""),
        )
        object.__setattr__(self, "proof_exit_code", int(self.proof_exit_code or 0))
        object.__setattr__(self, "proof_output_digest", str(self.proof_output_digest or ""))
        object.__setattr__(
            self,
            "verification_timestamp",
            float(self.verification_timestamp or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_roots": list(self.repo_roots),
            "commit_shas": dict(self.commit_shas),
            "submodule_shas": dict(self.submodule_shas),
            "tree_sha": self.tree_sha,
            "porcelain_baseline_digest": self.porcelain_baseline_digest,
            "proof_command_signature": list(self.proof_command_signature),
            "proof_exit_code": self.proof_exit_code,
            "proof_output_digest": self.proof_output_digest,
            "verification_timestamp": self.verification_timestamp,
            "governed_artifacts": list(self.governed_artifacts),
        }

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> Optional[VerificationReceipt]:
        if not isinstance(data, Mapping):
            return None
        return cls(
            repo_roots=tuple(str(x) for x in (data.get("repo_roots") or ())),
            commit_shas={str(k): str(v) for k, v in (data.get("commit_shas") or {}).items()},
            submodule_shas={str(k): str(v) for k, v in (data.get("submodule_shas") or {}).items()},
            tree_sha=str(data.get("tree_sha") or ""),
            porcelain_baseline_digest=str(data.get("porcelain_baseline_digest") or ""),
            proof_command_signature=tuple(
                str(x) for x in (data.get("proof_command_signature") or ())
            ),
            proof_exit_code=int(data.get("proof_exit_code", 0)),
            proof_output_digest=str(data.get("proof_output_digest") or ""),
            verification_timestamp=float(data.get("verification_timestamp") or 0.0),
            governed_artifacts=tuple(str(x) for x in (data.get("governed_artifacts") or ())),
        )


def _canonical_path(raw: str | Path) -> str:
    try:
        return Path(raw).expanduser().resolve().as_posix()
    except OSError:
        return Path(raw).expanduser().as_posix()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        if not path.exists():
            return "<missing>"
        if path.is_dir():
            entries: list[str] = []
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    rel = child.relative_to(path).as_posix()
                    entries.append(f"{rel}:{_sha256_file(child)}")
            return _sha256_bytes("\n".join(entries).encode("utf-8"))
        return _sha256_bytes(path.read_bytes())
    except Exception as exc:
        # Fail closed: an extant-but-unreadable/unresolvable path yields a unique nonce so it can
        # never match a previously serialized receipt digest (S-03).
        return f"<unreadable:{time.time_ns()}:{type(exc).__name__}>"


def _run_git_bytes(repo_root: str, *args: str) -> tuple[int, bytes, str]:
    """Run git and return raw stdout bytes (for NUL-delimited output where paths must be byte-exact)."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root, *args],
            capture_output=True,
            env=noninteractive_git_env(),
            timeout=_GIT_PROBE_TIMEOUT_S,
            check=False,
        )
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
        return proc.returncode, proc.stdout or b"", stderr
    except Exception as exc:
        return -1, b"", f"{type(exc).__name__}: {exc}"


def _run_git(repo_root: str, *args: str) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=noninteractive_git_env(),
            timeout=_GIT_PROBE_TIMEOUT_S,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except Exception as exc:
        return -1, "", f"{type(exc).__name__}: {exc}"


def _parse_porcelain_v2_z(raw_bytes: bytes) -> tuple[list[str], list[str]]:
    """Parse ``git status --porcelain=v2 -z`` output.

    With ``-z`` records are NUL-terminated and paths are emitted byte-exact (never C-quoted or
    octal-escaped), so non-ASCII / special-character filenames resolve on disk (S-03). Rename
    records (``2 ...``) carry ``<path>`` in the record and ``<origPath>`` as the following NUL token.

    Returns ``(porcelain_entries, sorted(set(paths)))``.
    """
    tokens = raw_bytes.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    entries: list[str] = []
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        line = tokens[i].decode("utf-8", errors="surrogateescape")
        i += 1
        if not line or line.startswith("#"):
            continue
        if line.startswith(("? ", "! ")):
            paths.append(line[2:])
            entries.append(line)
        elif line.startswith(("1 ", "u ")):
            # 1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
            # u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
            max_splits = 8 if line.startswith("1 ") else 10
            parts = line.split(" ", max_splits)
            if len(parts) > max_splits and parts[max_splits]:
                paths.append(parts[max_splits])
            entries.append(line)
        elif line.startswith("2 "):
            # 2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>NUL<origPath>NUL
            parts = line.split(" ", 9)
            orig_path = ""
            if i < len(tokens):
                orig_path = tokens[i].decode("utf-8", errors="surrogateescape")
                i += 1
            if len(parts) > 9 and parts[9]:
                paths.append(parts[9])
            entries.append(f"{line}\t{orig_path}")
        else:
            entries.append(line)
    return entries, sorted(set(paths))


def _probe_submodules(repo_root: str) -> Optional[dict[str, str]]:
    rc, stdout, _ = _run_git(repo_root, "ls-files", "--stage")
    if rc != 0:
        return None
    submodules: dict[str, str] = {}
    for line in stdout.splitlines():
        # Format: <mode> <object> <stage>\t<file>
        if not line.startswith("160000 "):
            continue
        meta, _, rel_path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 2 and rel_path:
            sub_full = Path(repo_root) / rel_path
            sub_rc, sub_head, _ = _run_git(sub_full.as_posix(), "rev-parse", "HEAD")
            sha = sub_head.strip() if sub_rc == 0 and sub_head.strip() else parts[1]
            submodules[f"{repo_root}:{rel_path}"] = sha
    return submodules


def compute_multi_repo_digest(
    repo_roots: Sequence[str | Path],
    governed_artifacts: Sequence[str | Path] = (),
) -> str:
    """Compute a deterministic SHA-256 digest over git status --porcelain=v2, on-disk SHA-256s of
    all dirty/untracked files across repo_roots, and any governed non-Git artifacts."""
    canonical_roots = sorted({_canonical_path(r) for r in (repo_roots or ()) if str(r).strip()})
    canonical_artifacts = sorted(
        {_canonical_path(a) for a in (governed_artifacts or ()) if str(a).strip()}
    )

    records: list[dict[str, Any]] = []
    for root in canonical_roots:
        root_path = Path(root)
        if root_path.is_file():
            records.append(
                {
                    "kind": "artifact_file",
                    "path": root,
                    "sha256": _sha256_file(root_path),
                }
            )
            continue
        if not root_path.exists():
            records.append(
                {
                    "kind": "missing_root",
                    "path": root,
                    "nonce": f"missing:{time.time_ns()}",
                }
            )
            continue

        rc, porcelain_raw, err_out = _run_git_bytes(
            root, "status", "--porcelain=v2", "--untracked-files=all", "-z"
        )
        if rc != 0:
            records.append(
                {
                    "kind": "git_error",
                    "path": root,
                    "rc": rc,
                    "error": err_out.strip(),
                    "nonce": f"git_error:{time.time_ns()}",
                }
            )
            continue

        porcelain_lines, dirty_paths = _parse_porcelain_v2_z(porcelain_raw)
        dirty_hashes: dict[str, str] = {}
        for rel_path in dirty_paths:
            dirty_hashes[rel_path] = _sha256_file(root_path / rel_path)

        records.append(
            {
                "kind": "git_repo",
                "repo_root": root,
                "porcelain_v2": porcelain_lines,
                "dirty_file_sha256": dirty_hashes,
            }
        )

    for artifact in canonical_artifacts:
        if artifact in canonical_roots and Path(artifact).is_file():
            continue
        records.append(
            {
                "kind": "governed_artifact",
                "path": artifact,
                "sha256": _sha256_file(Path(artifact)),
            }
        )

    serialized = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(serialized)


def _compute_tree_sha(
    commit_shas: Mapping[str, str],
    submodule_shas: Mapping[str, str],
    porcelain_baseline_digest: str,
) -> str:
    payload = {
        "commit_shas": {str(k): str(v) for k, v in sorted(commit_shas.items())},
        "submodule_shas": {str(k): str(v) for k, v in sorted(submodule_shas.items())},
        "porcelain_baseline_digest": str(porcelain_baseline_digest),
    }
    return _sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _normalize_output_digest(raw_digest_or_output: str) -> str:
    text = str(raw_digest_or_output or "").strip()
    if len(text) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in text):
        return text.lower()
    return _sha256_bytes(text.encode("utf-8"))


def serialize_verification_receipt(
    repo_roots: Iterable[str | Path],
    proof_command_signature: Iterable[str] = (),
    proof_exit_code: int = 0,
    proof_output_digest: str = "",
    *,
    governed_artifacts: Iterable[str | Path] = (),
    verification_timestamp: Optional[float] = None,
) -> VerificationReceipt:
    """Create a cryptographic VerificationReceipt across all canonical repo_roots and governed_artifacts."""
    canonical_roots = tuple(
        sorted({_canonical_path(r) for r in (repo_roots or ()) if str(r).strip()})
    )
    canonical_artifacts = tuple(
        sorted({_canonical_path(a) for a in (governed_artifacts or ()) if str(a).strip()})
    )
    if not canonical_roots:
        raise ValueError("serialize_verification_receipt requires at least one repo_root")

    commit_shas: dict[str, str] = {}
    submodule_shas: dict[str, str] = {}
    for root in canonical_roots:
        rc, head_out, err_out = _run_git(root, "rev-parse", "HEAD")
        head_sha = head_out.strip()
        if rc != 0 or len(head_sha) != 40:
            raise ValueError(
                f"Failed to resolve 40-hex HEAD SHA for repo_root {root!r} (rc={rc}): {err_out.strip()}"
            )
        commit_shas[root] = head_sha
        subs = _probe_submodules(root)
        if subs is None:
            raise ValueError(f"Failed to probe submodules for repo_root {root!r}")
        submodule_shas.update(subs)

    porcelain_digest = compute_multi_repo_digest(canonical_roots, canonical_artifacts)
    tree_sha = _compute_tree_sha(commit_shas, submodule_shas, porcelain_digest)
    ts = float(time.time() if verification_timestamp is None else verification_timestamp)

    return VerificationReceipt(
        repo_roots=canonical_roots,
        commit_shas=commit_shas,
        submodule_shas=submodule_shas,
        tree_sha=tree_sha,
        porcelain_baseline_digest=porcelain_digest,
        proof_command_signature=tuple(str(c) for c in (proof_command_signature or ())),
        proof_exit_code=int(proof_exit_code),
        proof_output_digest=_normalize_output_digest(proof_output_digest),
        verification_timestamp=ts,
        governed_artifacts=canonical_artifacts,
    )


def verify_receipt_parity(
    receipt: Optional[VerificationReceipt],
    *,
    now: Optional[float] = None,
) -> bool:
    """Verify that a VerificationReceipt is non-null, passed (proof_exit_code == 0), is within its
    3600s TTL, and matches the live multi-repo HEAD SHAs, submodule SHAs, and porcelain digest."""
    if receipt is None or not isinstance(receipt, VerificationReceipt):
        return False
    if receipt.proof_exit_code != 0:
        return False
    if not receipt.repo_roots or not receipt.tree_sha or not receipt.porcelain_baseline_digest:
        return False
    if receipt.verification_timestamp <= 0.0:
        return False

    current_time = float(time.time() if now is None else now)
    age = current_time - receipt.verification_timestamp
    if age > VERIFICATION_RECEIPT_TTL_S or age < -60.0:
        return False

    live_commit_shas: dict[str, str] = {}
    live_submodule_shas: dict[str, str] = {}
    for root in receipt.repo_roots:
        expected_head = receipt.commit_shas.get(root)
        if not expected_head:
            return False
        rc, head_out, _ = _run_git(root, "rev-parse", "HEAD")
        live_head = head_out.strip()
        if rc != 0 or live_head != expected_head:
            return False
        live_commit_shas[root] = live_head

        subs = _probe_submodules(root)
        if subs is None:
            return False
        live_submodule_shas.update(subs)

    if live_submodule_shas != dict(receipt.submodule_shas):
        return False

    current_porcelain_digest = compute_multi_repo_digest(
        receipt.repo_roots, receipt.governed_artifacts
    )
    if current_porcelain_digest != receipt.porcelain_baseline_digest:
        return False

    expected_tree_sha = _compute_tree_sha(
        live_commit_shas, live_submodule_shas, current_porcelain_digest
    )
    return expected_tree_sha == receipt.tree_sha


def _expand_contractions(text: str) -> str:
    out = text
    for pattern, replacement in _CONTRACTION_MAP:
        out = pattern.sub(replacement, out)
    return out


def _contains_token_or_phrase(normalized_text: str, tokens: Sequence[str]) -> bool:
    for token in tokens:
        escaped = r"\s+".join(re.escape(part) for part in token.lower().split())
        if re.search(rf"(?<!\w){escaped}(?!\w)", normalized_text, flags=re.IGNORECASE | re.UNICODE):
            return True
    return False


def release_action_message(user_message: str) -> bool:
    """Evaluate the 5-gate fail-closed release-intent grammar on user_message.

    Gate 1: Strip fenced code blocks, inline backticks, double/single quotes (after contraction
            expansion), and markdown blockquotes. Reject if any release marker was inside a
            quoted/fenced/blockquote span, if unclosed quotes remain, or if the remainder is empty.
    Gate 2: Reject any negation token/phrase in RELEASE_NEGATION_TOKENS (EN & VI).
    Gate 3: Reject '?' or any interrogative/hypothetical/explanatory token in
            RELEASE_INTERROGATIVE_HYPOTHETICAL_TOKENS (EN & VI).
    Gate 4: Reject any modification or task-expansion verb in NON_RELEASE_MODIFICATION_VERBS (EN & VI).
    Gate 5: Require the entire unquoted normalized message to match AFFIRMATIVE_RELEASE_COMMAND_RE.
    """
    if not isinstance(user_message, str) or not user_message.strip():
        return False

    # Gate 3 early check on raw '?' (before any stripping)
    if "?" in user_message or "？" in user_message:
        return False

    expanded = _expand_contractions(user_message)

    # Gate 1: Extract and strip code fences, inline backticks, quotes, and blockquotes
    stripped_spans: list[str] = []
    working = expanded
    for span_re in (
        _FENCED_CODE_RE,
        _INLINE_BACKTICK_RE,
        _DOUBLE_QUOTE_RE,
        _SINGLE_QUOTE_RE,
        _BLOCKQUOTE_LINE_RE,
    ):
        for match in span_re.finditer(working):
            stripped_spans.append(match.group(0))
        working = span_re.sub(" ", working)

    # Reject if any stripped span contained a release keyword or if any unclosed quote/backtick remains
    for span in stripped_spans:
        if _RELEASE_MARKER_KEYWORDS_RE.search(span):
            return False
    if _UNCLOSED_QUOTE_OR_TICK_RE.search(working):
        return False

    normalized = " ".join(working.strip().lower().split())
    if not normalized:
        return False

    # Gate 2: Negation prefix & clause rejection (EN & VI)
    if _contains_token_or_phrase(normalized, RELEASE_NEGATION_TOKENS):
        return False

    # Gate 3: Interrogative, hypothetical & explanatory rejection (EN & VI)
    if _contains_token_or_phrase(normalized, RELEASE_INTERROGATIVE_HYPOTHETICAL_TOKENS):
        return False

    # Gate 4: Modification & task-expansion verb rejection (EN & VI)
    if _contains_token_or_phrase(normalized, NON_RELEASE_MODIFICATION_VERBS):
        return False

    # Gate 5: Affirmative imperative release command grammar
    return bool(AFFIRMATIVE_RELEASE_COMMAND_RE.match(normalized))


def on_new_user_prompt_authorize_release(
    goal_state: Any,
    user_message: str,
    *,
    now: Optional[float] = None,
) -> str:
    """Two-step turn-entry release authorization protocol (S-02-R2, A1).

    Step 1 (Unconditional Reset): Unconditionally resets goal_state.phase = "ACTIVE" and unlinks
    goal_state.active_verification_receipt = None on arrival of EVERY new user prompt.
    Step 2 (Fail-Closed Re-Promotion): Re-promotes ACTIVE -> VERIFIED -> RELEASE_READY and re-links
    active_verification_receipt = last_verified_receipt if and only if release_action_message(user_message)
    passes all 5 fail-closed gates AND verify_receipt_parity(last_verified_receipt) is True.
    """
    if goal_state is None:
        return "ACTIVE"

    candidate_receipt = getattr(goal_state, "last_verified_receipt", None) or getattr(
        goal_state, "active_verification_receipt", None
    )

    # Step 1: Unconditional reset on every new user prompt
    goal_state.phase = "ACTIVE"
    goal_state.active_verification_receipt = None
    goal_state.last_transition_history = ("ACTIVE",)

    # Validate candidate receipt parity first; purge last_verified_receipt if tree/TTL drifted
    if candidate_receipt is not None and not verify_receipt_parity(candidate_receipt, now=now):
        goal_state.last_verified_receipt = None
        return "ACTIVE"

    # Step 2: 5-gate fail-closed grammar + receipt parity
    if not release_action_message(user_message):
        return "ACTIVE"

    if candidate_receipt is None:
        return "ACTIVE"

    # Transition ACTIVE -> VERIFIED -> RELEASE_READY
    goal_state.phase = "VERIFIED"
    goal_state.phase = "RELEASE_READY"
    goal_state.active_verification_receipt = candidate_receipt
    goal_state.last_verified_receipt = candidate_receipt
    goal_state.last_transition_history = ("ACTIVE", "VERIFIED", "RELEASE_READY")
    return "RELEASE_READY"


__all__ = [
    "AFFIRMATIVE_RELEASE_COMMAND_RE",
    "CONTINUATION_PROMPT_RELEASE_READY_TEMPLATE",
    "NON_RELEASE_MODIFICATION_VERBS",
    "RELEASE_INTERROGATIVE_HYPOTHETICAL_TOKENS",
    "RELEASE_NEGATION_TOKENS",
    "VERIFICATION_RECEIPT_TTL_S",
    "VerificationReceipt",
    "compute_multi_repo_digest",
    "on_new_user_prompt_authorize_release",
    "release_action_message",
    "serialize_verification_receipt",
    "verify_receipt_parity",
]
