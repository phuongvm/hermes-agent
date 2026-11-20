"""Task 3.4 tests for verified-handle bounded prerequisite capture (D3a)."""
from __future__ import annotations

import errno
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
from threading import Barrier, Event
from types import SimpleNamespace
from typing import Any

import pytest

from agent import delegation_context as dc, tool_executor as executor
from agent.prerequisite import catalog, guard, reader, reservations
from tests.tools.proposal_catalog_fixture import CHANGE, proposal_entry


SCOPE: dict[str, Any] = dict(
    actor_session_id="actor",
    parent_session_id="parent",
    goal="Write proposal.md.",
    context=None,
)


def _pin_for_file(key: str, path: Path, *, max_file_bytes: int | None = None) -> dict[str, Any]:
    data = path.read_bytes()
    info = path.lstat()
    cap = max_file_bytes if max_file_bytes is not None else max(1, min(catalog.MAX_FILE_BYTES, max(512, len(data))))
    return {
        "path": str(path),
        "device": info.st_dev,
        "inode": info.st_ino,
        "size": len(data),
        "max_file_bytes": cap,
        "sha256": sha256(data).hexdigest(),
    }


@pytest.fixture
def env_fixture(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    planning = tmp_path / "planning"
    entry = proposal_entry(planning)
    agents_md = planning / "rules" / "AGENTS.md"
    secret_tok = "sk-antigravitySecretToken1234567890abcdef"
    agents_bytes = (
        f"# Governance Header\nLine two content\nAPI_TOKEN={secret_tok}\nLine four tail\n"
    ).encode()
    agents_md.write_bytes(agents_bytes)
    entry["resources"]["governance/AGENTS.md"] = _pin_for_file(
        "governance/AGENTS.md", agents_md, max_file_bytes=512
    )
    selection = catalog.freeze_proposal_catalog(CHANGE, entry)
    monkeypatch.setattr(dc, "child_proposal_selection", lambda *_a, **_kw: selection)
    return SimpleNamespace(
        root=planning,
        home=home,
        entry=entry,
        selection=selection,
        target=agents_md,
        secret_tok=secret_tok,
    )


def test_executor_dispatches_verified_bounded_read_with_cache_eviction_and_output_cap(
    env_fixture, monkeypatch
):
    target = env_fixture.target
    args_p1 = {"path": str(target), "offset": 1, "limit": 2}
    args_p2 = {"path": str(target), "offset": 3, "limit": 2}

    agent = SimpleNamespace(
        session_id="actor",
        _parent_session_id="parent",
        _current_turn_id="turn",
        tool_progress_callback=None,
        _tool_guardrails=SimpleNamespace(before_call=lambda *_: SimpleNamespace(allows_execution=True)),
    )
    monkeypatch.setattr(executor, "_begin_tool_execution", lambda *_: None)
    monkeypatch.setattr(executor, "_emit_terminal_post_tool_call", lambda *_a, **_kw: None)

    def reserve_in_hook(agent_obj, ref):
        assert guard.reserve_progress_read(
            agent_obj.session_id,
            dc.current_task_intent("actor", "parent").assignment_id,
            ref.call_id,
            ref.name,
        ) == "reserved"
        return None, ref.args

    monkeypatch.setattr(executor, "_pre_tool_block", reserve_in_hook)

    def legacy_forbidden(_args):
        pytest.fail("Allowance-backed read_file must never call ordinary/legacy reader")

    with dc.native_task_intent(**SCOPE) as intent:
        assert reservations.start_preparation(intent, "actor", 8, 120)

        with reader.measure_physical_reads() as m1:
            state1 = executor._ManagedToolResult(None, dict(args_p1), [], False, False)
            ref1 = executor._ToolCallRef("read_file", dict(args_p1), "task-1", "call-p1", [])
            out1 = executor._dispatch_authorized_once(
                agent, state1, ref1, execute=legacy_forbidden, scope_block=None,
                display_index=None, begin_execution=None, authorization_gate=None,
            )
        assert not state1.blocked
        assert reader.active_handle_count() == 0
        assert m1.bytes_read == target.stat().st_size
        payload1 = json.loads(out1)
        assert "1: # Governance Header" in payload1["content"]
        assert payload1["truncated"] is True
        snap1 = reservations.accounting_snapshot("actor", "parent", intent.assignment_id)
        assert snap1 is not None
        assert snap1["operations_reserved"] == 1
        assert snap1["source_bytes_reserved"] == 512

        # Second page hits frozen assignment cache: consumes an operation slot, 0 physical OS reads, 0 new byte reservation.
        with reader.measure_physical_reads() as m2:
            state2 = executor._ManagedToolResult(None, dict(args_p2), [], False, False)
            ref2 = executor._ToolCallRef("read_file", dict(args_p2), "task-1", "call-p2", [])
            out2 = executor._dispatch_authorized_once(
                agent, state2, ref2, execute=legacy_forbidden, scope_block=None,
                display_index=None, begin_execution=None, authorization_gate=None,
            )
        assert not state2.blocked
        assert m2.bytes_read == 0
        assert m2.opens == 0
        payload2 = json.loads(out2)
        assert env_fixture.secret_tok not in out2
        assert "API_TOKEN=" in payload2["content"]
        snap2 = reservations.accounting_snapshot("actor", "parent", intent.assignment_id)
        assert snap2["operations_reserved"] == 2
        assert snap2["source_bytes_reserved"] == 512

        # Evict cached buffer: next read must re-reserve max_file_bytes without refunding prior 512 bytes.
        assert reservations.evict_cached_resources(intent, "actor", "governance/AGENTS.md") == 1
        with reader.measure_physical_reads() as m3:
            state3 = executor._ManagedToolResult(None, dict(args_p1), [], False, False)
            ref3 = executor._ToolCallRef("read_file", dict(args_p1), "task-1", "call-p3", [])
            out3 = executor._dispatch_authorized_once(
                agent, state3, ref3, execute=legacy_forbidden, scope_block=None,
                display_index=None, begin_execution=None, authorization_gate=None,
            )
        assert not state3.blocked
        assert m3.bytes_read == target.stat().st_size
        snap3 = reservations.accounting_snapshot("actor", "parent", intent.assignment_id)
        assert snap3["operations_reserved"] == 3
        assert snap3["source_bytes_reserved"] == 1024
        assert json.loads(out3)["total_lines"] == 4


def test_output_ceiling_cannot_be_raised_above_256_kib_and_can_be_lowered(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    cfg = home / "config.yaml"
    cfg.write_text("file_read_max_chars: 10000000\ntool_output:\n  max_bytes: 10000000\n", encoding="utf-8")
    planning = tmp_path / "planning"
    entry = proposal_entry(planning)
    big_doc = planning / "rules" / "AGENTS.md"
    line = ("A" * 240 + "\n").encode("utf-8")
    big_bytes = line * 2000  # ~482 KiB <= 1 MiB source cap, > 256 KiB output cap
    big_doc.write_bytes(big_bytes)
    entry["resources"]["governance/AGENTS.md"] = _pin_for_file(
        "governance/AGENTS.md", big_doc, max_file_bytes=catalog.MAX_FILE_BYTES
    )
    selection = catalog.freeze_proposal_catalog(CHANGE, entry)
    monkeypatch.setattr(dc, "child_proposal_selection", lambda *_a, **_kw: selection)

    with dc.native_task_intent(**SCOPE) as intent:
        assert reservations.start_preparation(intent, "actor", 6, 60)
        args = {"path": str(big_doc), "offset": 1, "limit": 2000}
        assert reservations.reserve_read(intent, "actor", "big-1", "read_file", args) == "reserved"
        raw_json, err_type = reader.dispatch_bounded_read_file(intent, "actor", "big-1", args, task_id="t")
        assert err_type is None
        encoded_len = len(raw_json.encode("utf-8"))
        assert 150_000 < encoded_len <= catalog.MAX_OUTPUT_BYTES
        parsed = json.loads(raw_json)
        assert parsed["truncated"] is True

        # Lowering file_read_max_chars in profile config lowers returned output ceiling accordingly.
        cfg.write_text("file_read_max_chars: 4096\n", encoding="utf-8")
        args_low = {"path": str(big_doc), "offset": 2, "limit": 2000}
        assert reservations.reserve_read(intent, "actor", "big-2", "read_file", args_low) == "reserved"
        raw_low, err_low = reader.dispatch_bounded_read_file(intent, "actor", "big-2", args_low, task_id="t")
        assert err_low is None
        assert len(raw_low.encode("utf-8")) <= 4096
        assert json.loads(raw_low)["truncated"] is True


@pytest.mark.parametrize("tamper", ["oversize", "replaced_inode", "inplace_same_size"])
def test_oversized_replaced_or_modified_resource_reads_zero_mismatched_or_replacement_bytes(
    env_fixture, tmp_path, tamper
):
    target = env_fixture.target
    args = {"path": str(target), "offset": 1, "limit": 50}
    with dc.native_task_intent(**SCOPE) as intent:
        assert reservations.start_preparation(intent, "actor", 4, 60)
        assert reservations.reserve_read(intent, "actor", f"call-{tamper}", "read_file", args) == "reserved"

        # Mutate AFTER reservation (bypassing pre-reservation metadata check via mock or post-reserve swap)
        # Note: begin_verified_read re-runs resolve_operation which calls verify_catalog_metadata unless
        # tamper happens between metadata preflight and handle open! Test BOTH direct reader seam & race seam!
        def swap_before_open():
            if tamper == "oversize":
                target.write_bytes(b"X" * 2048)  # exceeds max_file_bytes=512
            elif tamper == "replaced_inode":
                replacement = tmp_path / "replacement.md"
                replacement.write_bytes(b"R" * target.stat().st_size)
                os.replace(replacement, target)
            elif tamper == "inplace_same_size":
                orig_len = target.stat().st_size
                with open(target, "r+b") as fh:
                    fh.seek(0)
                    fh.write(b"Z" * orig_len)

        with reader.measure_physical_reads() as metrics:
            with pytest.raises(reader.BoundedReadError) as exc_info:
                reader.capture_descriptor_buffers(
                    intent, "actor", f"call-{tamper}", "read_file", args,
                    on_before_open=swap_before_open,
                )
        assert reader.active_handle_count() == 0
        if tamper in {"oversize", "replaced_inode"}:
            assert metrics.bytes_read == 0
        else:
            assert exc_info.value.code in {"digest_mismatch", "resource_changed_during_capture"}
        # Reservation and byte budget are NOT refunded on failure.
        snap = reservations.accounting_snapshot("actor", "parent", intent.assignment_id)
        assert snap["operations_reserved"] == 1
        assert snap["source_bytes_reserved"] == 512
        # Verify Windows handle was closed cleanly (file is writable and removable).
        target.write_bytes(b"cleanup-writable\n")
        assert target.exists()


def test_growth_during_capture_never_exceeds_reservation_and_releases_nothing(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    planning = tmp_path / "planning"
    entry = proposal_entry(planning)
    target = planning / "rules" / "AGENTS.md"
    initial = b"A" * 128
    target.write_bytes(initial)
    entry["resources"]["governance/AGENTS.md"] = _pin_for_file(
        "governance/AGENTS.md", target, max_file_bytes=128
    )
    selection = catalog.freeze_proposal_catalog(CHANGE, entry)
    monkeypatch.setattr(dc, "child_proposal_selection", lambda *_a, **_kw: selection)

    args = {"path": str(target), "offset": 1, "limit": 10}
    with dc.native_task_intent(**SCOPE) as intent:
        assert reservations.start_preparation(intent, "actor", 2, 60)
        assert reservations.reserve_read(intent, "actor", "grow-1", "read_file", args) == "reserved"

        # Simulate concurrent growth on the opened stream via chunk hook / oversized stream adapter
        grown_stream = [b"A" * 100, b"B" * 200]
        requested_budgets: list[int] = []

        def grow_read(_fd, max_n):
            requested_budgets.append(max_n)
            if not grown_stream:
                return b""
            chunk = grown_stream.pop(0)
            if len(chunk) > max_n:
                grown_stream.insert(0, chunk[max_n:])
                return chunk[:max_n]
            return chunk

        with reader.measure_physical_reads() as metrics:
            with pytest.raises(reader.BoundedReadError) as exc_info:
                reader.capture_descriptor_buffers(
                    intent, "actor", "grow-1", "read_file", args,
                    read_override=grow_read,
                )
        assert exc_info.value.code in {"resource_changed_during_capture", "digest_mismatch"}
        assert metrics.bytes_read == 128
        assert requested_budgets == [128, 28]
        # Unread tail proves no extra 1-byte EOF probe ran beyond the 128-byte reservation!
        assert grown_stream == [b"B" * 172]
        assert reader.active_handle_count() == 0

    # Even when the first 128 bytes match the pinned SHA-256, post-read fstat growth rejects with 0 extra reads.
    same_prefix_pin = catalog.ResourcePin(
        key="governance/AGENTS.md",
        path="/srv/governance/AGENTS.md",
        device=7,
        inode=11,
        size=128,
        max_file_bytes=128,
        sha256=sha256(initial).hexdigest(),
    )
    fstat_calls = [0]
    read_calls: list[int] = []

    def grow_fstat(fd: int):
        if fd == 10:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_dev=7, st_ino=1, st_size=0, st_mtime_ns=1)
        fstat_calls[0] += 1
        cur_size = 128 if fstat_calls[0] == 1 else 328
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_dev=7, st_ino=11, st_size=cur_size, st_mtime_ns=1)

    def exact_prefix_read(_fd: int, max_n: int) -> bytes:
        read_calls.append(max_n)
        return initial[:max_n]

    ops_grow = reader.PosixHandleOps(
        open=lambda name, _f, dir_fd=None: 20 if name == "AGENTS.md" else 10,
        fstat=grow_fstat,
        read=exact_prefix_read,
        close=lambda _fd: None,
        o_rdonly=0,
        o_nofollow=1,
        o_nonblock=2,
        o_directory=4,
        o_cloexec=8,
    )
    with reader.measure_physical_reads() as m_pos:
        with pytest.raises(reader.BoundedReadError) as exc_pos:
            reader.capture_verified_pin(same_prefix_pin, posix_ops=ops_grow, skip_host_denylist=True)
    assert exc_pos.value.code == "resource_changed_during_capture"
    assert m_pos.bytes_read == 128
    assert read_calls == [128]
    assert reader.active_handle_count() == 0


def test_atomic_aggregate_byte_reservations_cap_at_16_mib(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    planning = tmp_path / "planning"
    entry = proposal_entry(planning)
    # Set 15 resources to max_file_bytes = 1 MiB each; remaining entries small -> <= 16 MiB total in catalog.
    # Then evict & re-read across 4 concurrent workers to compete for the final 1 MiB of aggregate 16 MiB budget!
    keys = sorted(k for k, v in entry["resources"].items() if v is not None)[:15]
    paths = []
    for key in keys:
        rel_or_abs = entry["resources"][key]["path"]
        p = Path(rel_or_abs) if Path(rel_or_abs).is_absolute() else (planning / rel_or_abs)
        entry["resources"][key]["max_file_bytes"] = catalog.MAX_FILE_BYTES
        paths.append((key, p))
    for key, row in list(entry["resources"].items()):
        if row is not None and key not in keys:
            p = Path(row["path"]) if Path(row["path"]).is_absolute() else (planning / row["path"])
            row["max_file_bytes"] = max(256, p.stat().st_size)
    selection = catalog.freeze_proposal_catalog(CHANGE, entry)
    monkeypatch.setattr(dc, "child_proposal_selection", lambda *_a, **_kw: selection)

    with dc.native_task_intent(**SCOPE) as intent:
        assert reservations.start_preparation(intent, "actor", 32, 120)
        # Consume 15 MiB across the 15 resources (leaving < 1 MiB of the 16 MiB ceiling once small pins or 1 more 1 MiB runs).
        for idx, (_key, p) in enumerate(paths):
            c_args = {"path": str(p), "offset": 1, "limit": 5}
            cid = f"warm-{idx}"
            assert reservations.reserve_read(intent, "actor", cid, "read_file", c_args) == "reserved"
            captured = reader.capture_descriptor_buffers(intent, "actor", cid, "read_file", c_args)
            assert len(captured.buffers) == 1

        snap = reservations.accounting_snapshot("actor", "parent", intent.assignment_id)
        assert snap["source_bytes_reserved"] == 15 * catalog.MAX_FILE_BYTES

        # Evict 4 of the 1 MiB resources and race 4 threads for the remaining 1 MiB of aggregate budget!
        evicted = [paths[i] for i in range(4)]
        for key, _p in evicted:
            assert reservations.evict_cached_resources(intent, "actor", key) == 1

        barrier = Barrier(4, timeout=10)

        def race_capture(idx: int) -> str:
            _key, p = evicted[idx]
            c_args = {"path": str(p), "offset": 1, "limit": 5}
            cid = f"race-{idx}"
            assert reservations.reserve_read(intent, "actor", cid, "read_file", c_args) == "reserved"
            barrier.wait()
            try:
                reader.capture_descriptor_buffers(intent, "actor", cid, "read_file", c_args)
                return "ok"
            except reader.BoundedReadError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(copy_context().run, race_capture, i) for i in range(4)]
            outcomes = [job.result(timeout=15) for job in jobs]
        assert outcomes.count("ok") == 1
        assert outcomes.count("source_byte_budget_exhausted") == 3
        final_snap = reservations.accounting_snapshot("actor", "parent", intent.assignment_id)
        assert final_snap["source_bytes_reserved"] == catalog.MAX_SOURCE_BYTES


@pytest.mark.parametrize("bad_path", [
    r"\??\C:\Windows\win.ini",
    r"\\.\NUL",
    r"\\?\UNC\attacker.example\share\file.md",
    r"\\?\GLOBALROOT\Device\HarddiskVolume1\file.md",
    r"\\attacker.example\share\file.md",
    "//attacker.example/share/file.md",
    "/dev/zero",
    "/proc/self/environ",
])
def test_unsafe_device_namespace_and_unc_paths_rejected_with_zero_reads(bad_path):
    fake_pin = catalog.ResourcePin(
        key="governance/AGENTS.md",
        path=bad_path,
        device=1,
        inode=1,
        size=10,
        max_file_bytes=64,
        sha256="a" * 64,
    )
    with reader.measure_physical_reads() as metrics:
        with pytest.raises(reader.BoundedReadError) as exc_info:
            reader.capture_verified_pin(fake_pin)
    assert exc_info.value.code in {"unsafe_path", "denied_path"}
    assert metrics.bytes_read == 0
    assert metrics.opens == 0
    assert reader.active_handle_count() == 0


def test_denylist_blocks_pinned_credentials_with_zero_reads(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    auth_file = home / "auth.json"
    auth_file.write_bytes(b'{"token":"secret"}\n')
    st = auth_file.lstat()
    auth_pin = catalog.ResourcePin(
        key="governance/AGENTS.md",
        path=str(auth_file),
        device=st.st_dev,
        inode=st.st_ino,
        size=st.st_size,
        max_file_bytes=256,
        sha256=sha256(auth_file.read_bytes()).hexdigest(),
    )
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_bytes(b"OPENAI_API_KEY=sk-1234567890abcdef123456\n")
    st_env = dotenv_file.lstat()
    dotenv_pin = catalog.ResourcePin(
        key="governance/AGENTS.md",
        path=str(dotenv_file),
        device=st_env.st_dev,
        inode=st_env.st_ino,
        size=st_env.st_size,
        max_file_bytes=256,
        sha256=sha256(dotenv_file.read_bytes()).hexdigest(),
    )
    for pin in (auth_pin, dotenv_pin):
        with reader.measure_physical_reads() as metrics:
            with pytest.raises(reader.BoundedReadError) as exc_info:
                reader.capture_verified_pin(pin)
        assert exc_info.value.code == "denied_path"
        assert metrics.bytes_read == 0
        assert metrics.opens == 0
        assert reader.active_handle_count() == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows junction/reparse verification")
def test_windows_reparse_junction_rejected_without_reading_content(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    secret_file = real_dir / "target.md"
    secret_bytes = b"reparse-secret-payload\n"
    secret_file.write_bytes(secret_bytes)
    real_stat = secret_file.lstat()

    junction_dir = tmp_path / "junction_dir"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction_dir), str(real_dir)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    try:
        via_junction = junction_dir / "target.md"
        pin = catalog.ResourcePin(
            key="governance/AGENTS.md",
            path=str(via_junction),
            device=real_stat.st_dev,
            inode=real_stat.st_ino,
            size=real_stat.st_size,
            max_file_bytes=256,
            sha256=sha256(secret_bytes).hexdigest(),
        )
        with reader.measure_physical_reads() as metrics:
            with pytest.raises(reader.BoundedReadError) as exc_info:
                reader.capture_verified_pin(pin)
        assert exc_info.value.code == "reparse_or_symlink"
        assert metrics.bytes_read == 0
        assert reader.active_handle_count() == 0
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(junction_dir)], capture_output=True, check=False)


