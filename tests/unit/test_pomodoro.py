from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ui import pomodoro


class FakeStreamlit:
    """Small Streamlit double for Pomodoro helpers that only need local state."""

    def __init__(self):
        self.session_state = {}
        self.html_payloads = []
        self.info_messages = []

    def html(self, payload):
        self.html_payloads.append(payload)

    def info(self, message):
        self.info_messages.append(message)

    def rerun(self):
        raise RuntimeError("rerun")


class FakeWorkTimer:
    """Capture timer resets without starting any real background timer."""

    def __init__(self):
        self.resets = []

    def reset(self, *, duration, on_expiry):
        self.resets.append({"duration": duration, "on_expiry": on_expiry})


class FakePomodoroHarness:
    """Stateful harness that supplies callbacks required by PomodoroServices."""

    def __init__(
        self,
        *,
        preferences=None,
        enriched_rows=None,
        open_task=None,
        adaptation=None,
        timer_snapshot=None,
        guidance_expires_at=None,
        body_doubling_active=False,
        post_work_status="asleep",
    ):
        self.preferences = preferences or {}
        self.enriched_rows = enriched_rows or {}
        self.open_task = open_task
        self.adaptation = adaptation
        self.timer_snapshot = timer_snapshot or SimpleNamespace(
            running=False,
            expires_at=None,
            duration_seconds=None,
        )
        self.guidance_expires_at = guidance_expires_at
        self.body_doubling_active = body_doubling_active
        self.post_work_status = post_work_status
        self.work_timer = FakeWorkTimer()
        self.disabled_timer = False
        self.timer_log_lines = []
        self.scheduled_timers = []
        self.status_updates = []
        self.feedback_requests = []
        self.cleared_feedback = False
        self.work_ended = False
        self.displayed_messages = []
        self.voice_buttons = []
        self.chunk_cycle_requests = []
        self.handled_errors = []
        self.info_messages = []

    def services(self):
        """Build the real dataclass with callbacks pointing at this harness."""

        return pomodoro.PomodoroServices(
            get_user_preferences=lambda: self.preferences,
            get_enriched_task_row_by_instance_id=lambda instance_id: self.enriched_rows.get(
                instance_id
            ),
            get_current_task_adaptation=lambda tasks_df: (self.adaptation, None),
            get_tasks_dataframe=lambda: None,
            get_work_timer_snapshot=lambda: self.timer_snapshot,
            get_work_timer=lambda session_state=None: self.work_timer,
            schedule_work_timer=self._schedule_work_timer,
            disable_work_timer=self._disable_work_timer,
            append_timer_log_line=self.timer_log_lines.append,
            expire_sprint_callback=self._expire_sprint_callback,
            expire_rest_callback=self._expire_rest_callback,
            get_open_task_row=lambda: self.open_task,
            get_post_work_incomplete_task_status=lambda task_row: self.post_work_status,
            update_task_status=self._update_task_status,
            notify_work_ended=self._notify_work_ended,
            request_task_completion_feedback=self._request_task_completion_feedback,
            clear_task_completion_feedback_request=self._clear_feedback_request,
            display_message=self._display_message,
            get_adaptive_message_intensity=lambda: "high",
            render_voice_message_button=self._render_voice_message_button,
            request_next_chunk_cycle=self._request_next_chunk_cycle,
            body_doubling_session_active=lambda: self.body_doubling_active,
            get_open_task_guidance_expires_at=lambda: self.guidance_expires_at,
            handle_api_exception=self._handle_api_exception,
            rerun=lambda: (_ for _ in ()).throw(RuntimeError("rerun")),
            info=self.info_messages.append,
        )

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

    def _expire_sprint_callback(self, *args, **kwargs):
        return None

    def _expire_rest_callback(self, *args, **kwargs):
        return None

    def _update_task_status(self, task_row, status):
        self.status_updates.append((task_row, status))

    def _notify_work_ended(self):
        self.work_ended = True

    def _request_task_completion_feedback(self, *args, **kwargs):
        self.feedback_requests.append((args, kwargs))

    def _clear_feedback_request(self):
        self.cleared_feedback = True

    def _display_message(self, *args, **kwargs):
        self.displayed_messages.append((args, kwargs))

    def _render_voice_message_button(self, *args, **kwargs):
        self.voice_buttons.append((args, kwargs))

    def _request_next_chunk_cycle(self, *args, **kwargs):
        self.chunk_cycle_requests.append((args, kwargs))
        return True

    def _handle_api_exception(self, error, message):
        self.handled_errors.append((error, message))


