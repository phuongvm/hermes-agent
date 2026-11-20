"""Subagent failure lines gloss the classified `failure_reason` instead of echoing the child's raw
exception, and always name the next step (/agents). Contract tests, not snapshots.
"""

from tools.delegate_tool_progress import describe_subagent_failure, format_subagent_failure_line


def test_classified_reason_replaces_raw_exception_text():
    line = format_subagent_failure_line(
        "research pricing", "failed", error="Error code: 429 - {'error': 'rate limited'}",
        duration_seconds=12, failure_reason="rate_limit")
    assert "rate-limited" in line
    assert "Error code" not in line and "429" not in line
    assert "/agents" in line


def test_unclassified_failure_keeps_the_cleaned_error_as_cause():
    assert describe_subagent_failure(None, "Traceback...\nKeyError: 'x'") == "KeyError: 'x'"
    line = format_subagent_failure_line("g", "failed", error="KeyError: 'x'")
    assert "KeyError: 'x'" in line and "/agents" in line


def test_timeout_line_names_outcome_and_the_timeout_setting_without_duplicating_duration():
    raw = "Subagent timed out after 600s with 4 API call(s) completed — likely stuck on a slow API call."
    line = format_subagent_failure_line("scan the repo", "timeout", error=raw, duration_seconds=600)
    assert "delegation.child_timeout_seconds" in line
    assert line.count("600") == 0 and "10 min" in line
    assert "API call" not in line


def test_tier_floor_violation_and_invalid_params_preserve_typed_reason_and_exact_summary():
    from types import SimpleNamespace
    from agent.tier_floor_policy import TierFloorViolationError
    from tools.delegate_tool_child_run import _SchemaOutcome, _build_result_entry

    child = SimpleNamespace(
        model="cb-agent-reviewer",
        session_prompt_tokens=10,
        session_completion_tokens=0,
        session_estimated_cost_usd=0.0,
        session_cost_status="known",
        _delegate_role="leaf",
    )
    tier_err = TierFloorViolationError(
        model_id="gpt-5.6-sol", profile_model="cb-agent-reviewer", reason="body model rejected"
    )
    child._last_api_error = tier_err
    masked_result = {
        "final_response": "⚠️ No reply: the model provider didn't answer after all retries.",
        "error": "⚠️ No reply: the model provider didn't answer after all retries.",
        "failed": True,
        "completed": False,
        "api_calls": 3,
        "failure_reason": "server_error",
        "messages": [],
    }
    entry = _build_result_entry(child, masked_result, 0, 15.0, _SchemaOutcome(None, None, [], 0))
    assert entry["status"] == "failed"
    assert entry["failure_reason"] == "tier_floor_violation"
    assert "gpt-5.6-sol" in entry["error"]
    assert "didn't answer after all retries" not in entry["error"]

    line = format_subagent_failure_line(
        "review PR",
        entry["status"],
        error=entry["error"],
        duration_seconds=15,
        failure_reason=entry["failure_reason"],
    )
    assert "tier_floor_violation" in line
    assert "gpt-5.6-sol" in line
    assert "internal error" not in line
    assert "didn't answer after all retries" not in line

    # invalid_params wrapped in non-retryable copy keeps typed reason + exact Provider said detail.
    child._last_api_error = None
    params_result = {
        "final_response": "AI Router rejected this request as malformed.\n\nProvider said: invalid_params: Unknown field 'foo'",
        "error": "AI Router rejected this request as malformed.\n\nProvider said: invalid_params: Unknown field 'foo'",
        "failed": True,
        "completed": False,
        "api_calls": 1,
        "failure_reason": "format_error",
        "messages": [],
    }
    params_entry = _build_result_entry(child, params_result, 1, 2.0, _SchemaOutcome(None, None, [], 0))
    assert params_entry["failure_reason"] == "invalid_params"
    assert "Unknown field 'foo'" in params_entry["error"]
    params_desc = describe_subagent_failure(params_entry["failure_reason"], params_entry["error"])
    assert "invalid_params" in params_desc
    assert "Unknown field 'foo'" in params_desc