def test_posix_descriptor_traversal_enforces_nofollow_nonblock_and_closes_all_fds():
    content = b"verified posix buffer\n"
    digest = sha256(content).hexdigest()
    pin = catalog.ResourcePin(
        key="governance/AGENTS.md",
        path="/srv/governance/rules/AGENTS.md",
        device=42,
        inode=99,
        size=len(content),
        max_file_bytes=256,
        sha256=digest,
    )

    def make_posix_ops(*, symlink_part: str | None = None, leaf_mode: int = stat.S_IFREG | 0o644):
        next_fd = [10]
        open_fds: dict[int, tuple[str, int]] = {}
        calls: list[tuple[str, int, int | None]] = []
        bytes_read = [0]

        O_NOFOLLOW = 0x20000
        O_NONBLOCK = 0x00800
        O_DIRECTORY = 0x10000
        O_CLOEXEC = 0x80000

        def fake_open(name: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            calls.append((name, flags, dir_fd))
            if dir_fd is not None and dir_fd not in open_fds:
                raise OSError(errno.EBADF, "closed dir_fd")
            if name != "/" and not (flags & O_NOFOLLOW):
                raise AssertionError(f"Expected O_NOFOLLOW for {name}")
            if name == symlink_part:
                raise OSError(errno.ELOOP, "Too many levels of symbolic links")
            fd = next_fd[0]
            next_fd[0] += 1
            open_fds[fd] = (name, flags)
            return fd

        def fake_fstat(fd: int):
            if fd not in open_fds:
                raise OSError(errno.EBADF, "bad fd")
            name, _flags = open_fds[fd]
            if name == "AGENTS.md":
                if not (_flags & O_NONBLOCK):
                    raise AssertionError("Expected O_NONBLOCK on leaf open")
                return SimpleNamespace(
                    st_mode=leaf_mode, st_dev=42, st_ino=99, st_size=len(content), st_mtime_ns=123456
                )
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_dev=42, st_ino=fd, st_size=0, st_mtime_ns=1)

        sent = [False]

        def fake_read(fd: int, n: int) -> bytes:
            if fd not in open_fds:
                raise OSError(errno.EBADF, "bad fd")
            if sent[0]:
                return b""
            sent[0] = True
            chunk = content[:n]
            bytes_read[0] += len(chunk)
            return chunk

        def fake_close(fd: int) -> None:
            if fd not in open_fds:
                raise OSError(errno.EBADF, "double close")
            del open_fds[fd]

        ops = reader.PosixHandleOps(
            open=fake_open, fstat=fake_fstat, read=fake_read, close=fake_close,
            o_rdonly=0, o_nofollow=O_NOFOLLOW, o_nonblock=O_NONBLOCK,
            o_directory=O_DIRECTORY, o_cloexec=O_CLOEXEC,
        )
        return ops, open_fds, calls, bytes_read

    # 1. Symlink directory component rejected with 0 content bytes and 0 leaked fds.
    ops_sym, open_fds_sym, _calls_sym, read_sym = make_posix_ops(symlink_part="rules")
    with pytest.raises(reader.BoundedReadError) as exc_sym:
        reader.capture_verified_pin(pin, posix_ops=ops_sym, skip_host_denylist=True)
    assert exc_sym.value.code == "reparse_or_symlink"
    assert read_sym[0] == 0
    assert open_fds_sym == {}

    # 2. Nonblocking open of FIFO leaf rejected at fstat with 0 content bytes and 0 leaked fds.
    ops_fifo, open_fds_fifo, _calls_fifo, read_fifo = make_posix_ops(leaf_mode=stat.S_IFIFO | 0o600)
    with pytest.raises(reader.BoundedReadError) as exc_fifo:
        reader.capture_verified_pin(pin, posix_ops=ops_fifo, skip_host_denylist=True)
    assert exc_fifo.value.code == "unsafe_path"
    assert read_fifo[0] == 0
    assert open_fds_fifo == {}

    # 3. Valid regular file succeeds via descriptor-relative chain and closes every fd.
    ops_ok, open_fds_ok, calls_ok, read_ok = make_posix_ops()
    captured = reader.capture_verified_pin(pin, posix_ops=ops_ok, skip_host_denylist=True)
    assert captured == content
    assert read_ok[0] == len(content)
    assert open_fds_ok == {}
    assert [name for name, _, _ in calls_ok] == ["/", "srv", "governance", "rules", "AGENTS.md"]