@pytest.fixture
def fake_st(monkeypatch):
    """Replace Streamlit with a deterministic fake for unit-level state tests."""

    fake = FakeStreamlit()
    monkeypatch.setattr(pomodoro, "st", fake)
    return fake


def make_task_row(**overrides):
    """Create the compact task row shape required by the Pomodoro module."""

    row = {
        "instance_id": "instance-1",
        "task_id": "task-1",
        "title": "Write notes",
        "description": "Draft the notes",
        "size_minutes": 30,
    }
    row.update(overrides)
    return row


def patch_pomodoro_now(timestamp):
    """Return a tiny datetime facade that makes time-based tests deterministic."""

    return SimpleNamespace(
        now=lambda tz=None: SimpleNamespace(timestamp=lambda: timestamp)
    )


def test_focus_cycle_tracker_increments_only_for_same_task_and_cycle(fake_st):
    task_row = make_task_row()

    first_tracker = pomodoro.start_focus_cycle_tracker(task_row, "pomodoro")
    second_tracker = pomodoro.start_focus_cycle_tracker(task_row, "pomodoro")
    different_cycle_tracker = pomodoro.start_focus_cycle_tracker(task_row, "chunk")

    assert first_tracker["iterations"] == 1
    assert second_tracker["iterations"] == 2
    assert different_cycle_tracker["iterations"] == 1


def test_start_pomodoro_overlay_uses_enriched_task_duration(fake_st, monkeypatch):
    task_row = make_task_row(size_minutes=30)
    harness = FakePomodoroHarness(
        enriched_rows={"instance-1": {"size_minutes": 45}},
    )
    monkeypatch.setattr(pomodoro, "datetime", patch_pomodoro_now(1_000.0))

    pomodoro.start_pomodoro_overlay(task_row, 25, harness.services())

    overlay = fake_st.session_state[pomodoro.POMODORO_OVERLAY_STATE_KEY]
    assert overlay["instance_id"] == "instance-1"
    assert overlay["cycle_type"] == "pomodoro"
    assert overlay["mode"] == "work"
    assert overlay["duration_seconds"] == 25 * 60
    assert overlay["task_duration_minutes"] == 45
    assert overlay["iterations"] == 1
    assert overlay["started_at"] == 1_000.0


def test_start_pomodoro_rest_overlay_preserves_task_context(fake_st, monkeypatch):
    fake_st.session_state[pomodoro.POMODORO_OVERLAY_STATE_KEY] = {
        "instance_id": "instance-1",
        "task_id": "task-1",
        "title": "Write notes",
        "mode": "work",
    }
    monkeypatch.setattr(pomodoro, "datetime", patch_pomodoro_now(2_000.0))

    pomodoro.start_pomodoro_rest_overlay(7)

    overlay = fake_st.session_state[pomodoro.POMODORO_OVERLAY_STATE_KEY]
    assert overlay["instance_id"] == "instance-1"
    assert overlay["title"] == "Write notes"
    assert overlay["mode"] == "rest"
    assert overlay["cycle_type"] == "pomodoro"
    assert overlay["duration_seconds"] == 7 * 60
    assert overlay["started_at"] == 2_000.0


def test_effective_durations_use_preferences_and_defaults(fake_st):
    default_services = FakePomodoroHarness().services()
    custom_services = FakePomodoroHarness(
        preferences={"sprint": 15, "rest_duration": 8}
    ).services()

    assert pomodoro.get_effective_pomodoro_sprint_minutes(default_services) > 0
    assert pomodoro.get_effective_rest_duration_minutes(default_services) > 0
    assert pomodoro.get_effective_pomodoro_sprint_minutes(custom_services) == 15
    assert pomodoro.get_effective_rest_duration_minutes(custom_services) == 8


def test_clear_expired_rest_message_removes_message_and_reruns(
    fake_st,
    monkeypatch,
):
    fake_st.session_state[pomodoro.REST_MESSAGE_KEY] = "Rest is over."
    fake_st.session_state[pomodoro.REST_MESSAGE_EXPIRES_AT_KEY] = 10.0
    monkeypatch.setattr(pomodoro, "datetime", patch_pomodoro_now(11.0))

    with pytest.raises(RuntimeError, match="rerun"):
        pomodoro.clear_expired_rest_message(FakePomodoroHarness().services())

    assert pomodoro.REST_MESSAGE_KEY not in fake_st.session_state
    assert pomodoro.REST_MESSAGE_EXPIRES_AT_KEY not in fake_st.session_state


