from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ui import chunk


class FakeStreamlit:
    """Small Streamlit double for Chunk helpers that only need state and rerun."""

    def __init__(self):
        self.session_state = {}
        self.rerun_called = False

    def rerun(self):
        self.rerun_called = True
        raise RuntimeError("rerun")


class FakeChunkHarness:
    """Stateful harness that supplies the callbacks required by ChunkServices."""

    def __init__(
        self,
        *,
        preferences=None,
        enriched_rows=None,
        open_task=None,
        elapsed_session_minutes=0.0,
        expected_session_minutes=60.0,
        persona_name="balanced",
        state_name="engaged",
        timer_snapshot=None,
        overlay_state=None,
    ):
        self.preferences = preferences or {}
        self.enriched_rows = enriched_rows or {}
        self.open_task = open_task
        self.elapsed_session_minutes = elapsed_session_minutes
        self.expected_session_minutes = expected_session_minutes
        self.persona_name = persona_name
        self.state_name = state_name
        self.timer_snapshot = timer_snapshot or SimpleNamespace(
            running=False,
            expires_at=None,
            duration_seconds=None,
        )
        self.overlay_state = overlay_state or {}
        self.scheduled_timers = []
        self.disabled_timer = False
        self.cleared_overlay = False
        self.rest_breaks = []
        self.feedback_requests = []
        self.status_updates = []
        self.work_ended = False

    def services(self):
        """Build the real dataclass with callbacks pointing at this harness."""

        return chunk.ChunkServices(
            get_user_preferences=lambda: self.preferences,
            get_enriched_task_row_by_instance_id=lambda instance_id: self.enriched_rows.get(
                instance_id
            ),
            get_open_task_row=lambda: self.open_task,
            get_resumable_session_elapsed_minutes=lambda: self.elapsed_session_minutes,
            get_effective_session_work_time=lambda: self.expected_session_minutes,
            get_current_persona_name=lambda: self.persona_name,
            get_effective_current_state_name=lambda: self.state_name,
            start_focus_cycle_tracker=lambda task_row, cycle_type: {
                "instance_id": task_row.get("instance_id"),
                "task_id": task_row.get("task_id"),
                "cycle_type": cycle_type,
                "iterations": 1,
            },
            set_focus_overlay_state=self._set_overlay_state,
            get_focus_overlay_state=lambda: self.overlay_state,
            clear_focus_overlay_state=self._clear_overlay_state,
            format_cycle_minutes_label=lambda seconds: str(int(float(seconds) / 60)),
            schedule_work_timer=self._schedule_work_timer,
            disable_work_timer=self._disable_work_timer,
            get_work_timer_snapshot=lambda: self.timer_snapshot,
            expire_chunk_cycle=lambda *args, **kwargs: None,
            get_current_task_adaptation=lambda tasks_df: (None, None),
            get_tasks_dataframe=lambda: None,
            begin_rest_break=self._begin_rest_break,
            clear_task_completion_feedback_request=lambda: None,
            request_task_completion_feedback=self._request_task_completion_feedback,
            get_post_work_incomplete_task_status=lambda task_row: "asleep",
            update_task_status=self._update_task_status,
            notify_work_ended=self._notify_work_ended,
            handle_api_exception=lambda error, message: (_ for _ in ()).throw(error),
        )

    def _set_overlay_state(self, overlay_state):
        self.overlay_state = overlay_state

    def _clear_overlay_state(self):
        self.cleared_overlay = True
        self.overlay_state = {}

    def _schedule_work_timer(self, duration_minutes, on_expiry, source_label):
        self.scheduled_timers.append(
            {
                "duration_minutes": duration_minutes,
                "on_expiry": on_expiry,
                "source_label": source_label,
            }
        )

    def _disable_work_timer(self):
        self.disabled_timer = True

    def _begin_rest_break(self, **kwargs):
        self.rest_breaks.append(kwargs)

    def _request_task_completion_feedback(self, *args, **kwargs):
        self.feedback_requests.append((args, kwargs))

    def _update_task_status(self, task_row, status):
        self.status_updates.append((task_row, status))

    def _notify_work_ended(self):
        self.work_ended = True


@pytest.fixture
def fake_st(monkeypatch):
    """Replace Streamlit with a deterministic fake for unit-level state tests."""

    fake = FakeStreamlit()
    monkeypatch.setattr(chunk, "st", fake)
    return fake


def make_task_row(**overrides):
    """Create the compact task row shape required by the Chunk module."""

    row = {
        "instance_id": "instance-1",
        "task_id": "task-1",
        "title": "Write notes",
        "description": "Draft the notes",
        "size_minutes": 30,
        "size_id": 2,
    }
    row.update(overrides)
    return row


def test_remaining_minutes_initialises_from_size_minutes(fake_st):
    harness = FakeChunkHarness()
    services = harness.services()
    task_row = make_task_row(size_minutes=42)

    remaining = chunk.get_chunk_remaining_minutes(task_row, services)

    # The first cycle seeds per-instance state from the enriched task estimate.
    assert remaining == 42.0
    assert fake_st.session_state[chunk.CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] == {
        "instance-1": 42.0
    }


def test_remaining_minutes_uses_custom_size_when_size_minutes_is_missing(fake_st):
    harness = FakeChunkHarness(preferences={"custom_sizes": [10, 25, 50]})
    services = harness.services()
    task_row = make_task_row(size_minutes=None, size_id=2)

    remaining = chunk.get_chunk_remaining_minutes(task_row, services)

    # Chunk supports rows that only carry lookup ids by resolving the user's
    # current custom size table.
    assert remaining == 25.0