def test_changed_parameters_and_revocation_before_and_in_flight(env_fixture):
    target = env_fixture.target
    args = {"path": str(target), "offset": 1, "limit": 10}

    # 1. Changed parameters after reservation -> rejected with 0 physical reads.
    with dc.native_task_intent(**SCOPE) as intent:
        assert reservations.start_preparation(intent, "actor", 4, 60)
        assert reservations.reserve_read(intent, "actor", "chg-1", "read_file", args) == "reserved"
        with reader.measure_physical_reads() as m_chg:
            raw_chg, err_chg = reader.dispatch_bounded_read_file(
                intent, "actor", "chg-1", {**args, "limit": 20}, task_id="t"
            )
        assert err_chg == "prerequisite_parameter_mismatch"
        assert m_chg.bytes_read == 0
        assert m_chg.opens == 0
        assert "mismatch" in json.loads(raw_chg)["error"].lower()

    # 2. Revocation BEFORE dispatch linearization -> 0 opens, 0 reads, disposition="closed", in_flight=False.
    with dc.native_task_intent(**SCOPE) as intent2:
        assert reservations.start_preparation(intent2, "actor", 4, 60)
        assert reservations.reserve_read(intent2, "actor", "rev-pre", "read_file", args) == "reserved"
        dc.revoke_task_intent(intent2)
        with reader.measure_physical_reads() as m_pre:
            raw_pre, err_pre = reader.dispatch_bounded_read_file(
                intent2, "actor", "rev-pre", args, task_id="t"
            )
        assert err_pre == "prerequisite_closed"
        body_pre = json.loads(raw_pre)
        assert body_pre["disposition"] == "closed"
        assert body_pre["in_flight"] is False
        assert m_pre.bytes_read == 0
        assert m_pre.opens == 0

    # 3. Revocation IN FLIGHT (after linearization while handle is open) -> honest in_flight=True reporting, handle closed.
    opened, release = Event(), Event()
    with dc.native_task_intent(**SCOPE) as intent3:
        assert reservations.start_preparation(intent3, "actor", 4, 60)
        assert reservations.reserve_read(intent3, "actor", "rev-mid", "read_file", args) == "reserved"

        def pause_mid_capture():
            opened.set()
            assert release.wait(10)

        with ThreadPoolExecutor(max_workers=2) as pool:
            worker = pool.submit(
                copy_context().run,
                lambda: reader.dispatch_bounded_read_file(
                    intent3, "actor", "rev-mid", args, task_id="t", on_after_open=pause_mid_capture
                ),
            )
            try:
                assert opened.wait(10)
                snap_mid = reservations.preparation_snapshot_for_intent(intent3)
                assert snap_mid is not None and snap_mid["in_flight_count"] == 1
                dc.revoke_task_intent(intent3)
            finally:
                release.set()
            raw_mid, err_mid = worker.result(timeout=10)

        assert reader.active_handle_count() == 0
        assert err_mid == "prerequisite_revoked_in_flight"
        body_mid = json.loads(raw_mid)
        assert body_mid["disposition"] == "revoked_in_flight"
        assert body_mid["in_flight"] is True
        receipts = reservations.assignment_receipts(intent3)
        assert any(
            r.get("tool_call_id") == "rev-mid"
            and r.get("disposition") == "revoked_in_flight"
            and r.get("in_flight") is True
            for r in receipts
        )