def test_begin_rest_break_stores_resume_context_and_resets_timer(fake_st):
    harness = FakePomodoroHarness(preferences={"sprint": 20, "rest_duration": 6})
    services = harness.services()

    pomodoro.begin_pomodoro_rest_break(
        previous_work_outcome="incomplete",
        resume_cycle_type="chunk",
        services=services,
    )

    # Rest keeps enough context to decide whether to resume a Chunk or a
    # Pomodoro after the break ends.
    assert fake_st.session_state[pomodoro.REST_RESUME_PROMPT_CONTEXT_KEY] == {
        "previous_work_outcome": "incomplete",
        "work_duration_minutes": 20,
        "resume_cycle_type": "chunk",
    }
    assert harness.work_timer.resets == [
        {"duration": 6 * 60, "on_expiry": harness._expire_rest_callback}
    ]
    assert "callback=eoRest" in harness.timer_log_lines[0]
    assert fake_st.session_state[pomodoro.POMODORO_OVERLAY_STATE_KEY]["mode"] == "rest"


def test_finalize_post_rest_finish_updates_incomplete_open_task(fake_st):
    task_row = make_task_row()
    harness = FakePomodoroHarness(open_task=task_row, post_work_status="debt")
    fake_st.session_state[pomodoro.REST_MESSAGE_KEY] = "Rest is over."
    fake_st.session_state[pomodoro.REST_RESUME_PROMPT_CONTEXT_KEY] = {
        "previous_work_outcome": "incomplete",
    }
    fake_st.session_state[pomodoro.POMODORO_OVERLAY_STATE_KEY] = {"mode": "rest"}

    pomodoro.finalize_post_rest_finish(
        {"previous_work_outcome": "incomplete"},
        harness.services(),
    )

    assert harness.disabled_timer is True
    assert harness.status_updates == [(task_row, "debt")]
    assert harness.work_ended is True
    assert pomodoro.POMODORO_OVERLAY_STATE_KEY not in fake_st.session_state
    assert pomodoro.REST_MESSAGE_KEY not in fake_st.session_state
    assert pomodoro.REST_RESUME_PROMPT_CONTEXT_KEY not in fake_st.session_state


def test_expire_sprint_clears_overlay_disables_timer_and_queues_review(fake_st):
    harness = FakePomodoroHarness()
    fake_st.session_state[pomodoro.POMODORO_OVERLAY_STATE_KEY] = {"mode": "work"}

    with pytest.raises(RuntimeError, match="rerun"):
        pomodoro.expire_sprint(services=harness.services())

    assert harness.disabled_timer is True
    assert fake_st.session_state[pomodoro.SPRINT_REVIEW_PENDING_KEY] is True
    assert pomodoro.POMODORO_OVERLAY_STATE_KEY not in fake_st.session_state


def test_expire_rest_without_open_task_shows_info_without_rerun(fake_st):
    harness = FakePomodoroHarness(open_task=None)
    fake_st.session_state[pomodoro.POMODORO_OVERLAY_STATE_KEY] = {"mode": "rest"}
    fake_st.session_state[pomodoro.REST_RESUME_PROMPT_CONTEXT_KEY] = {
        "previous_work_outcome": "incomplete",
    }

    pomodoro.expire_rest(services=harness.services())

    assert harness.disabled_timer is True
    assert harness.info_messages == ["Rest is over."]
    assert pomodoro.POMODORO_OVERLAY_STATE_KEY not in fake_st.session_state
    assert pomodoro.REST_RESUME_PROMPT_CONTEXT_KEY not in fake_st.session_state


def test_expire_rest_with_prompt_context_marks_resume_prompt_pending(fake_st):
    harness = FakePomodoroHarness(open_task=make_task_row())
    fake_st.session_state[pomodoro.POMODORO_OVERLAY_STATE_KEY] = {"mode": "rest"}
    fake_st.session_state[pomodoro.REST_RESUME_PROMPT_CONTEXT_KEY] = {
        "previous_work_outcome": "incomplete",
    }

    with pytest.raises(RuntimeError, match="rerun"):
        pomodoro.expire_rest(services=harness.services())

    assert harness.disabled_timer is True
    assert fake_st.session_state[pomodoro.REST_RESUME_PROMPT_PENDING_KEY] is True
    assert pomodoro.POMODORO_OVERLAY_STATE_KEY not in fake_st.session_state