def test_register_elapsed_work_subtracts_from_remaining_minutes(fake_st):
    harness = FakeChunkHarness()
    services = harness.services()
    task_row = make_task_row(size_minutes=30)
    chunk.get_chunk_remaining_minutes(task_row, services)

    chunk.register_chunk_work_elapsed(task_row, 12 * 60, services)

    assert fake_st.session_state[chunk.CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] == {
        "instance-1": 18.0
    }


def test_calculate_next_chunk_plan_returns_floor_when_calculated_cycle_is_too_small(fake_st):
    harness = FakeChunkHarness(
        preferences={"chunk_min_floor_minutes": 5, "max_continuous_work_minutes": 60},
        elapsed_session_minutes=55,
        expected_session_minutes=60,
        open_task=make_task_row(size_minutes=30),
    )

    plan = chunk.calculate_next_chunk_plan(None, harness.services())

    # Stamina would suggest roughly 2.5 minutes, but the configured floor still
    # fits inside the soft session limit, so Chunk rounds up to a useful block.
    assert plan == {"status": "ok", "duration_minutes": 5}


def test_calculate_next_chunk_plan_requests_extension_when_floor_exceeds_soft_limit(fake_st):
    harness = FakeChunkHarness(
        preferences={"chunk_min_floor_minutes": 10, "max_continuous_work_minutes": 60},
        elapsed_session_minutes=66,
        expected_session_minutes=60,
        open_task=make_task_row(size_minutes=30),
    )

    plan = chunk.calculate_next_chunk_plan(None, harness.services())

    # Past the tolerated session overrun, Chunk should pause instead of silently
    # starting another meaningful block.
    assert plan["status"] == "needs_session_extension"
    assert plan["suggested_floor_minutes"] == 10
    assert plan["suggested_extension_minutes"] >= 1
    assert plan["task_row"]["instance_id"] == "instance-1"


def test_request_next_chunk_cycle_starts_timer_and_overlay_for_ok_plan(fake_st):
    task_row = make_task_row(size_minutes=20)
    harness = FakeChunkHarness(
        preferences={"chunk_min_floor_minutes": 5, "max_continuous_work_minutes": 60},
        open_task=task_row,
        elapsed_session_minutes=0,
        expected_session_minutes=60,
    )
    services = harness.services()

    started = chunk.request_next_chunk_cycle(
        task_row,
        source_label="unit-test",
        services=services,
    )

    assert started is True
    assert harness.scheduled_timers[0]["source_label"] == "unit-test"
    assert harness.overlay_state["cycle_type"] == "chunk"
    assert harness.overlay_state["instance_id"] == "instance-1"


def test_request_next_chunk_cycle_queues_extension_prompt_instead_of_timer(fake_st):
    task_row = make_task_row(size_minutes=30)
    harness = FakeChunkHarness(
        preferences={"chunk_min_floor_minutes": 10, "max_continuous_work_minutes": 60},
        open_task=task_row,
        elapsed_session_minutes=66,
        expected_session_minutes=60,
    )

    started = chunk.request_next_chunk_cycle(
        task_row,
        source_label="extension-needed",
        services=harness.services(),
    )

    assert started is False
    assert harness.scheduled_timers == []
    assert fake_st.session_state[chunk.CHUNK_SESSION_EXTENSION_PROMPT_CONTEXT_KEY][
        "source_label"
    ] == "extension-needed"


def test_expire_chunk_cycle_records_elapsed_work_and_opens_review(fake_st):
    task_row = make_task_row(size_minutes=30)
    fake_st.session_state[chunk.CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = {
        "instance-1": 30.0
    }
    harness = FakeChunkHarness(
        open_task=task_row,
        overlay_state={"duration_seconds": 1800, "task_row": task_row},
        timer_snapshot=SimpleNamespace(
            running=True,
            expires_at=1_000.0,
            duration_seconds=1800,
        ),
    )
    monkeypatch_now = SimpleNamespace(timestamp=lambda: 1_600.0)

    # Patch the module's datetime facade narrowly so the elapsed calculation is
    # deterministic without depending on wall-clock time.
    original_datetime = chunk.datetime
    chunk.datetime = SimpleNamespace(now=lambda tz=None: monkeypatch_now)
    try:
        with pytest.raises(RuntimeError, match="rerun"):
            chunk.expire_chunk_cycle(services=harness.services())
    finally:
        chunk.datetime = original_datetime

    assert fake_st.session_state[chunk.CHUNK_CONTINUOUS_WORK_SECONDS_KEY] == 600
    assert fake_st.session_state[chunk.CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] == {
        "instance-1": 20.0
    }
    assert fake_st.session_state[chunk.CHUNK_REVIEW_PENDING_KEY] is True
    assert harness.disabled_timer is True
    assert harness.cleared_overlay is True


def test_clear_chunk_remaining_minutes_removes_only_target_instance(fake_st):
    fake_st.session_state[chunk.CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = {
        "instance-1": 10.0,
        "instance-2": 20.0,
    }

    chunk.clear_chunk_remaining_minutes({"instance_id": "instance-1"})

    assert fake_st.session_state[chunk.CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] == {
        "instance-2": 20.0
    }