def test_tenant_sandbox_and_remote_backend_rejected_without_fallback(env_fixture, monkeypatch):
    from tools import file_tools_paths
    from tools.environments import sandbox_scope

    target = env_fixture.target
    args = {"path": str(target), "offset": 1, "limit": 10}

    # 1. Remote SSH backend cannot verify host handles -> rejected without fallback or host open.
    monkeypatch.setattr(file_tools_paths, "_terminal_env_type_for_task", lambda _tid="default": "ssh")
    with dc.native_task_intent(**SCOPE) as intent:
        assert reservations.start_preparation(intent, "actor", 4, 60)
        assert reservations.reserve_read(intent, "actor", "ssh-1", "read_file", args) == "reserved"
        with reader.measure_physical_reads() as m_ssh:
            raw_ssh, err_ssh = reader.dispatch_bounded_read_file(intent, "actor", "ssh-1", args, task_id="t-ssh")
        assert err_ssh == "prerequisite_unsupported_backend"
        assert m_ssh.opens == 0
        assert m_ssh.bytes_read == 0
        assert json.loads(raw_ssh)["disposition"] == "unsupported_backend"

    # 2. Tenant hosted_scope active -> rejected even when terminal backend appears local.
    monkeypatch.setattr(file_tools_paths, "_terminal_env_type_for_task", lambda _tid="default": "local")
    monkeypatch.setattr(sandbox_scope, "hosted_scope", lambda: object())
    with dc.native_task_intent(**SCOPE) as intent2:
        assert reservations.start_preparation(intent2, "actor", 4, 60)
        assert reservations.reserve_read(intent2, "actor", "tenant-1", "read_file", args) == "reserved"
        with reader.measure_physical_reads() as m_tenant:
            raw_t, err_t = reader.dispatch_bounded_read_file(intent2, "actor", "tenant-1", args, task_id="t-tenant")
        assert err_t == "prerequisite_unsupported_backend"
        assert m_tenant.opens == 0
        assert m_tenant.bytes_read == 0
        assert json.loads(raw_t)["disposition"] == "unsupported_backend"

    # 3. Missing host backend verification module (#31 advisory #1) -> fails closed unsupported_backend.
    import builtins

    real_import = builtins.__import__

    def block_backend_import(name, *a, **kw):
        if name in {"tools.file_tools_paths", "tools.environments"}:
            raise ImportError("missing host backend module")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", block_backend_import)
    with dc.native_task_intent(**SCOPE) as intent3:
        assert reservations.start_preparation(intent3, "actor", 4, 60)
        assert reservations.reserve_read(intent3, "actor", "imp-err", "read_file", args) == "reserved"
        with reader.measure_physical_reads() as m_imp:
            raw_imp, err_imp = reader.dispatch_bounded_read_file(intent3, "actor", "imp-err", args, task_id="t-imp")
        assert err_imp == "prerequisite_unsupported_backend"
        assert m_imp.opens == 0
        assert m_imp.bytes_read == 0
        assert json.loads(raw_imp)["disposition"] == "unsupported_backend"