def test_resume_work_after_rest_routes_chunk_resume_to_chunk_service(fake_st):
    task_row = make_task_row()
    harness = FakePomodoroHarness(open_task=task_row)
    fake_st.session_state[pomodoro.REST_MESSAGE_KEY] = "Rest is over."
    fake_st.session_state[pomodoro.REST_RESUME_PROMPT_CONTEXT_KEY] = {
        "previous_work_outcome": "incomplete",
        "work_duration_minutes": 12,
        "resume_cycle_type": "chunk",
    }

    with pytest.raises(RuntimeError, match="rerun"):
        pomodoro.resume_work_after_rest(
            fake_st.session_state[pomodoro.REST_RESUME_PROMPT_CONTEXT_KEY],
            harness.services(),
        )

    assert harness.chunk_cycle_requests == [
        ((task_row,), {"source_label": "chunk_rest_resume_work"})
    ]
    assert harness.scheduled_timers == []
    assert pomodoro.REST_MESSAGE_KEY not in fake_st.session_state
    assert pomodoro.REST_RESUME_PROMPT_CONTEXT_KEY not in fake_st.session_state


def test_resume_work_after_rest_schedules_pomodoro_when_context_is_pomodoro(
    fake_st,
    monkeypatch,
):
    task_row = make_task_row()
    harness = FakePomodoroHarness(open_task=task_row)
    monkeypatch.setattr(pomodoro, "datetime", patch_pomodoro_now(3_000.0))
    prompt_context = {
        "previous_work_outcome": "incomplete",
        "work_duration_minutes": 12,
        "resume_cycle_type": "pomodoro",
    }

    with pytest.raises(RuntimeError, match="rerun"):
        pomodoro.resume_work_after_rest(prompt_context, harness.services())

    assert harness.scheduled_timers[0]["duration_minutes"] == 12
    assert harness.scheduled_timers[0]["source_label"] == "pomodoro_rest_resume_work"
    assert fake_st.session_state[pomodoro.POMODORO_OVERLAY_STATE_KEY]["mode"] == "work"


def test_should_render_pomodoro_session_only_respects_blockers(fake_st):
    fake_st.session_state[pomodoro.POMODORO_OVERLAY_STATE_KEY] = {"mode": "work"}
    running_snapshot = SimpleNamespace(
        running=True,
        expires_at=1_000.0,
        duration_seconds=600,
    )
    services = FakePomodoroHarness(timer_snapshot=running_snapshot).services()

    assert pomodoro.should_render_pomodoro_session_only(services) is True

    fake_st.session_state[pomodoro.SPRINT_REVIEW_PENDING_KEY] = True
    assert pomodoro.should_render_pomodoro_session_only(services) is False

    fake_st.session_state.pop(pomodoro.SPRINT_REVIEW_PENDING_KEY)
    guided_services = FakePomodoroHarness(
        timer_snapshot=running_snapshot,
        guidance_expires_at=1_500.0,
    ).services()
    assert pomodoro.should_render_pomodoro_session_only(guided_services) is False
    assert pomodoro.should_render_pomodoro_session_with_guidance_only(
        guided_services
    ) is True


def test_render_pomodoro_overlay_emits_html_for_running_timer(
    fake_st,
    monkeypatch,
):
    fake_st.session_state[pomodoro.POMODORO_OVERLAY_STATE_KEY] = {
        "mode": "work",
        "cycle_type": "pomodoro",
        "title": "Write notes",
        "description": "Draft the notes",
        "duration_minutes": 25,
        "duration_seconds": 25 * 60,
        "iterations": 2,
    }
    running_snapshot = SimpleNamespace(
        running=True,
        expires_at=1_900.0,
        duration_seconds=25 * 60,
    )
    harness = FakePomodoroHarness(timer_snapshot=running_snapshot)
    monkeypatch.setattr(pomodoro, "datetime", patch_pomodoro_now(1_000.0))

    pomodoro.render_pomodoro_overlay(harness.services())

    # The renderer should emit the user-facing overlay through Streamlit's HTML
    # escape hatch, while keeping the timer and current task visible.
    assert len(fake_st.html_payloads) == 1
    assert "Pomodoro focus" in fake_st.html_payloads[0]
    assert "Write notes" in fake_st.html_payloads[0]
    assert "15:00" in fake_st.html_payloads[0]
