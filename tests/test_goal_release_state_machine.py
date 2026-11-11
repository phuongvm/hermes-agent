"""Unit tests for Slice 3: Cryptographic Receipt-Bound Goal Release State Machine.

Covers:
- ACTIVE -> VERIFIED -> RELEASE_READY transitions bound to VerificationReceipt
- serialize_verification_receipt() and verify_receipt_parity() across multi-repo roots
- Step 1 unconditional new-prompt reset (RELEASE_READY / VERIFIED -> ACTIVE + unlink active_verification_receipt)
- Step 2 5-gate fail-closed release_action_message() parser:
  * Gate 1: Quoted, code-fenced, inline-backtick, and blockquote rejection
  * Gate 2: Negation prefix & clause rejection (EN & VI)
  * Gate 3: Interrogative, hypothetical & explanatory rejection (EN & VI)
  * Gate 4: Modification & task-expansion verb rejection (EN & VI)
  * Gate 5: Affirmative imperative release command grammar
- Same-turn fast-path re-promotion (ACTIVE -> VERIFIED -> RELEASE_READY) only with valid receipt parity
- Invalidation to ACTIVE on file edit, untracked file creation, branch/HEAD change, or >3600s TTL expiry
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from hermes_cli.goal_release import (
    AFFIRMATIVE_RELEASE_COMMAND_RE,
    CONTINUATION_PROMPT_RELEASE_READY_TEMPLATE,
    NON_RELEASE_MODIFICATION_VERBS,
    RELEASE_INTERROGATIVE_HYPOTHETICAL_TOKENS,
    RELEASE_NEGATION_TOKENS,
    VERIFICATION_RECEIPT_TTL_S,
    VerificationReceipt,
    compute_multi_repo_digest,
    on_new_user_prompt_authorize_release,
    release_action_message,
    serialize_verification_receipt,
    verify_receipt_parity,
)
from hermes_cli.goals import GoalManager, GoalState


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout.strip()


def _init_git_repo(base: Path, name: str = "repo_a") -> Path:
    repo = base / name
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    tracked = repo / "module.py"
    tracked.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "module.py")
    _git(repo, "commit", "-m", "initial commit")
    return repo


def test_new_user_prompt_unconditionally_resets_release_ready_to_active(tmp_path: Path) -> None:
    """Step 1 of on_new_user_prompt_authorize_release unconditionally resets RELEASE_READY -> ACTIVE
    and unlinks active_verification_receipt = None on every new user prompt."""
    repo = _init_git_repo(tmp_path, "repo_reset")
    receipt = serialize_verification_receipt(
        repo_roots=[str(repo)],
        proof_command_signature=("pytest -q",),
        proof_exit_code=0,
        proof_output_digest="1 passed in 0.12s",
    )
    assert verify_receipt_parity(receipt) is True

    state = GoalState(
        goal="Ship verified slice",
        phase="RELEASE_READY",
        active_verification_receipt=receipt,
        last_verified_receipt=receipt,
    )
    assert state.phase == "RELEASE_READY"
    assert state.active_verification_receipt == receipt

    # Non-release question arrives: must unconditionally reset to ACTIVE and unlink active receipt
    resulting_phase = on_new_user_prompt_authorize_release(
        state,
        "explain why test X was slow",
    )
    assert resulting_phase == "ACTIVE"
    assert state.phase == "ACTIVE"
    assert state.active_verification_receipt is None
    # last_verified_receipt remains cached while tree is clean so a subsequent valid release command can use it
    assert state.last_verified_receipt == receipt

    # Also verify reset from VERIFIED phase
    state.phase = "VERIFIED"
    state.active_verification_receipt = receipt
    resulting_phase_2 = on_new_user_prompt_authorize_release(
        state,
        "status summary please",
    )
    assert resulting_phase_2 == "ACTIVE"
    assert state.phase == "ACTIVE"
    assert state.active_verification_receipt is None


def test_negated_release_prompts_rejected_fail_closed(tmp_path: Path) -> None:
    """Gate 2 rejects negated release prompts in both English and Vietnamese fail-closed."""
    repo = _init_git_repo(tmp_path, "repo_neg")
    receipt = serialize_verification_receipt(
        repo_roots=[str(repo)],
        proof_command_signature=("pytest -q",),
        proof_exit_code=0,
        proof_output_digest="all pass",
    )

    negated_prompts = [
        "do not commit and push",
        "don't git push",
        "dont commit and push",
        "never commit and push",
        "not ready to commit and push",
        "no commit and push",
        "stop before git push",
        "avoid git push",
        "skip commit and push",
        "without git push",
        "hold off on commit and push",
        "wait before commit and push",
        "không commit và push",
        "đừng đóng gói commit",
        "đừng đóng gói commit và đồng bộ",
        "chưa commit và đồng bộ",
        "chưa commit và push",
        "khoan đóng gói commit",
        "không được git push",
        "chớ commit và đồng bộ",
    ]

    for prompt in negated_prompts:
        assert release_action_message(prompt) is False, f"Expected False for negated prompt: {prompt!r}"
        state = GoalState(
            goal="Implement feature",
            phase="RELEASE_READY",
            active_verification_receipt=receipt,
            last_verified_receipt=receipt,
        )
        phase = on_new_user_prompt_authorize_release(state, prompt)
        assert phase == "ACTIVE", f"Negated prompt {prompt!r} must leave phase ACTIVE"
        assert state.phase == "ACTIVE"
        assert state.active_verification_receipt is None


def test_quoted_and_code_fenced_release_markers_rejected_fail_closed(tmp_path: Path) -> None:
    """Gate 1 strips fenced code, inline backticks, single/double/curly quotes, and blockquotes;
    any release marker inside quoted/code spans or leaving an empty/non-command remainder is rejected."""
    repo = _init_git_repo(tmp_path, "repo_quote")
    receipt = serialize_verification_receipt(
        repo_roots=[str(repo)],
        proof_command_signature=("pytest -q",),
        proof_exit_code=0,
        proof_output_digest="ok",
    )

    quoted_prompts = [
        "explain why 'commit and push' is unsafe",
        'task: "commit and push"',
        "run `git push` later",
        "explain why “commit and push” is unsafe",
        "explain why ‘commit and push’ failed",
        "'commit and push'",
        '"git push"',
        "`commit and push`",
        "```bash\ngit push origin main\n```",
        "> commit and push",
        "proceed with 'commit and push' after review",
    ]

    for prompt in quoted_prompts:
        assert release_action_message(prompt) is False, f"Expected False for quoted/fenced prompt: {prompt!r}"
        state = GoalState(
            goal="Implement feature",
            phase="RELEASE_READY",
            active_verification_receipt=receipt,
            last_verified_receipt=receipt,
        )
        phase = on_new_user_prompt_authorize_release(state, prompt)
        assert phase == "ACTIVE", f"Quoted/fenced prompt {prompt!r} must reset to ACTIVE"
        assert state.phase == "ACTIVE"
        assert state.active_verification_receipt is None


def test_interrogative_and_hypothetical_release_prompts_rejected_fail_closed(tmp_path: Path) -> None:
    """Gate 3 rejects prompts with '?' or interrogative, hypothetical, or explanatory tokens (EN & VI)."""
    repo = _init_git_repo(tmp_path, "repo_interrogative")
    receipt = serialize_verification_receipt(
        repo_roots=[str(repo)],
        proof_command_signature=("pytest -q",),
        proof_exit_code=0,
        proof_output_digest="ok",
    )

    interrogative_prompts = [
        "why did commit and push fail?",
        "why did commit and push fail",
        "how to commit and push",
        "is it safe to commit and push?",
        "is it safe to commit and push",
        "tại sao đóng gói commit thất bại?",
        "tại sao đóng gói commit thất bại",
        "vì sao commit và đồng bộ lỗi",
        "làm sao để commit and push",
        "như thế nào khi git push",
        "có nên đóng gói commit",
        "giải thích quy trình commit and push",
        "nếu tiến hành đóng gói commit",
        "giả sử commit và đồng bộ",
        "should we commit and push",
        "could you explain commit and push",
        "what happens if we commit and push",
        "when to git push",
        "where does git push go",
        "if we commit and push",
        "suppose we git push",
        "assuming we commit and push",
        "whether to commit and push",
        "commit and push?",
    ]

    for prompt in interrogative_prompts:
        assert release_action_message(prompt) is False, f"Expected False for interrogative prompt: {prompt!r}"
        state = GoalState(
            goal="Implement feature",
            phase="RELEASE_READY",
            active_verification_receipt=receipt,
            last_verified_receipt=receipt,
        )
        phase = on_new_user_prompt_authorize_release(state, prompt)
        assert phase == "ACTIVE", f"Interrogative prompt {prompt!r} must remain ACTIVE"
        assert state.phase == "ACTIVE"
        assert state.active_verification_receipt is None


def test_prompt_containing_commit_in_non_release_context_does_not_authorize_release(tmp_path: Path) -> None:
    """Gate 4 rejects modification & task-expansion verbs even when combined with release phrases."""
    repo = _init_git_repo(tmp_path, "repo_mod")
    receipt = serialize_verification_receipt(
        repo_roots=[str(repo)],
        proof_command_signature=("pytest -q",),
        proof_exit_code=0,
        proof_output_digest="ok",
    )

    modification_prompts = [
        "please fix bug in commit parser and commit and push",
        "update code and push",
        "edit goal_release.py and commit and push",
        "modify the test then git push",
        "refactor release handler and commit and push",
        "implement task 3.2 and commit and push",
        "add test and commit and push",
        "investigate failure and git push",
        "debug the issue and commit and push",
        "revert the commit and push",
        "change config and git push",
        "create manifest and commit and push",
        "write docstring and commit and push",
        "delete temp file and git push",
        "remove unused import and commit and push",
        "sửa lỗi rồi đóng gói commit và đồng bộ",
        "thêm test và commit và đồng bộ",
        "cập nhật tài liệu rồi git push",
        "viết thêm hàm và đóng gói commit",
        "xóa file nháp và git push",
        "kiểm tra lại rồi đóng gói commit",
    ]

    for prompt in modification_prompts:
        assert release_action_message(prompt) is False, f"Expected False for modification prompt: {prompt!r}"
        state = GoalState(
            goal="Implement feature",
            phase="RELEASE_READY",
            active_verification_receipt=receipt,
            last_verified_receipt=receipt,
        )
        phase = on_new_user_prompt_authorize_release(state, prompt)
        assert phase == "ACTIVE", f"Modification prompt {prompt!r} must remain ACTIVE"
        assert state.phase == "ACTIVE"
        assert state.active_verification_receipt is None


def test_affirmative_release_command_re_promotes_only_with_valid_receipt(tmp_path: Path) -> None:
    """Gate 5 accepts affirmative imperative release commands (EN & VI) and Step 2 re-promotes
    ACTIVE -> VERIFIED -> RELEASE_READY only when verify_receipt_parity(last_verified_receipt) is True."""
    repo = _init_git_repo(tmp_path, "repo_aff")
    receipt = serialize_verification_receipt(
        repo_roots=[str(repo)],
        proof_command_signature=("pytest -q",),
        proof_exit_code=0,
        proof_output_digest="7 passed",
    )

    affirmative_prompts = [
        "commit and push",
        "proceed tiến hành đóng gói commit và đồng bộ",
        "please commit and push",
        "go ahead and commit and push to origin",
        "now git push origin HEAD:main",
        "ok commit & push",
        "approved commit and push to origin",
        "tiến hành đóng gói commit",
        "tiến hành đóng gói và đồng bộ",
        "hãy commit và đồng bộ lên origin",
        "bây giờ push lên origin",
        "đồng ý đóng gói commit vào main",
        "openspec archive",
        "fast-forward to origin",
    ]

    for prompt in affirmative_prompts:
        assert release_action_message(prompt) is True, f"Expected True for affirmative prompt: {prompt!r}"
        state = GoalState(
            goal="Ship verified slice",
            phase="RELEASE_READY",
            active_verification_receipt=receipt,
            last_verified_receipt=receipt,
        )
        phase = on_new_user_prompt_authorize_release(state, prompt)
        assert phase == "RELEASE_READY", f"Affirmative prompt {prompt!r} with valid receipt must re-promote to RELEASE_READY"
        assert state.phase == "RELEASE_READY"
        assert state.active_verification_receipt == receipt
        assert state.last_verified_receipt == receipt
        # Verify transition path included unconditional Step 1 reset followed by ACTIVE -> VERIFIED -> RELEASE_READY
        assert state.last_transition_history == ("ACTIVE", "VERIFIED", "RELEASE_READY")

        # Verify continuation prompt template formatting
        rendered = state.render_release_ready_prompt()
        expected_prefix = f"PHASE: RELEASE_READY (receipt={receipt.tree_sha[:12]})"
        assert expected_prefix in rendered
        assert CONTINUATION_PROMPT_RELEASE_READY_TEMPLATE.split("{")[0] in rendered

    # Without a valid receipt (last_verified_receipt is None), affirmative command MUST NOT promote
    state_no_receipt = GoalState(
        goal="Ship unverified slice",
        phase="ACTIVE",
        active_verification_receipt=None,
        last_verified_receipt=None,
    )
    phase_unverified = on_new_user_prompt_authorize_release(state_no_receipt, "commit and push")
    assert phase_unverified == "ACTIVE"
    assert state_no_receipt.phase == "ACTIVE"
    assert state_no_receipt.active_verification_receipt is None


def test_release_ready_requires_valid_verification_receipt_and_resets_on_file_edit(tmp_path: Path) -> None:
    """Multi-repo file drift, untracked file creation, branch/HEAD mutation, non-zero exit code,
    or TTL > 3600s causes verify_receipt_parity() to return False and resets GoalState to ACTIVE."""
    repo_a = _init_git_repo(tmp_path, "repo_a")
    repo_b = _init_git_repo(tmp_path, "repo_b")
    governed_doc = tmp_path / "governed_artifact.md"
    governed_doc.write_text("# Section 13\n", encoding="utf-8")

    now_ts = time.time()
    receipt = serialize_verification_receipt(
        repo_roots=[str(repo_b), str(repo_a)],
        proof_command_signature=("pytest -q tests/test_goal_release_state_machine.py",),
        proof_exit_code=0,
        proof_output_digest="all tests passed",
        governed_artifacts=[str(governed_doc)],
        verification_timestamp=now_ts,
    )

    # Frozen dataclass contract check
    with pytest.raises(FrozenInstanceError):
        receipt.proof_exit_code = 1  # type: ignore[misc]

    # Canonical sorted repo_roots
    assert len(receipt.repo_roots) == 2
    assert list(receipt.repo_roots) == sorted(receipt.repo_roots)
    assert len(receipt.commit_shas) == 2
    assert len(receipt.tree_sha) == 64
    assert len(receipt.porcelain_baseline_digest) == 64
    assert verify_receipt_parity(receipt, now=now_ts + 10.0) is True

    # Promote ACTIVE -> VERIFIED -> RELEASE_READY via GoalState.promote_with_receipt
    state = GoalState(goal="Multi-repo goal")
    assert state.phase == "ACTIVE"
    promoted = state.promote_with_receipt(receipt, gates_passed=True, now=now_ts + 10.0)
    assert promoted is True
    assert state.phase == "RELEASE_READY"
    assert state.active_verification_receipt == receipt
    assert state.last_verified_receipt == receipt

    # JSON round-trip preserves phase and receipts
    restored = GoalState.from_json(state.to_json())
    assert restored.phase == "RELEASE_READY"
    assert restored.active_verification_receipt == receipt
    assert restored.last_verified_receipt == receipt

    # 1. TTL expiry (> 3600.0s) invalidates receipt parity and resets state to ACTIVE
    assert verify_receipt_parity(receipt, now=now_ts + VERIFICATION_RECEIPT_TTL_S + 1.0) is False
    phase_ttl = on_new_user_prompt_authorize_release(
        restored,
        "commit and push",
        now=now_ts + VERIFICATION_RECEIPT_TTL_S + 1.0,
    )
    assert phase_ttl == "ACTIVE"
    assert restored.phase == "ACTIVE"
    assert restored.active_verification_receipt is None
    assert restored.last_verified_receipt is None

    # 2. Tracked file modification in repo_b invalidates parity and unlinks both receipts
    (repo_b / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert compute_multi_repo_digest(receipt.repo_roots) != receipt.porcelain_baseline_digest
    assert verify_receipt_parity(receipt, now=now_ts + 20.0) is False
    phase_drift = on_new_user_prompt_authorize_release(state, "commit and push", now=now_ts + 20.0)
    assert phase_drift == "ACTIVE"
    assert state.phase == "ACTIVE"
    assert state.active_verification_receipt is None
    assert state.last_verified_receipt is None

    # Restore tracked file; parity recovers for the raw receipt
    (repo_b / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert verify_receipt_parity(receipt, now=now_ts + 25.0) is True

    # 3. Untracked file creation in repo_a invalidates parity
    untracked = repo_a / "untracked_temp.txt"
    untracked.write_text("stray file\n", encoding="utf-8")
    assert verify_receipt_parity(receipt, now=now_ts + 30.0) is False
    untracked.unlink()
    assert verify_receipt_parity(receipt, now=now_ts + 35.0) is True

    # 4. Governed non-Git artifact drift invalidates parity
    governed_doc.write_text("# Section 13 modified\n", encoding="utf-8")
    assert verify_receipt_parity(receipt, now=now_ts + 40.0) is False
    governed_doc.write_text("# Section 13\n", encoding="utf-8")
    assert verify_receipt_parity(receipt, now=now_ts + 45.0) is True

    # 5. Branch / HEAD SHA change invalidates parity
    _git(repo_a, "commit", "--allow-empty", "-m", "advance head")
    assert verify_receipt_parity(receipt, now=now_ts + 50.0) is False

    # 6. Non-zero proof_exit_code receipt never passes parity
    failed_receipt = serialize_verification_receipt(
        repo_roots=[str(repo_a)],
        proof_command_signature=("pytest -q",),
        proof_exit_code=1,
        proof_output_digest="1 failed",
        verification_timestamp=now_ts,
    )
    assert verify_receipt_parity(failed_receipt, now=now_ts + 5.0) is False


def test_goal_manager_evaluate_after_turn_promotes_and_emits_release_ready_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GoalManager.evaluate_after_turn() transitions ACTIVE -> VERIFIED -> RELEASE_READY when
    _check_gates() is None and verify_receipt_parity(active_verification_receipt) == True,
    emitting CONTINUATION_PROMPT_RELEASE_READY_TEMPLATE."""
    import hermes_cli.goals as goals_mod

    # Re-exports in hermes_cli.goals must be identical to hermes_cli.goal_release
    assert goals_mod.VerificationReceipt is VerificationReceipt
    assert goals_mod.serialize_verification_receipt is serialize_verification_receipt
    assert goals_mod.verify_receipt_parity is verify_receipt_parity
    assert goals_mod.release_action_message is release_action_message
    assert goals_mod.on_new_user_prompt_authorize_release is on_new_user_prompt_authorize_release
    assert goals_mod.CONTINUATION_PROMPT_RELEASE_READY_TEMPLATE == CONTINUATION_PROMPT_RELEASE_READY_TEMPLATE

    monkeypatch.setattr(goals_mod, "load_goal", lambda _sid: None)
    monkeypatch.setattr(goals_mod, "save_goal", lambda _sid, _state: None)
    monkeypatch.setattr(
        goals_mod,
        "judge_goal",
        lambda *_a, **_kw: ("continue", "ready to package release", False, None, False),
    )

    repo = _init_git_repo(tmp_path, "repo_mgr")
    receipt = serialize_verification_receipt(
        repo_roots=[str(repo)],
        proof_command_signature=("pytest -q",),
        proof_exit_code=0,
        proof_output_digest="all pass",
    )

    mgr = GoalManager("sess-slice3")
    state = mgr.set("Implement and release Slice 3")
    state.record_verification_receipt(receipt)
    assert state.phase == "ACTIVE"

    decision = mgr.evaluate_after_turn("All unit tests pass.")
    assert decision["should_continue"] is True
    assert state.phase == "RELEASE_READY"
    assert state.active_verification_receipt == receipt
    assert state.last_verified_receipt == receipt
    assert f"PHASE: RELEASE_READY (receipt={receipt.tree_sha[:12]})" in decision["continuation_prompt"]

    # New non-release user prompt via GoalManager.authorize_new_user_prompt resets to ACTIVE
    assert mgr.authorize_new_user_prompt("explain the design") == "ACTIVE"
    assert state.phase == "ACTIVE"
    assert state.active_verification_receipt is None

    # Affirmative release command re-promotes to RELEASE_READY via cached last_verified_receipt
    assert mgr.authorize_new_user_prompt("commit and push") == "RELEASE_READY"
    assert state.phase == "RELEASE_READY"
    assert state.active_verification_receipt == receipt