def _dummy_agent(monkeypatch) -> SimpleNamespace:
    monkeypatch.setattr(executor, "_begin_tool_execution", lambda *_: None)
    monkeypatch.setattr(executor, "_emit_terminal_post_tool_call", lambda *_a, **_kw: None)
    return SimpleNamespace(
        session_id="actor",
        _parent_session_id="parent",
        _current_turn_id="turn",
        tool_progress_callback=None,
        _tool_guardrails=SimpleNamespace(before_call=lambda *_: SimpleNamespace(allows_execution=True)),
    )


def test_skill_view_adapter_uses_frozen_buffers_blocks_inline_shell_and_undeclared_files(tmp_path, monkeypatch):
    from agent.prerequisite import adapters
    from hermes_cli import plugins

    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    # Enable inline_shell and extreme output limits in config to prove neither escapes the adapter.
    (home / "config.yaml").write_text(
        "file_read_max_chars: 10000000\ntool_output:\n  max_bytes: 10000000\nskills:\n  inline_shell: true\n  template_vars: true\n",
        encoding="utf-8",
    )

    planning = tmp_path / "planning"
    entry = proposal_entry(planning)
    secret_tok = "sk-skillSecretToken1234567890abcdef"
    skill_md = planning / "skills" / "verification-before-claim" / "SKILL.md"
    ref_md = planning / "skills" / "verification-before-claim" / "references" / "completion-gate.md"
    # Drop an UNDECLARED file in references/ to prove zero directory scan/listing.
    undeclared_md = planning / "skills" / "verification-before-claim" / "references" / "undeclared-secret.md"
    undeclared_md.write_text("UNDECLARED_FILE_MUST_NOT_BE_SCANNED\n", encoding="utf-8")

    skill_body = (
        "---\n"
        "name: verification-before-claim\n"
        "description: Verify before claiming completion\n"
        "tags: [verification, governance]\n"
        "---\n"
        "Dir=${HERMES_SKILL_DIR} Session=${HERMES_SESSION_ID}\n"
        "ShellProbe=!`echo SHELL_MUST_NEVER_RUN`\n"
        f"CREDENTIAL={secret_tok}\n"
    ).encode()
    ref_body = f"# Completion Gate\nGate reference body {secret_tok}\n".encode()
    skill_md.write_bytes(skill_body)
    ref_md.write_bytes(ref_body)
    entry["resources"]["skill/verification-before-claim"] = _pin_for_file(
        "skill/verification-before-claim", skill_md, max_file_bytes=2048
    )
    entry["resources"]["reference/verification-before-claim/references/completion-gate.md"] = _pin_for_file(
        "reference/verification-before-claim/references/completion-gate.md", ref_md, max_file_bytes=1024
    )
    selection = catalog.freeze_proposal_catalog(CHANGE, entry)
    monkeypatch.setattr(dc, "child_proposal_selection", lambda *_a, **_kw: selection)
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: pytest.fail("No subprocess may spawn during skill_view adapter")
    )

    manager = plugins.PluginManager(str(home))
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)
    agent = _dummy_agent(monkeypatch)

    def attach_reserve():
        def _hook(**kw):
            assert guard.reserve_progress_read(
                "actor", intent.assignment_id, kw["tool_call_id"], kw["tool_name"]
            ) == "reserved"

        manager._hooks["pre_tool_call"] = [_hook]

    with dc.native_task_intent(**SCOPE) as intent:
        assert reservations.start_preparation(intent, "actor", 8, 60)
        attach_reserve()

        # 1. View SKILL.md -> reads SKILL.md once, expands template vars, does NOT execute !`...`,
        #    redacts secret, and lists ONLY catalog-declared references (ignoring undeclared-secret.md).
        args_main = {"name": "verification-before-claim"}
        with reader.measure_physical_reads() as m1:
            state1 = executor._ManagedToolResult(None, dict(args_main), [], False, False)
            ref1 = executor._ToolCallRef("skill_view", dict(args_main), "t-sk", "sk-1", [])
            out1 = executor._dispatch_authorized_once(
                agent, state1, ref1,
                execute=lambda _a: pytest.fail("Legacy skill_view forbidden"),
                scope_block=None, display_index=None, begin_execution=None, authorization_gate=None,
            )
        assert not state1.blocked
        assert m1.bytes_read == len(skill_body)
        parsed1 = json.loads(out1)
        assert parsed1["success"] is True
        assert parsed1["name"] == "verification-before-claim"
        assert "Session=actor" in parsed1["content"]
        assert "!`echo SHELL_MUST_NEVER_RUN`" in parsed1["content"]
        assert "SHELL_MUST_NEVER_RUN\n" not in parsed1["content"]
        assert secret_tok not in parsed1["content"]
        assert parsed1["linked_files"] == {"references": ["references/completion-gate.md"]}
        assert "undeclared-secret.md" not in out1
        snap1 = reservations.accounting_snapshot("actor", "parent", intent.assignment_id)
        assert snap1["source_bytes_reserved"] == 2048

        # 2. View declared reference -> SKILL.md served from assignment cache (0 rescan bytes!),
        #    only completion-gate.md opened & read, reserving +1024 bytes.
        args_ref = {"name": "verification-before-claim", "file_path": "references/completion-gate.md"}
        with reader.measure_physical_reads() as m2:
            state2 = executor._ManagedToolResult(None, dict(args_ref), [], False, False)
            ref2 = executor._ToolCallRef("skill_view", dict(args_ref), "t-sk", "sk-2", [])
            out2 = executor._dispatch_authorized_once(
                agent, state2, ref2,
                execute=lambda _a: pytest.fail("Legacy skill_view forbidden"),
                scope_block=None, display_index=None, begin_execution=None, authorization_gate=None,
            )
        assert not state2.blocked
        assert m2.bytes_read == len(ref_body)
        parsed2 = json.loads(out2)
        assert parsed2["success"] is True
        assert parsed2["file"] == "references/completion-gate.md"
        assert "Gate reference body" in parsed2["content"]
        assert secret_tok not in parsed2["content"]
        snap2 = reservations.accounting_snapshot("actor", "parent", intent.assignment_id)
        assert snap2["operations_reserved"] == 2
        assert snap2["source_bytes_reserved"] == 2048 + 1024

    # 3. Skill declaring external `deps: [ffmpeg]` is rejected fail-closed as undeclared_dependency.
    deps_md = planning / "skills" / "codebase-design" / "SKILL.md"
    deps_bytes = b"---\nname: codebase-design\ndeps: [ffmpeg]\n---\nNeeds external tool.\n"
    deps_md.write_bytes(deps_bytes)
    entry["resources"]["skill/codebase-design"] = _pin_for_file(
        "skill/codebase-design", deps_md, max_file_bytes=512
    )
    sel_deps = catalog.freeze_proposal_catalog(CHANGE, entry)
    monkeypatch.setattr(dc, "child_proposal_selection", lambda *_a, **_kw: sel_deps)
    with dc.native_task_intent(**SCOPE) as intent_deps:
        assert reservations.start_preparation(intent_deps, "actor", 4, 60)
        assert reservations.reserve_read(
            intent_deps, "actor", "sk-deps", "skill_view", {"name": "codebase-design"}
        ) == "reserved"
        raw_deps, err_deps = adapters.dispatch_bounded_prerequisite(
            intent_deps, "actor", "sk-deps", "skill_view", {"name": "codebase-design"}, task_id="t-sk"
        )
        assert err_deps == "prerequisite_undeclared_dependency"
        assert json.loads(raw_deps)["disposition"] == "undeclared_dependency"


