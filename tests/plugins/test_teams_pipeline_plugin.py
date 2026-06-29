"""Tests for the Teams pipeline plugin package."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from pathlib import Path

import pytest

from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from plugins.teams_pipeline import register
from plugins.teams_pipeline.pipeline import TeamsMeetingPipeline
from plugins.teams_pipeline.store import TeamsPipelineStore
from plugins.teams_pipeline.models import MeetingArtifact, TeamsMeetingRef, TeamsMeetingSummaryPayload


class FakeGraphClient:
    def __init__(self) -> None:
        self.downloaded = False


async def _transcript_meeting_resolver(client, *, meeting_id=None, join_web_url=None, tenant_id=None):
    from plugins.teams_pipeline.models import TeamsMeetingRef

    return TeamsMeetingRef(
        meeting_id=str(meeting_id),
        tenant_id=tenant_id,
        metadata={"subject": "Weekly Sync", "participants": [{"displayName": "Ada"}]},
    )


async def _no_call_record(*args, **kwargs):
    return None


def test_register_adds_cli_only():
    mgr = PluginManager()
    manifest = PluginManifest(name="teams_pipeline")
    ctx = PluginContext(manifest, mgr)

    register(ctx)

    assert "teams-pipeline" in mgr._cli_commands
    entry = mgr._cli_commands["teams-pipeline"]
    assert entry["plugin"] == "teams_pipeline"
    assert callable(entry["setup_fn"])
    assert callable(entry["handler_fn"])


def test_runtime_config_uses_existing_teams_platform_settings():
    from plugins.teams_pipeline.runtime import build_pipeline_runtime_config

    gateway_config = GatewayConfig(
        platforms={
            Platform("teams"): PlatformConfig(
                enabled=True,
                extra={
                    "delivery_mode": "graph",
                    "team_id": "team-1",
                    "channel_id": "channel-1",
                    "meeting_pipeline": {
                        "transcript_min_chars": 120,
                        "notion": {"enabled": True, "database_id": "db-1"},
                    },
                },
            )
        }
    )

    runtime_config = build_pipeline_runtime_config(gateway_config)

    assert runtime_config["transcript_min_chars"] == 120
    assert runtime_config["notion"]["database_id"] == "db-1"
    assert runtime_config["teams_delivery"] == {
        "enabled": True,
        "mode": "graph",
        "team_id": "team-1",
        "channel_id": "channel-1",
    }


def test_build_pipeline_runtime_reuses_existing_teams_adapter_surface(monkeypatch, tmp_path):
    from plugins.teams_pipeline import runtime as runtime_module

    class FakeWriter:
        def __init__(self, platform_config=None, **kwargs) -> None:
            self.platform_config = platform_config

    monkeypatch.setattr(runtime_module, "build_graph_client", lambda: object())
    monkeypatch.setattr(runtime_module, "resolve_teams_pipeline_store_path", lambda: tmp_path / "teams-store.json")
    monkeypatch.setattr("plugins.platforms.teams.adapter.TeamsSummaryWriter", FakeWriter)

    gateway = SimpleNamespace(
        config=GatewayConfig(
            platforms={
                Platform("teams"): PlatformConfig(
                    enabled=True,
                    extra={
                        "delivery_mode": "incoming_webhook",
                        "incoming_webhook_url": "https://example.com/hook",
                    },
                )
            }
        )
    )

    runtime = runtime_module.build_pipeline_runtime(gateway)

    assert isinstance(runtime.teams_sender, FakeWriter)
    assert runtime.teams_sender.platform_config is gateway.config.platforms[Platform("teams")]


@pytest.mark.anyio
async def test_build_pipeline_runtime_notifies_review_target_via_delivery_router(monkeypatch, tmp_path):
    from plugins.teams_pipeline import runtime as runtime_module
    from plugins.teams_pipeline.models import TeamsMeetingPipelineJob

    deliveries = []

    class FakeDeliveryRouter:
        async def deliver(self, content, targets, metadata=None, **kwargs):
            deliveries.append(
                {
                    "content": content,
                    "targets": [target.to_string() for target in targets],
                    "metadata": metadata,
                }
            )
            return {"matrix:!review:example.org": {"success": True}}

    monkeypatch.setattr(runtime_module, "build_graph_client", lambda: object())
    monkeypatch.setattr("plugins.platforms.teams.adapter.TeamsSummaryWriter", object)
    monkeypatch.setattr(runtime_module, "resolve_teams_pipeline_store_path", lambda: tmp_path / "teams-store.json")

    gateway = SimpleNamespace(
        config=GatewayConfig(
            platforms={
                Platform("teams"): PlatformConfig(enabled=False),
                Platform.MATRIX: PlatformConfig(
                    enabled=True,
                    home_channel=HomeChannel(
                        platform=Platform.MATRIX,
                        chat_id="!review:example.org",
                        name="Review",
                    ),
                ),
            }
        ),
        delivery_router=FakeDeliveryRouter(),
    )
    runtime = runtime_module.build_pipeline_runtime(gateway)
    job = TeamsMeetingPipelineJob(
        job_id="teams-job-review",
        event_id="event-review",
        source_event_type="graph.notification",
        dedupe_key="event-review",
        status="pending_review",
    )
    payload = TeamsMeetingSummaryPayload(
        meeting_ref=TeamsMeetingRef(meeting_id="meeting-review"),
        title="Review me",
    )

    await runtime._notify_pending_review(job, payload)

    assert deliveries
    assert deliveries[0]["targets"] == ["matrix:!review:example.org"]
    assert "teams-job-review" in deliveries[0]["content"]
    assert deliveries[0]["metadata"]["teams_pipeline_status"] == "pending_review"


def test_default_summary_template_lives_under_skills_templates():
    from plugins.teams_pipeline.pipeline import DEFAULT_TEMPLATE_PATH, _load_summary_template

    repo_root = Path(__file__).resolve().parents[2]
    template_path = Path(DEFAULT_TEMPLATE_PATH).resolve()

    assert template_path.is_relative_to(repo_root / "skills" / "templates")
    assert template_path.is_file()
    assert _load_summary_template() is not None


@pytest.mark.anyio
async def test_pending_review_notifier_is_injected(tmp_path):
    from plugins.teams_pipeline.models import TeamsMeetingPipelineJob

    notifications = []

    async def _notify(job, payload):
        notifications.append((job.job_id, payload.title))

    pipeline = TeamsMeetingPipeline(
        graph_client=FakeGraphClient(),
        store=TeamsPipelineStore(tmp_path / "teams-store.json"),
        pending_review_notifier=_notify,
    )
    job = TeamsMeetingPipelineJob(
        job_id="teams-job-review",
        event_id="event-review",
        source_event_type="graph.notification",
        dedupe_key="event-review",
        status="pending_review",
    )
    payload = TeamsMeetingSummaryPayload(
        meeting_ref=TeamsMeetingRef(meeting_id="meeting-review"),
        title="Review me",
    )

    await pipeline._notify_pending_review(job, payload)

    assert notifications == [("teams-job-review", "Review me")]


@pytest.mark.anyio
async def test_bind_gateway_runtime_attaches_scheduler(monkeypatch, tmp_path):
    from plugins.teams_pipeline import runtime as runtime_module

    class FakeAdapter:
        def __init__(self) -> None:
            self.scheduler = None

        def set_notification_scheduler(self, scheduler) -> None:
            self.scheduler = scheduler

    class FakePipeline:
        def __init__(self) -> None:
            self.notifications = []

        async def run_notification(self, notification):
            self.notifications.append(notification)

    adapter = FakeAdapter()
    pipeline = FakePipeline()
    gateway = SimpleNamespace(
        adapters={Platform.MSGRAPH_WEBHOOK: adapter},
        config=GatewayConfig(platforms={}),
        _teams_pipeline_runtime=None,
        _teams_pipeline_runtime_error=None,
    )

    monkeypatch.setattr(runtime_module, "build_pipeline_runtime", lambda gateway_runner: pipeline)

    bound = runtime_module.bind_gateway_runtime(gateway)

    assert bound is True
    assert gateway._teams_pipeline_runtime is pipeline
    assert callable(adapter.scheduler)

    notification = {"id": "notif-1"}
    await adapter.scheduler(notification, object())
    assert pipeline.notifications == [notification]


@pytest.mark.anyio
async def test_bind_gateway_runtime_drops_notifications_when_unavailable(monkeypatch):
    from plugins.teams_pipeline import runtime as runtime_module
    from tools.microsoft_graph_auth import MicrosoftGraphConfigError

    class FakeAdapter:
        def __init__(self) -> None:
            self.scheduler = None

        def set_notification_scheduler(self, scheduler) -> None:
            self.scheduler = scheduler

    adapter = FakeAdapter()
    gateway = SimpleNamespace(
        adapters={Platform.MSGRAPH_WEBHOOK: adapter},
        config=GatewayConfig(platforms={}),
        _teams_pipeline_runtime=None,
        _teams_pipeline_runtime_error=None,
    )

    def _raise(_gateway_runner):
        raise MicrosoftGraphConfigError("missing graph env")

    monkeypatch.setattr(runtime_module, "build_pipeline_runtime", _raise)

    bound = runtime_module.bind_gateway_runtime(gateway)

    assert bound is False
    assert "missing graph env" in gateway._teams_pipeline_runtime_error
    assert callable(adapter.scheduler)
    await adapter.scheduler({"id": "notif-2"}, object())


def test_store_persists_subscription_event_and_job_state(tmp_path):
    store_path = tmp_path / "teams-store.json"
    store = TeamsPipelineStore(store_path)
    store.upsert_subscription(
        "sub-1",
        {"client_state": "abc", "resource": "communications/onlineMeetings"},
    )
    store.record_event_timestamp("evt-1", "2026-05-03T19:30:00Z")
    store.upsert_job("job-1", {"status": "received", "event_id": "evt-1"})
    store.upsert_sink_record("notion:meeting-1", {"page_id": "page-1"})

    reloaded = TeamsPipelineStore(store_path)
    subscription = reloaded.get_subscription("sub-1")
    job = reloaded.get_job("job-1")
    sink = reloaded.get_sink_record("notion:meeting-1")

    assert subscription is not None
    assert subscription["subscription_id"] == "sub-1"
    assert subscription["client_state"] == "abc"
    assert reloaded.get_event_timestamp("evt-1") == "2026-05-03T19:30:00Z"
    assert job is not None
    assert job["status"] == "received"
    assert sink is not None
    assert sink["page_id"] == "page-1"


def test_store_notification_receipts_are_idempotent(tmp_path):
    store = TeamsPipelineStore(tmp_path / "teams-store.json")
    notification = {
        "subscriptionId": "sub-1",
        "resource": "communications/onlineMeetings/meeting-1",
        "changeType": "updated",
    }
    receipt_key = TeamsPipelineStore.build_notification_receipt_key(notification)

    assert store.record_notification_receipt(receipt_key, notification) is True
    assert store.record_notification_receipt(receipt_key, notification) is False
    assert store.has_notification_receipt(receipt_key) is True

    reloaded = TeamsPipelineStore(tmp_path / "teams-store.json")
    assert reloaded.has_notification_receipt(receipt_key) is True


@pytest.mark.anyio
class TestTeamsMeetingPipeline:
    async def test_transcript_first_path_persists_state_and_skips_recording(self, tmp_path, monkeypatch):
        from plugins.teams_pipeline import pipeline as pipeline_module

        monkeypatch.setattr(pipeline_module, "resolve_meeting_reference", _transcript_meeting_resolver)

        async def _fetch_transcript(client, meeting_ref):
            return (
                MeetingArtifact(artifact_type="transcript", artifact_id="tx-1", display_name="meeting.vtt"),
                "Action: Send draft by Friday.\nDecision: Ship the transcript-first path.\nDetailed transcript content.",
            )

        async def _call_record(client, meeting_ref, *, call_record_id=None, allow_permission_errors=True):
            return MeetingArtifact(
                artifact_type="call_record",
                artifact_id="call-1",
                metadata={"metrics": {"participant_count": 4}},
            )

        async def _summarize(**kwargs):
            return pipeline_module.TeamsMeetingSummaryPayload(
                meeting_ref=kwargs["resolved_meeting"],
                title="Weekly Sync",
                transcript_text=kwargs["transcript_text"],
                summary="Short summary",
                key_decisions=["Ship the transcript-first path."],
                action_items=["Send draft by Friday."],
                risks=["Timeline risk."],
                confidence="high",
                confidence_notes="Transcript available.",
                source_artifacts=kwargs["artifacts"],
            )

        monkeypatch.setattr(pipeline_module, "fetch_preferred_transcript_text", _fetch_transcript)
        monkeypatch.setattr(pipeline_module, "enrich_meeting_with_call_record", _call_record)

        store = TeamsPipelineStore(tmp_path / "teams-store.json")
        pipeline = TeamsMeetingPipeline(
            graph_client=FakeGraphClient(),
            store=store,
            config={"transcript_min_chars": 20},
            summarize_fn=_summarize,
        )

        job = await pipeline.run_notification(
            {
                "id": "notif-1",
                "changeType": "updated",
                "resource": "communications/onlineMeetings/meeting-123",
                "resourceData": {"id": "meeting-123"},
            }
        )

        assert job.status == "completed"
        assert job.selected_artifact_strategy == "transcript_first"
        assert job.summary_payload is not None
        assert job.summary_payload.summary == "Short summary"
        stored = store.get_job(job.job_id)
        assert stored is not None
        assert stored["status"] == "completed"

    async def test_recording_fallback_uses_stt_and_updates_sink_records(self, tmp_path, monkeypatch):
        from plugins.teams_pipeline import pipeline as pipeline_module

        monkeypatch.setattr(pipeline_module, "resolve_meeting_reference", _transcript_meeting_resolver)

        async def _no_transcript(client, meeting_ref):
            return None, None

        async def _recordings(client, meeting_ref):
            return [
                MeetingArtifact(
                    artifact_type="recording",
                    artifact_id="rec-1",
                    display_name="recording.mp4",
                    download_url="https://files.example/recording.mp4",
                )
            ]

        async def _download(client, meeting_ref, recording, destination):
            target = Path(destination)
            target.write_bytes(b"video-bytes")
            return {"path": str(target), "size_bytes": 11, "content_type": "video/mp4"}

        async def _prepare_audio(self, recording_path):
            audio_path = recording_path.with_suffix(".wav")
            audio_path.write_bytes(b"audio-bytes")
            return audio_path

        def _transcribe(file_path, model):
            return {"success": True, "transcript": "Action: Follow up with Legal.\nRisk: Budget approval pending.", "provider": "local"}

        async def _summarize(**kwargs):
            return pipeline_module.TeamsMeetingSummaryPayload(
                meeting_ref=kwargs["resolved_meeting"],
                title="Weekly Sync",
                transcript_text=kwargs["transcript_text"],
                summary="Fallback summary",
                key_decisions=[],
                action_items=["Follow up with Legal."],
                risks=["Budget approval pending."],
                confidence="medium",
                confidence_notes="Generated from STT fallback.",
                source_artifacts=kwargs["artifacts"],
            )

        class FakeNotionWriter:
            async def write_summary(self, payload, config, existing_record=None):
                return {"page_id": existing_record.get("page_id") if existing_record else "page-1", "url": "https://notion.so/page-1"}

        async def _teams_sender(payload, config, existing_record=None):
            return {"message_id": existing_record.get("message_id") if existing_record else "msg-1"}

        monkeypatch.setattr(pipeline_module, "fetch_preferred_transcript_text", _no_transcript)
        monkeypatch.setattr(pipeline_module, "list_recording_artifacts", _recordings)
        monkeypatch.setattr(pipeline_module, "download_recording_artifact", _download)
        monkeypatch.setattr(pipeline_module.TeamsMeetingPipeline, "_prepare_audio_path", _prepare_audio)
        monkeypatch.setattr(pipeline_module, "enrich_meeting_with_call_record", _no_call_record)

        store = TeamsPipelineStore(tmp_path / "teams-store.json")
        pipeline = TeamsMeetingPipeline(
            graph_client=FakeGraphClient(),
            store=store,
            config={
                "notion": {"enabled": True, "database_id": "db-1"},
                "teams_delivery": {"enabled": True, "channel_id": "channel-1"},
            },
            transcribe_fn=_transcribe,
            summarize_fn=_summarize,
            notion_writer=FakeNotionWriter(),
            teams_sender=_teams_sender,
        )

        job = await pipeline.run_notification(
            {
                "id": "notif-2",
                "changeType": "updated",
                "resource": "communications/onlineMeetings/meeting-456",
                "resourceData": {"id": "meeting-456"},
            }
        )

        assert job.status == "completed"
        assert job.selected_artifact_strategy == "recording_stt_fallback"
        assert job.summary_payload is not None
        assert job.summary_payload.summary == "Fallback summary"
        notion_record = store.get_sink_record("notion:meeting-456")
        teams_record = store.get_sink_record("teams:meeting-456")
        assert notion_record is not None
        assert notion_record["page_id"] == "page-1"
        assert teams_record is not None
        assert teams_record["message_id"] == "msg-1"

    async def test_join_web_url_returns_pending_reference_when_graph_cannot_resolve(self, monkeypatch):
        from plugins.teams_pipeline import meetings as meetings_module

        class FakeClient:
            async def get_json(self, path, params=None, headers=None):
                raise meetings_module.MicrosoftGraphAPIError(
                    400,
                    "GET",
                    f"https://graph.microsoft.com/v1.0{path}",
                    "BadRequest",
                )

        ref = await meetings_module.resolve_meeting_reference(
            FakeClient(),
            join_web_url="https://teams.microsoft.com/meet/278515858493828?p=8FExxBj8D8N6Zxd1vu",
        )

        assert ref.join_web_url == "https://teams.microsoft.com/meet/278515858493828?p=8FExxBj8D8N6Zxd1vu"
        assert ref.metadata["pending_resolution"] is True
        assert ref.meeting_id == "278515858493828"

    async def test_chat_url_bridges_through_call_record_lookup(self, monkeypatch):
        from plugins.teams_pipeline import meetings as meetings_module

        chat_url = "https://teams.microsoft.com/l/chat/19:meeting_MGQ3MGMxNDQtOGU0ZC00Yzc0LTkxY2MtZjRhNDU3NTcxY2Q4@thread.v2/conversations?context=%7B%22contextType%22%3A%22chat%22%7D"
        call_join_url = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_MGQ3MGMxNDQtOGU0ZC00Yzc0LTkxY2MtZjRhNDU3NTcxY2Q4%40thread.v2/0?context=%7B%22Tid%22%3A%2219cd252c-b188-4d97-96dd-c74217c18f6d%22%2C%22Oid%22%3A%2229427ce2-dce0-42ce-b212-92a227f61979%22%7D"

        class FakeClient:
            async def collect_paginated(self, path):
                if path == "/communications/callRecords":
                    return [
                        {
                            "id": "call-record-1",
                            "joinWebUrl": call_join_url,
                            "organizer": {"user": {"id": "user-123"}},
                        }
                    ]
                raise AssertionError(f"Unexpected Graph call: {path}")

            async def get_json(self, path, params=None, headers=None):
                if path == "/communications/onlineMeetings":
                    raise meetings_module.MicrosoftGraphAPIError(
                        404,
                        "GET",
                        f"https://graph.microsoft.com/v1.0{path}",
                        "NotFound",
                    )
                expected_filter = "JoinWebUrl eq '{}'".format(call_join_url.replace("'", "''"))
                if path == "/users/user-123/onlineMeetings" and params == {"$filter": expected_filter}:
                    return {
                        "value": [
                            {
                                "id": "meeting-123",
                                "joinWebUrl": call_join_url,
                                "subject": "Weekly Sync",
                                "organizer": {"user": {"id": "user-123"}},
                                "participants": [{"displayName": "Ada"}],
                                "chatInfo": {"threadId": "19:meeting_MGQ3MGMxNDQtOGU0ZC00Yzc0LTkxY2MtZjRhNDU3NTcxY2Q4@thread.v2"},
                            }
                        ]
                    }
                raise AssertionError(f"Unexpected Graph call: {path} {params}")

        ref = await meetings_module.resolve_meeting_reference(FakeClient(), join_web_url=chat_url)

        assert ref.meeting_id == "meeting-123"
        assert ref.organizer_user_id == "user-123"
        assert ref.join_web_url == call_join_url
        assert ref.thread_id == "19:meeting_MGQ3MGMxNDQtOGU0ZC00Yzc0LTkxY2MtZjRhNDU3NTcxY2Q4@thread.v2"

    async def test_call_record_notification_bridges_past_online_meeting_lookup_error(self, monkeypatch):
        from plugins.teams_pipeline import meetings as meetings_module

        call_record_id = "call-record-1"
        call_join_url = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_callrecord@thread.v2/0?context=%7B%22Tid%22%3A%2219cd252c-b188-4d97-96dd-c74217c18f6d%22%2C%22Oid%22%3A%22user-123%22%7D"

        class FakeClient:
            async def get_json(self, path, params=None, headers=None):
                if path == f"/communications/onlineMeetings/{call_record_id}":
                    raise meetings_module.MicrosoftGraphAPIError(
                        400,
                        "GET",
                        f"https://graph.microsoft.com/v1.0{path}",
                        "BadRequest",
                    )
                if path == f"/communications/callRecords/{call_record_id}":
                    return {
                        "id": call_record_id,
                        "joinWebUrl": call_join_url,
                        "organizer": {"user": {"id": "user-123"}},
                    }
                # VTC filter may be tried first (Strategy 1) — expect it to fail
                if path == "/communications/onlineMeetings" and params and "$filter" in params:
                    if "VideoTeleconferenceId" in params["$filter"]:
                        raise meetings_module.MicrosoftGraphAPIError(
                            404, "GET", f"https://graph.microsoft.com/v1.0{path}", "NotFound"
                        )
                    # JoinWebUrl filter on /communications (Strategy 2) — also fails
                    if "JoinWebUrl eq" in params["$filter"]:
                        raise meetings_module.MicrosoftGraphAPIError(
                            400, "GET", f"https://graph.microsoft.com/v1.0{path}", "BadRequest"
                        )
                expected_filter = "JoinWebUrl eq '{}'".format(call_join_url.replace("'", "''"))
                if path == "/users/user-123/onlineMeetings" and params == {"$filter": expected_filter}:
                    return {
                        "value": [
                            {
                                "id": "meeting-123",
                                "joinWebUrl": call_join_url,
                                "organizer": {"user": {"id": "user-123"}},
                            }
                        ]
                    }
                raise AssertionError(f"Unexpected Graph call: {path} {params}")

        ref = await meetings_module.resolve_meeting_reference(FakeClient(), meeting_id=call_record_id)

        assert ref.meeting_id == "meeting-123"
        assert ref.organizer_user_id == "user-123"
        assert ref.join_web_url == call_join_url

    async def test_join_web_url_permission_errors_surface(self, monkeypatch):
        from plugins.teams_pipeline import meetings as meetings_module

        class FakeClient:
            async def get_json(self, path, params=None, headers=None):
                raise meetings_module.MicrosoftGraphAPIError(
                    403,
                    "GET",
                    f"https://graph.microsoft.com/v1.0{path}",
                    "Forbidden",
                )

        with pytest.raises(meetings_module.TeamsMeetingPermissionError):
            await meetings_module.resolve_meeting_reference(
                FakeClient(),
                join_web_url="https://teams.microsoft.com/meet/278515858493828?p=8FExxBj8D8N6Zxd1vu",
            )

    async def test_fetch_preferred_transcript_text_uses_resolved_meeting_for_download(self, monkeypatch):
        from plugins.teams_pipeline import meetings as meetings_module

        pending_ref = meetings_module.TeamsMeetingRef(
            meeting_id="278515858493828",
            join_web_url="https://teams.microsoft.com/meet/278515858493828?p=8FExxBj8D8N6Zxd1vu",
            tenant_id="tenant-1",
            metadata={"pending_resolution": True, "join_web_url": "https://teams.microsoft.com/meet/278515858493828?p=8FExxBj8D8N6Zxd1vu"},
        )
        resolved_ref = meetings_module.TeamsMeetingRef(
            meeting_id="meeting-123",
            organizer_user_id="user-123",
            join_web_url="https://teams.microsoft.com/l/meetup-join/real",
            tenant_id="tenant-1",
            metadata={"subject": "Weekly Sync"},
        )
        captured = {}

        async def _resolve_artifact_meeting_ref(client, meeting_ref):
            return resolved_ref

        async def _list_transcript_artifacts(client, meeting_ref):
            captured["listed_ref"] = meeting_ref.meeting_id
            return [
                MeetingArtifact(
                    artifact_type="transcript",
                    artifact_id="tx-1",
                    display_name="meeting.vtt",
                    download_url=None,
                )
            ]

        async def _download_transcript_text(
            client,
            meeting_ref,
            transcript,
            *,
            resolved_meeting_ref=None,
            encoding="utf-8",
        ):
            captured["download_ref"] = meeting_ref.meeting_id
            captured["resolved_download_ref"] = resolved_meeting_ref.meeting_id if resolved_meeting_ref else None
            return "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello world\n"

        monkeypatch.setattr(meetings_module, "_resolve_artifact_meeting_ref", _resolve_artifact_meeting_ref)
        monkeypatch.setattr(meetings_module, "list_transcript_artifacts", _list_transcript_artifacts)
        monkeypatch.setattr(meetings_module, "download_transcript_text", _download_transcript_text)

        transcript, text = await meetings_module.fetch_preferred_transcript_text(object(), pending_ref)

        assert transcript is not None
        assert text is not None
        assert captured["listed_ref"] == "meeting-123"
        assert captured["download_ref"] == "meeting-123"
        assert captured["resolved_download_ref"] == "meeting-123"

    async def test_download_recording_artifact_uses_resolved_meeting_for_download(self, tmp_path):
        from plugins.teams_pipeline import meetings as meetings_module

        captured = {}

        class FakeClient:
            async def download_to_file(self, path, destination):
                captured["path"] = path
                destination.write_text("binary", encoding="utf-8")
                return {"path": str(destination), "size_bytes": destination.stat().st_size, "content_type": "video/mp4"}

        pending_ref = meetings_module.TeamsMeetingRef(
            meeting_id="278515858493828",
            join_web_url="https://teams.microsoft.com/meet/278515858493828?p=8FExxBj8D8N6Zxd1vu",
            tenant_id="tenant-1",
            metadata={"pending_resolution": True, "join_web_url": "https://teams.microsoft.com/meet/278515858493828?p=8FExxBj8D8N6Zxd1vu"},
        )
        resolved_ref = meetings_module.TeamsMeetingRef(
            meeting_id="meeting-123",
            organizer_user_id="user-123",
            join_web_url="https://teams.microsoft.com/l/meetup-join/real",
            tenant_id="tenant-1",
            metadata={"subject": "Weekly Sync"},
        )
        recording = MeetingArtifact(
            artifact_type="recording",
            artifact_id="rec-1",
            display_name="meeting.mp4",
            download_url=None,
        )

        result = await meetings_module.download_recording_artifact(
            FakeClient(),
            pending_ref,
            recording,
            tmp_path / "out.mp4",
            resolved_meeting_ref=resolved_ref,
        )

        assert captured["path"] == "/users/user-123/onlineMeetings/meeting-123/recordings/rec-1/content"
        assert result["size_bytes"] > 0

    async def test_transcript_artifacts_use_organizer_specific_endpoint(self, tmp_path, monkeypatch):
        from plugins.teams_pipeline import meetings as meetings_module

        captured = {"paths": []}

        class FakeClient:
            async def collect_paginated(self, path):
                captured["paths"].append(path)
                return [
                    {
                        "id": "tx-1",
                        "displayName": "meeting.vtt",
                        "contentType": "text/vtt",
                        "downloadUrl": None,
                    }
                ]

            async def download_to_file(self, path, destination):
                captured["paths"].append(path)
                destination.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello world\n", encoding="utf-8")
                return {"path": str(destination), "size_bytes": destination.stat().st_size, "content_type": "text/vtt"}

        meeting_ref = meetings_module.TeamsMeetingRef(
            meeting_id="meeting-123",
            organizer_user_id="user-123",
            join_web_url="https://teams.microsoft.com/l/meetup-join/abc",
            tenant_id="tenant-1",
            metadata={"subject": "Weekly Sync"},
        )

        transcript, text = await meetings_module.fetch_preferred_transcript_text(FakeClient(), meeting_ref)

        assert transcript is not None
        assert text is not None
        assert captured["paths"][0] == "/users/user-123/onlineMeetings/meeting-123/transcripts"
        assert captured["paths"][1] == "/users/user-123/onlineMeetings/meeting-123/transcripts/tx-1/content"
        assert "Hello world" in text


    async def test_missing_transcript_endpoint_falls_back_to_recordings(self, tmp_path, monkeypatch):
        from plugins.teams_pipeline import meetings as meetings_module

        async def _missing(*args, **kwargs):
            raise meetings_module.TeamsMeetingNotFoundError("No transcripts found for Teams meeting meeting-789")

        monkeypatch.setattr(meetings_module, "list_transcript_artifacts", _missing)

        meeting_ref = await _transcript_meeting_resolver(FakeGraphClient())
        transcript_artifact, transcript_text = await meetings_module.fetch_preferred_transcript_text(
            FakeGraphClient(),
            meeting_ref,
        )

        assert transcript_artifact is None
        assert transcript_text is None

    async def test_missing_transcript_and_recording_schedules_retry(self, tmp_path, monkeypatch):
        from plugins.teams_pipeline import pipeline as pipeline_module

        monkeypatch.setattr(pipeline_module, "resolve_meeting_reference", _transcript_meeting_resolver)
        monkeypatch.setattr(pipeline_module, "fetch_preferred_transcript_text", lambda *a, **kw: asyncio.sleep(0, result=(None, None)))
        monkeypatch.setattr(pipeline_module, "list_recording_artifacts", lambda *a, **kw: asyncio.sleep(0, result=[]))

        store = TeamsPipelineStore(tmp_path / "teams-store.json")
        pipeline = TeamsMeetingPipeline(
            graph_client=FakeGraphClient(),
            config={},
            summarize_fn=lambda **kwargs: asyncio.sleep(0, result=None),
            store=store,
        )

        job = await pipeline.run_notification(
            {
                "id": "notif-3",
                "changeType": "updated",
                "resource": "communications/onlineMeetings/meeting-789",
                "resourceData": {"id": "meeting-789"},
            }
        )

        assert job.status == "retry_scheduled"
        assert job.error_info["retryable"] is True
        assert "Recording unavailable" in job.error_info["message"]

    async def test_duplicate_notification_reuses_completed_job(self, tmp_path, monkeypatch):
        from plugins.teams_pipeline import pipeline as pipeline_module

        monkeypatch.setattr(pipeline_module, "resolve_meeting_reference", _transcript_meeting_resolver)

        async def _fetch_transcript(client, meeting_ref):
            return (
                MeetingArtifact(artifact_type="transcript", artifact_id="tx-dup", display_name="meeting.vtt"),
                "Decision: Keep duplicate notifications idempotent.\nAction: Verify the cached job is reused.",
            )

        summarize_calls = 0

        async def _summarize(**kwargs):
            nonlocal summarize_calls
            summarize_calls += 1
            return pipeline_module.TeamsMeetingSummaryPayload(
                meeting_ref=kwargs["resolved_meeting"],
                title="Weekly Sync",
                transcript_text=kwargs["transcript_text"],
                summary="Duplicate-safe summary",
                key_decisions=["Keep duplicate notifications idempotent."],
                action_items=["Verify the cached job is reused."],
                confidence="high",
                confidence_notes="Transcript available.",
                source_artifacts=kwargs["artifacts"],
            )

        monkeypatch.setattr(pipeline_module, "fetch_preferred_transcript_text", _fetch_transcript)
        monkeypatch.setattr(pipeline_module, "enrich_meeting_with_call_record", _no_call_record)

        store = TeamsPipelineStore(tmp_path / "teams-store.json")
        pipeline = TeamsMeetingPipeline(
            graph_client=FakeGraphClient(),
            store=store,
            config={"transcript_min_chars": 20},
            summarize_fn=_summarize,
        )
        notification = {
            "id": "notif-dup",
            "changeType": "updated",
            "resource": "communications/onlineMeetings/meeting-dup",
            "resourceData": {"id": "meeting-dup"},
        }

        first_job = await pipeline.run_notification(notification)
        second_job = await pipeline.run_notification(notification)

        assert first_job.status == "completed"
        assert second_job.status == "completed"
        assert second_job.job_id == first_job.job_id
        assert summarize_calls == 1
        assert len(store.list_jobs()) == 1
        receipt_key = TeamsPipelineStore.build_notification_receipt_key(notification)
        assert store.has_notification_receipt(receipt_key) is True


# ─── HTML Rendering Tests (Group 7) ────────────────────────────────


def _make_full_payload() -> TeamsMeetingSummaryPayload:
    """Build a payload with all extended fields populated for HTML rendering tests."""
    from plugins.teams_pipeline.models import (
        SpeakerUpdate, StrategicDiscussion, StructuredActionItem, StructuredRisk,
    )
    from datetime import datetime, timezone

    return TeamsMeetingSummaryPayload(
        meeting_ref=TeamsMeetingRef(
            meeting_id="meeting-html-test",
            metadata={"organizer": "Ted Blackmon"},
        ),
        title="Daily Stand-up",
        start_time=datetime(2026, 5, 22, 6, 59, tzinfo=timezone.utc),
        end_time=datetime(2026, 5, 22, 8, 23, tzinfo=timezone.utc),
        participants=["Phuong Lambert", "Markus Chen", "Shadi Shaheen"],
        speakers=[
            SpeakerUpdate(
                name="Phuong",
                topic_label="Database Modeling",
                bullets=[
                    "Refactored schema.py with new MeetingArtifact class",
                    "Fixed config.yaml loading for template_path",
                ],
            ),
            SpeakerUpdate(
                name="Markus",
                topic_label="Frontend Updates",
                bullets=["Completed TaskList component with drag-and-drop"],
            ),
        ],
        decision_headline="Adopt tiered visibility",
        decision_chosen="Three-tier model: public, internal, confidential.",
        decision_rejected="Flat permission model was too coarse.",
        strategic_discussions=[
            StrategicDiscussion(topic="Q3 Roadmap", content="Expanding AI features."),
        ],
        structured_action_items=[
            StructuredActionItem(who="Phuong", action="Finalize schema PR"),
            StructuredActionItem(who="Markus", action="Write unit tests"),
            StructuredActionItem(who="Shadi", action="Review proposal"),
            StructuredActionItem(who="Team", action="Schedule review meeting"),
        ],
        structured_risks=[
            StructuredRisk(risk="OAuth token expiry", impact="Summaries fail silently"),
        ],
    )


def test_render_html_contains_expected_tags_and_sections():
    """7.9: HTML output contains expected tags and section order."""
    from plugins.teams_pipeline.pipeline import _render_summary_html

    payload = _make_full_payload()
    result = _render_summary_html(payload)

    # Header
    assert "<h2>📋 Meeting Summary: Daily Stand-up</h2>" in result
    assert "May 22, 2026" in result
    assert "06:59–08:23 UTC" in result
    assert "~84 min" in result
    assert "Ted Blackmon" in result
    assert "Phuong Lambert, Markus Chen, Shadi Shaheen" in result

    # Status updates
    assert "<b>Phuong — Database Modeling</b>" in result
    assert "<ul>" in result
    assert "<li>" in result
    assert "<code>schema.py</code>" in result
    assert "<code>config.yaml</code>" in result

    # Decision
    assert "🎯 Key Decision: Adopt tiered visibility" in result
    assert "Three-tier model" in result
    assert "<i>Rejected:" in result

    # Strategic
    assert "📌 Q3 Roadmap" in result

    # Action items table
    assert "<th>Who</th>" in result
    assert "<th>Action</th>" in result

    # Risks table
    assert "<th>Risk</th>" in result
    assert "<th>Impact</th>" in result

    # Separators
    assert "<hr>" in result

    # Section order: header → status → decision → strategic → actions → risks
    idx_status = result.index("📊 Status Updates")
    idx_decision = result.index("🎯 Key Decision")
    idx_strategic = result.index("📌 Q3 Roadmap")
    idx_actions = result.index("✅ Action Items")
    idx_risks = result.index("⚠️ Risks")
    assert idx_status < idx_decision < idx_strategic < idx_actions < idx_risks


def test_render_html_action_items_sorted_alphabetically():
    """7.10: Action items table sorted alphabetically by Who, Team last."""
    from plugins.teams_pipeline.pipeline import _render_summary_html
    import re

    payload = _make_full_payload()
    result = _render_summary_html(payload)

    # Extract Who values in order from table rows
    who_values = re.findall(r'<tr><td><b>([^<]+)</b></td>', result)
    assert who_values == ["Markus", "Phuong", "Shadi", "Team"]


def test_render_html_conditional_sections_omitted_when_empty():
    """7.11: Conditional sections omitted when data is empty."""
    from plugins.teams_pipeline.pipeline import _render_summary_html

    payload = TeamsMeetingSummaryPayload(
        meeting_ref=TeamsMeetingRef(meeting_id="empty-meeting"),
        title="Empty Meeting",
    )
    result = _render_summary_html(payload)

    assert "📊 Status Updates" not in result
    assert "🎯 Key Decision" not in result
    assert "📌" not in result
    assert "<table>" not in result
    assert "⚠️ Risks" not in result
    # Header should still be present
    assert "<h2>📋 Meeting Summary: Empty Meeting</h2>" in result


def test_render_html_legacy_action_items():
    """Legacy string action_items auto-extracted into Who/Action table."""
    from plugins.teams_pipeline.pipeline import _render_summary_html

    payload = TeamsMeetingSummaryPayload(
        meeting_ref=TeamsMeetingRef(meeting_id="legacy"),
        title="Legacy Meeting",
        action_items=["Phuong: Fix the auth bug", "Team: Review PR #42"],
    )
    result = _render_summary_html(payload)

    assert "<b>Phuong</b>" in result
    assert "Fix the auth bug" in result
    # Phuong before Team (alphabetical, Team last)
    assert result.index("<b>Phuong</b>") < result.index("<b>Team</b>")


# ─── Teams Sink Integration Tests (Group 8) ─────────────────────────


def test_teams_writer_uses_pre_rendered_html_when_available():
    """8.4: Teams writer passes through pre-rendered template-driven HTML."""
    from plugins.platforms.teams.adapter import TeamsSummaryWriter

    writer = TeamsSummaryWriter()

    # Create payload with pre-rendered HTML
    payload = TeamsMeetingSummaryPayload(
        meeting_ref=TeamsMeetingRef(meeting_id="pre-rendered"),
        title="Template Meeting",
        summary="This should not appear",
        rendered_html="<h2>📋 Pre-rendered Template HTML</h2><hr><h3>✅ Action Items</h3><table><tr><th>Who</th><th>Action</th></tr></table>",
    )

    result = writer._render_summary_html(payload)

    # Should use pre-rendered HTML, not generate its own
    assert "📋 Pre-rendered Template HTML" in result
    assert "This should not appear" not in result
    assert "<h3>✅ Action Items</h3>" in result


def test_teams_writer_fallback_to_hardcoded_when_no_pre_rendered_html():
    """8.3: Teams writer falls back to hardcoded renderer when rendered_html is None."""
    from plugins.platforms.teams.adapter import TeamsSummaryWriter

    writer = TeamsSummaryWriter()

    payload = TeamsMeetingSummaryPayload(
        meeting_ref=TeamsMeetingRef(meeting_id="fallback"),
        title="Fallback Meeting",
        summary="This summary uses the fallback renderer",
        key_decisions=["Use vLLM for inference"],
        action_items=["Deploy by Friday"],
        risks=["GPU budget exceeded"],
    )

    result = writer._render_summary_html(payload)

    # Should use hardcoded renderer (no pre-rendered HTML)
    assert "<h2>Fallback Meeting</h2>" in result
    assert "This summary uses the fallback renderer" in result
    assert "Use vLLM for inference" in result
    assert "Deploy by Friday" in result
    assert "GPU budget exceeded" in result
    # Should NOT have template-driven elements (emoji, tables, etc.)
    assert "📋" not in result
    assert "<table>" not in result


# ─── Notion/Linear Sink Preservation Tests (Group 9) ────────


def test_render_markdown_includes_extended_fields():
    """9.1: Markdown renderer includes speakers and strategic_discussions."""
    from plugins.teams_pipeline.pipeline import _render_summary_markdown

    payload = _make_full_payload()
    result = _render_summary_markdown(payload)

    # Status updates section with per-speaker entries
    assert "## Status Updates & Code Exploration" in result
    assert "**Phuong — Database Modeling**" in result
    assert "**Markus — Frontend Updates**" in result
    assert "Refactored schema.py" in result
    assert "Completed TaskList" in result

    # Key decision with headline
    assert "## Key Decision: Adopt tiered visibility" in result
    assert "Three-tier model" in result
    assert "*Rejected:" in result

    # Strategic discussions
    assert "## Strategic Discussions" in result
    assert "### Q3 Roadmap" in result
    assert "Expanding AI features" in result

    # Action items with structured format
    assert "## Action Items" in result
    assert "**Phuong**: Finalize schema PR" in result
    assert "**Markus**: Write unit tests" in result
    assert "**Shadi**: Review proposal" in result
    assert "**Team**: Schedule review meeting" in result

    # Risks with impact
    assert "## Risks" in result
    assert "**OAuth token expiry**: Summaries fail silently" in result


def test_render_markdown_no_html_tags():
    """9.2 & 9.3: Markdown output contains no HTML tags (safe for Notion/Linear)."""
    from plugins.teams_pipeline.pipeline import _render_summary_markdown

    payload = _make_full_payload()
    result = _render_summary_markdown(payload)

    # No HTML tags allowed in markdown output
    assert "<h2>" not in result
    assert "<h3>" not in result
    assert "<p>" not in result
    assert "<table>" not in result
    assert "<code>" not in result
    assert "<b>" not in result
    assert "<ul>" not in result
    assert "<li>" not in result
    assert "<hr>" not in result

    # Markdown formatting is present instead
    assert "**Phuong" in result
    assert "- " in result


def test_render_markdown_backward_compat_legacy_payload():
    """Legacy payload (no extended fields) renders same as before, no errors."""
    from plugins.teams_pipeline.pipeline import _render_summary_markdown

    payload = TeamsMeetingSummaryPayload(
        meeting_ref=TeamsMeetingRef(meeting_id="legacy-md"),
        title="Legacy Markdown Meeting",
        summary="This is a summary",
        key_decisions=["Decision A", "Decision B"],
        action_items=["Fix bug", "Deploy to prod"],
        risks=["Budget risk"],
        confidence="high",
        confidence_notes="Based on 2-hour call",
    )
    result = _render_summary_markdown(payload)

    # Basic sections present
    assert "# Legacy Markdown Meeting" in result
    assert "## Summary" in result
    assert "This is a summary" in result
    assert "## Key Decisions" in result
    assert "- Decision A" in result
    assert "- Fix bug" in result
    assert "- Deploy to prod" in result
    assert "## Risks" in result
    assert "- Budget risk" in result
    assert "Confidence: high" in result
    assert "Based on 2-hour call" in result

    # No extended sections (not populated)
    assert "## Status Updates" not in result
    assert "## Strategic Discussions" not in result


# ─── E2E Verification Tests (Group 10) ─────────────────────


def test_fallback_to_hardcoded_summary_when_template_missing():
    """10.2: When template file is missing, pipeline uses fallback behavior with no errors."""
    from plugins.teams_pipeline.pipeline import _load_summary_template, _render_summary_markdown

    # Missing template returns None
    template = _load_summary_template("/nonexistent/path/to/template.yaml")
    assert template is None

    # Markdown renderer works fine with legacy payload (no extended fields)
    payload = TeamsMeetingSummaryPayload(
        meeting_ref=TeamsMeetingRef(meeting_id="fallback-test"),
        title="Fallback Summary",
        summary="Generated without template",
        key_decisions=["Fallback decision"],
        action_items=["Fix something"],
    )
    result = _render_summary_markdown(payload)
    assert "# Fallback Summary" in result
    assert "Generated without template" in result


def test_rendered_html_not_used_without_template():
    """10.2: payload.rendered_html stays None when template is not loaded."""
    payload = TeamsMeetingSummaryPayload(
        meeting_ref=TeamsMeetingRef(meeting_id="no-template"),
        title="No Template",
    )
    assert payload.rendered_html is None


# ─── Stage 2 Two-Stage Pipeline Tests (Group 11) ──────────


def test_stage2_system_prompt_contains_only_structured_schema():
    """11.7: Stage 2 prompt contains ONLY structured extraction schema — no overview fields."""
    from plugins.teams_pipeline.pipeline import _build_stage2_system_prompt, _build_system_prompt_from_template, _load_summary_template

    template = _load_summary_template()
    assert template is not None

    stage2_prompt = _build_stage2_system_prompt(template)
    stage1_prompt = _build_system_prompt_from_template(template)

    # Stage 2 MUST contain structured extraction schema
    assert "speakers" in stage2_prompt
    assert "structured_action_items" in stage2_prompt
    assert "decision_headline" in stage2_prompt
    assert "strategic_discussions" in stage2_prompt
    assert "topic_label" in stage2_prompt
    assert "bullets" in stage2_prompt

    # Stage 2 MUST NOT contain overview fields (those are Stage 1's job)
    assert '"summary": str' not in stage2_prompt
    assert '"key_decisions": [str]' not in stage2_prompt
    assert '"confidence": str' not in stage2_prompt

    # Stage 1 SHOULD contain overview fields
    assert '"summary": str' in stage1_prompt
    assert '"key_decisions": [str]' in stage1_prompt
    assert '"confidence": str' in stage1_prompt

    # Stage 2 is narrower than Stage 1
    assert len(stage2_prompt) < len(stage1_prompt)


def test_stage2_parse_failure_returns_empty_dict():
    """11.8: Stage 2 parse failure returns empty dict — Stage 1 output preserved."""
    from plugins.teams_pipeline.pipeline import _parse_stage2_json

    # Empty input
    assert _parse_stage2_json("") == {}
    assert _parse_stage2_json(None) == {}  # type: ignore

    # Invalid JSON
    assert _parse_stage2_json("not valid json {{{") == {}

    # Non-object JSON
    assert _parse_stage2_json('"just a string"') == {}
    assert _parse_stage2_json("[1, 2, 3]") == {}

    # Valid but empty object — returns dict with empty lists (not empty dict)
    result = _parse_stage2_json("{}")
    assert result == {
        "speakers": [],
        "structured_action_items": [],
        "decision_headline": None,
        "decision_chosen": None,
        "decision_rejected": None,
        "strategic_discussions": [],
    }


def test_stage2_parse_success_extracts_structured_data():
    """11.9: Stage 2 success → speakers have detailed bullets, action items have proper who/action split."""
    from plugins.teams_pipeline.pipeline import _parse_stage2_json
    from plugins.teams_pipeline.models import SpeakerUpdate, StructuredActionItem, StrategicDiscussion

    stage2_json = '''{
        "speakers": [
            {
                "name": "Phuong",
                "topic_label": "Database Schema Refactor",
                "bullets": [
                    "Refactored models.py with 4 new dataclasses",
                    "Added rendered_html field to TeamsMeetingSummaryPayload",
                    "Fixed compile errors in pipeline.py"
                ]
            },
            {
                "name": "Sebastian",
                "topic_label": "Deliverable Deduplication",
                "bullets": [
                    "Deduplication logic complete for deliverables",
                    "Fixed edge case connecting new nodes to existing ones",
                    "Deployment failure resolved in test environment"
                ]
            }
        ],
        "structured_action_items": [
            {"who": "Phuong", "action": "Finalize user chat architecture"},
            {"who": "Sebastian", "action": "Fix deployment for deduplication"},
            {"who": "Cali", "action": "Test deliverable deduplication"}
        ],
        "decision_headline": "Adopt left-menu UI for Optimizers",
        "decision_chosen": "Left-menu layout to handle expanding optimizer list",
        "decision_rejected": "Simple card/table view was too limited",
        "strategic_discussions": [
            {"topic": "AI Impact on Jobs", "content": "Discussed NVIDIA CEO Jensen Huang views on AI creating new roles"}
        ]
    }'''

    result = _parse_stage2_json(stage2_json)

    # Speakers with detailed bullets
    assert len(result["speakers"]) == 2
    assert isinstance(result["speakers"][0], SpeakerUpdate)
    assert result["speakers"][0].name == "Phuong"
    assert result["speakers"][0].topic_label == "Database Schema Refactor"
    assert len(result["speakers"][0].bullets) == 3
    assert "models.py" in result["speakers"][0].bullets[0]

    # Action items with proper who/action split
    assert len(result["structured_action_items"]) == 3
    assert isinstance(result["structured_action_items"][0], StructuredActionItem)
    assert result["structured_action_items"][0].who == "Phuong"
    assert result["structured_action_items"][0].action == "Finalize user chat architecture"
    # No name embedded in action text
    assert "Phuong" not in result["structured_action_items"][0].action

    # Decision fields
    assert result["decision_headline"] == "Adopt left-menu UI for Optimizers"
    assert result["decision_chosen"] == "Left-menu layout to handle expanding optimizer list"
    assert result["decision_rejected"] == "Simple card/table view was too limited"

    # Strategic discussions
    assert len(result["strategic_discussions"]) == 1
    assert isinstance(result["strategic_discussions"][0], StrategicDiscussion)
    assert result["strategic_discussions"][0].topic == "AI Impact on Jobs"


def test_stage2_parse_handles_code_fences():
    """Stage 2 parser strips markdown code fences from LLM output."""
    from plugins.teams_pipeline.pipeline import _parse_stage2_json

    fenced = '```json\n{"speakers": [{"name": "Cali", "topic_label": "Testing", "bullets": ["Test item"]}]}\n```'
    result = _parse_stage2_json(fenced)
    assert len(result["speakers"]) == 1
    assert result["speakers"][0].name == "Cali"


def test_stage2_user_prompt_contains_required_sections():
    """Stage 2 user prompt includes Stage 1 summary, transcript excerpt, and speaker list."""
    from plugins.teams_pipeline.pipeline import _build_stage2_user_prompt

    prompt = _build_stage2_user_prompt(
        stage1_summary="The meeting discussed database refactoring and UI improvements.",
        transcript_excerpt="<v Phuong Lambert>Hello team, let's discuss the schema changes.</v>",
        speaker_list=["phuong@optimalitypro.com", "cali@optimalitypro.com", "sebastian@optimalitypro.com"],
    )

    assert "Stage 1 Summary" in prompt
    assert "database refactoring" in prompt
    assert "Transcript Excerpt" in prompt
    assert "Phuong Lambert" in prompt
    assert "Speaker List" in prompt
    assert "phuong@optimalitypro.com" in prompt
    assert "Extract structured data" in prompt