def test_openspec_read_adapters_use_frozen_buffers_without_cli_or_rescan_and_cap_output(tmp_path, monkeypatch):
    from agent.prerequisite import adapters
    from hermes_cli import plugins

    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENSPEC_BIN", str(tmp_path / "fake-openspec-cli"))
    (home / "config.yaml").write_text("file_read_max_chars: 10000000\n", encoding="utf-8")

    planning = tmp_path / "planning"
    entry = proposal_entry(planning)
    secret_tok = "sk-openspecSecretToken1234567890abcdef"

    # Populate realistic JSON snapshots and OpenSpec markdown baselines.
    status_path = planning / f"openspec/workspace/prerequisites/{CHANGE}/status.json"
    status_bytes = json.dumps({
        "changeName": CHANGE,
        "schemaName": "spec-driven",
        "artifacts": [{"id": "proposal", "status": "ready"}],
        "note": f"token={secret_tok}",
    }).encode()
    status_path.write_bytes(status_bytes)
    entry["resources"]["snapshot/status"] = {
        **_pin_for_file("snapshot/status", status_path, max_file_bytes=1024),
        "path": f"openspec/workspace/prerequisites/{CHANGE}/status.json",
    }

    instr_path = planning / f"openspec/workspace/prerequisites/{CHANGE}/instructions/proposal.json"
    big_guidance = ("Guidance line " + ("X" * 200) + "\n") * 1600  # ~344 KiB > 256 KiB output cap
    instr_bytes = json.dumps({
        "artifactId": "proposal",
        "instruction": big_guidance,
        "secret": secret_tok,
    }).encode()
    instr_path.write_bytes(instr_bytes)
    entry["resources"]["snapshot/instructions/proposal"] = {
        **_pin_for_file("snapshot/instructions/proposal", instr_path, max_file_bytes=catalog.MAX_FILE_BYTES),
        "path": f"openspec/workspace/prerequisites/{CHANGE}/instructions/proposal.json",
    }

    spec1_rel = "openspec/specs/nerve-crew-dod-and-role-handoff/spec.md"
    spec1_path = planning / spec1_rel
    spec1_bytes = (
        "# Nerve Crew DoD Specification\n\n"
        "## Purpose\nEnsure verified handoffs.\n\n"
        "## Requirements\n\n"
        "### Requirement: Evidence gate\nEvery handoff SHALL carry raw proof.\n\n"
        "#### Scenario: Proof attached\n"
        "- **WHEN** coder finishes\n"
        f"- **THEN** proof is verified without leaking {secret_tok}\n"
    ).encode()
    spec1_path.write_bytes(spec1_bytes)
    entry["resources"]["baseline/nerve-crew-dod-and-role-handoff"] = {
        **_pin_for_file("baseline/nerve-crew-dod-and-role-handoff", spec1_path, max_file_bytes=2048),
        "path": spec1_rel,
    }

    spec2_rel = "openspec/specs/nerve-reflex-resilience/spec.md"
    spec2_path = planning / spec2_rel
    spec2_bytes = spec2_path.read_bytes()

    # Drop an UNDECLARED baseline spec directory on disk to prove openspec_spec_list never scans disk!
    undeclared_spec = planning / "openspec/specs/undeclared-rogue-spec/spec.md"
    undeclared_spec.parent.mkdir(parents=True, exist_ok=True)
    undeclared_spec.write_text("# Rogue Spec\n", encoding="utf-8")

    selection = catalog.freeze_proposal_catalog(CHANGE, entry)
    monkeypatch.setattr(dc, "child_proposal_selection", lambda *_a, **_kw: selection)
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: pytest.fail("No OpenSpec CLI subprocess may spawn")
    )

    manager = plugins.PluginManager(str(home))
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)
    agent = _dummy_agent(monkeypatch)

    with dc.native_task_intent(**SCOPE) as intent:
        assert reservations.start_preparation(intent, "actor", 10, 60)
        manager._hooks["pre_tool_call"] = [
            lambda **kw: (
                None
                if guard.reserve_progress_read("actor", intent.assignment_id, kw["tool_call_id"], kw["tool_name"])
                == "reserved"
                else {"action": "block", "message": "not reserved"}
            )
        ]

        def run_tool(tool_name: str, tool_args: dict[str, Any], cid: str) -> tuple[str, Any]:
            st = executor._ManagedToolResult(None, dict(tool_args), [], False, False)
            rf = executor._ToolCallRef(tool_name, dict(tool_args), "t-os", cid, [])
            with reader.measure_physical_reads() as m:
                out = executor._dispatch_authorized_once(
                    agent, st, rf,
                    execute=lambda _a: pytest.fail(f"Legacy {tool_name} handler forbidden"),
                    scope_block=None, display_index=None, begin_execution=None, authorization_gate=None,
                )
            assert not st.blocked
            return out, m

        # 1. openspec_status
        out_status, m_status = run_tool("openspec_status", {"workdir": str(planning), "change": CHANGE}, "os-1")
        assert m_status.bytes_read == len(status_bytes)
        p_status = json.loads(out_status)
        assert p_status["ok"] is True
        assert p_status["stdout"]["changeName"] == CHANGE
        assert secret_tok not in out_status

        # 2. openspec_instructions (exceeds 256 KiB source -> output strictly capped <= MAX_OUTPUT_BYTES!)
        out_instr, m_instr = run_tool(
            "openspec_instructions",
            {"workdir": str(planning), "change": CHANGE, "artifact": "proposal"},
            "os-2",
        )
        assert m_instr.bytes_read == len(instr_bytes)
        assert len(out_instr.encode()) <= catalog.MAX_OUTPUT_BYTES
        p_instr = json.loads(out_instr)
        assert p_instr["ok"] is True
        assert p_instr["truncated"] is True
        assert secret_tok not in out_instr

        # 3. openspec_spec_list -> reads BOTH pinned baselines, ignores undeclared-rogue-spec!
        out_list, m_list = run_tool("openspec_spec_list", {"workdir": str(planning)}, "os-3")
        assert m_list.bytes_read == len(spec1_bytes) + len(spec2_bytes)
        p_list = json.loads(out_list)
        assert p_list["ok"] is True
        assert p_list["specs"] == ["nerve-crew-dod-and-role-handoff", "nerve-reflex-resilience"]
        assert "undeclared-rogue-spec" not in out_list

        # 4. openspec_spec_show -> reuses cached buffer from openspec_spec_list (0 physical bytes read!),
        #    still consumes an operation slot, and parses Requirements & Scenarios cleanly!
        out_show, m_show = run_tool(
            "openspec_spec_show",
            {"workdir": str(planning), "spec": "nerve-crew-dod-and-role-handoff"},
            "os-4",
        )
        assert m_show.bytes_read == 0
        assert m_show.opens == 0
        p_show = json.loads(out_show)
        assert p_show["ok"] is True
        assert p_show["title"] == "Nerve Crew DoD"
        assert p_show["purpose"] == "Ensure verified handoffs."
        assert len(p_show["requirements"]) == 1
        assert p_show["requirements"][0]["name"] == "Evidence gate"
        assert len(p_show["requirements"][0]["scenarios"]) == 1
        assert secret_tok not in out_show

        snap = reservations.accounting_snapshot("actor", "parent", intent.assignment_id)
        assert snap["operations_reserved"] == 4

    # 5. Snapshot reporting ok=False cannot trigger _template_instruction_fallback or CLI probe!
    bad_instr = planning / f"openspec/workspace/prerequisites/{CHANGE}/instructions/tasks.json"
    bad_bytes = json.dumps({"ok": False, "error": "No changes found"}).encode()
    bad_instr.write_bytes(bad_bytes)
    entry["resources"]["snapshot/instructions/tasks"] = {
        **_pin_for_file("snapshot/instructions/tasks", bad_instr, max_file_bytes=512),
        "path": f"openspec/workspace/prerequisites/{CHANGE}/instructions/tasks.json",
    }
    sel_bad = catalog.freeze_proposal_catalog(CHANGE, entry)
    monkeypatch.setattr(dc, "child_proposal_selection", lambda *_a, **_kw: sel_bad)
    with dc.native_task_intent(**SCOPE) as intent_bad:
        assert reservations.start_preparation(intent_bad, "actor", 2, 60)
        bad_args = {"workdir": str(planning), "change": CHANGE, "artifact": "tasks"}
        assert reservations.reserve_read(intent_bad, "actor", "os-bad", "openspec_instructions", bad_args) == "reserved"
        raw_bad, err_bad = adapters.dispatch_bounded_prerequisite(
            intent_bad, "actor", "os-bad", "openspec_instructions", bad_args, task_id="t-os"
        )
        assert err_bad == "prerequisite_unavailable_snapshot"
        assert json.loads(raw_bad)["disposition"] == "unavailable_snapshot"


def test_skill_view_policy_module_error_fails_closed(tmp_path, monkeypatch):
    import sys
    import tools
    from agent.prerequisite import adapters

    home = tmp_path / "home"
    planning = tmp_path / "planning"
    home.mkdir()
    entry = proposal_entry(planning)
    selection = catalog.freeze_proposal_catalog(CHANGE, entry)
    monkeypatch.setattr(dc, "child_proposal_selection", lambda *_a, **_kw: selection)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delattr(tools, "skills_tool", raising=False)
    monkeypatch.setitem(sys.modules, "tools.skills_tool", None)

    with dc.native_task_intent(**SCOPE) as intent:
        assert reservations.start_preparation(intent, "actor", 2, 60)
        args = {"name": "openspec-propose"}
        assert reservations.reserve_read(intent, "actor", "sk-mod-err", "skill_view", args) == "reserved"
        raw, err = adapters.dispatch_bounded_prerequisite(
            intent, "actor", "sk-mod-err", "skill_view", args, task_id="t-w1"
        )
        assert err == "prerequisite_unsupported_backend"
        assert json.loads(raw)["disposition"] == "unsupported_backend"
