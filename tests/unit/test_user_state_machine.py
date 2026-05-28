from __future__ import annotations

from app.application.use_cases import user_state_machine as fsm


STATE_IDS = {
    fsm.PLANNER_STATE: 1,
    fsm.FROZEN_STATE: 2,
    fsm.ENGAGED_STATE: 3,
    fsm.RECOVERY_STATE: 4,
}


class FakeResponse:
    """Tiny Supabase response object with only the `.data` field the FSM reads."""

    def __init__(self, data=None):
        self.data = data


class FakeRpcRequest:
    """Fluent RPC request that records calls made by the user FSM."""

    def __init__(self, client, name, payload):
        self.client = client
        self.name = name
        self.payload = payload

    def execute(self):
        self.client.rpc_calls.append((self.name, self.payload))
        return FakeResponse()


class FakeSupabase:
    """Supabase test double scoped to `set_user_state` RPC calls."""

    def __init__(self):
        self.rpc_calls = []

    def rpc(self, name, payload=None):
        return FakeRpcRequest(self, name, payload)


def make_model(session_store=None):
    """Create a user FSM model with in-memory session state and fake Supabase."""

    client = FakeSupabase()
    session = session_store if session_store is not None else {}
    return fsm.LoggedUserModel(supabase_client=client, session_store=session), client, session


def saved_context(session):
    """Read the serialised context exactly as Streamlit would store it."""

    return session[fsm.USER_FSM_CONTEXT_KEY]


def test_login_can_start_in_planner_and_remember_declared_execution_state():
    model, client, session = make_model()

    result = model.transition(
        user_id="user-1",
        current_state_name=None,
        state_id_by_name=STATE_IDS,
        event=fsm.LOGIN_DECLARED_EVENT,
        declared_state=fsm.FROZEN_STATE,
        start_in_planner=True,
    )

    # Planner is a temporary state. `memory_state` keeps the state the user
    # declared so opening work can return them to the right execution mode.
    assert result.current_state == fsm.PLANNER_STATE
    assert result.current_state_id == STATE_IDS[fsm.PLANNER_STATE]
    assert result.start_planner_timer is True
    assert result.context.memory_state == fsm.FROZEN_STATE
    assert saved_context(session)["memory_state"] == fsm.FROZEN_STATE
    assert client.rpc_calls == [
        (
            "set_user_state",
            {
                "p_user_id": "user-1",
                "p_state_id": STATE_IDS[fsm.PLANNER_STATE],
                "p_experienced_at": None,
            },
        )
    ]


def test_task_opened_from_planner_restores_remembered_engaged_state():
    session = {
        fsm.USER_FSM_CONTEXT_KEY: fsm.UserStateMachineContext(
            current_state=fsm.PLANNER_STATE,
            memory_state=fsm.ENGAGED_STATE,
        ).to_dict()
    }
    model, client, _ = make_model(session)

    result = model.transition(
        user_id="user-1",
        current_state_name=fsm.PLANNER_STATE,
        state_id_by_name=STATE_IDS,
        event=fsm.TASK_OPENED_EVENT,
    )

    # Opening a task consumes Planner and returns to the remembered productive
    # state unless an adaptive flow explicitly overrides that target.
    assert result.previous_state == fsm.PLANNER_STATE
    assert result.current_state == fsm.ENGAGED_STATE
    assert result.stop_planner_timer is True
    assert result.context.memory_state == fsm.ENGAGED_STATE
    assert client.rpc_calls[-1][1]["p_state_id"] == STATE_IDS[fsm.ENGAGED_STATE]


def test_task_rejection_threshold_returns_user_to_planner_and_clears_runtime_overrides():
    session = {
        fsm.USER_FSM_CONTEXT_KEY: fsm.UserStateMachineContext(
            current_state=fsm.FROZEN_STATE,
            memory_state=fsm.FROZEN_STATE,
        ).to_dict()
    }
    model, client, session = make_model(session)
    model.set_runtime_parameter_overrides({"Z": 1})

    result = model.transition(
        user_id="user-1",
        current_state_name=fsm.FROZEN_STATE,
        state_id_by_name=STATE_IDS,
        event=fsm.TASK_REJECTED_EVENT,
    )

    # The adaptive override lowers the rejection threshold for this transition
    # only. The model must remove it in `finally` so it cannot leak forward.
    assert result.current_state == fsm.PLANNER_STATE
    assert result.start_planner_timer is True
    assert result.context.memory_state == fsm.FROZEN_STATE
    assert result.ui_messages[0].message_id == "STATE_RETURNED_TO_PLANNER_AFTER_REJECTIONS"
    assert result.ui_messages[0].params == {"threshold": 1}
    assert fsm.USER_FSM_PARAMETER_OVERRIDES_KEY not in session
    assert client.rpc_calls[-1][1]["p_state_id"] == STATE_IDS[fsm.PLANNER_STATE]


def test_frozen_moves_to_engaged_after_completed_task_threshold():
    session = {
        fsm.USER_FSM_CONTEXT_KEY: fsm.UserStateMachineContext(
            current_state=fsm.FROZEN_STATE,
            memory_state=fsm.FROZEN_STATE,
        ).to_dict()
    }
    model, client, _ = make_model(session)

    result = model.transition(
        user_id="user-1",
        current_state_name=fsm.FROZEN_STATE,
        state_id_by_name=STATE_IDS,
        event=fsm.TASK_COMPLETED_EVENT,
        preferences={"state_tasks_threshold": 1},
    )

    # Completing work is the main positive momentum path from Frozen into
    # Engaged. The result also tells the UI which adaptive callback to run.
    assert result.current_state == fsm.ENGAGED_STATE
    assert result.context.completed_tasks_in_session == 1
    assert result.callbacks == ["frozen_to_engaged"]
    assert result.ui_messages[0].message_id == "STATE_FROZEN_TO_ENGAGED_MOMENTUM"
    assert client.rpc_calls[-1][1]["p_state_id"] == STATE_IDS[fsm.ENGAGED_STATE]


def test_work_ended_returns_execution_state_to_planner_with_memory():
    session = {
        fsm.USER_FSM_CONTEXT_KEY: fsm.UserStateMachineContext(
            current_state=fsm.ENGAGED_STATE,
            memory_state=fsm.ENGAGED_STATE,
            consecutive_rejected_tasks=1,
        ).to_dict()
    }
    model, client, _ = make_model(session)

    result = model.transition(
        user_id="user-1",
        current_state_name=fsm.ENGAGED_STATE,
        state_id_by_name=STATE_IDS,
        event=fsm.WORK_ENDED_EVENT,
    )

    # Ending work does not mean Recovery. The user returns to Planner while the
    # previous execution state is preserved for the next task-open event.
    assert result.current_state == fsm.PLANNER_STATE
    assert result.context.memory_state == fsm.ENGAGED_STATE
    assert result.context.consecutive_rejected_tasks == 0
    assert result.start_planner_timer is True
    assert result.ui_messages[0].message_id == "STATE_RETURNED_TO_PLANNER_AFTER_WORK_ENDED"
    assert client.rpc_calls[-1][1]["p_state_id"] == STATE_IDS[fsm.PLANNER_STATE]


def test_planner_timer_warns_before_escalating_to_recovery():
    session = {
        fsm.USER_FSM_CONTEXT_KEY: fsm.UserStateMachineContext(
            current_state=fsm.PLANNER_STATE,
            memory_state=fsm.FROZEN_STATE,
        ).to_dict()
    }
    model, client, _ = make_model(session)

    first_tick = model.transition(
        user_id="user-1",
        current_state_name=fsm.PLANNER_STATE,
        state_id_by_name=STATE_IDS,
        event=fsm.PLANNER_TIMER_ELAPSED_EVENT,
        preferences={"planner_warning_limit": 1, "planner_minutes": 5},
    )
    second_tick = model.transition(
        user_id="user-1",
        current_state_name=fsm.PLANNER_STATE,
        state_id_by_name=STATE_IDS,
        event=fsm.PLANNER_TIMER_ELAPSED_EVENT,
        preferences={"planner_warning_limit": 1, "planner_minutes": 5},
    )

    # The first expiry is a warning and timer reset. Only the next expiry after
    # the warning budget is exhausted escalates the session to Recovery.
    assert first_tick.current_state == fsm.PLANNER_STATE
    assert first_tick.reset_planner_timer is True
    assert first_tick.ui_messages[0].message_id == "PLANNER_REMINDER"
    assert first_tick.ui_messages[0].params["minutes_until_recovery"] == 5
    assert second_tick.current_state == fsm.RECOVERY_STATE
    assert second_tick.stop_planner_timer is True
    assert second_tick.requires_recovery_cleanup is True
    assert second_tick.should_end_session is True
    assert second_tick.ui_messages[0].message_id == "PLANNER_LIMIT_REACHED"
    assert client.rpc_calls[-1][1]["p_state_id"] == STATE_IDS[fsm.RECOVERY_STATE]


def test_logout_moves_active_session_to_recovery_and_builds_summary():
    session = {
        fsm.USER_FSM_CONTEXT_KEY: fsm.UserStateMachineContext(
            current_state=fsm.ENGAGED_STATE,
            memory_state=fsm.ENGAGED_STATE,
            completed_tasks_in_session=2,
        ).to_dict()
    }
    model, client, _ = make_model(session)

    result = model.transition(
        user_id="user-1",
        current_state_name=fsm.ENGAGED_STATE,
        state_id_by_name=STATE_IDS,
        event=fsm.LOGOUT_EVENT,
        session_end_reason="manual_logout",
    )

    # Logout closes an active session into Recovery and returns a summary that
    # the UI can show without recalculating counters from session state.
    assert result.current_state == fsm.RECOVERY_STATE
    assert result.context.memory_state == fsm.ENGAGED_STATE
    assert result.stop_planner_timer is True
    assert result.should_end_session is True
    assert "reason=manual_logout" in result.summary_message
    assert "completed_tasks=2" in result.summary_message
    assert client.rpc_calls[-1][1]["p_state_id"] == STATE_IDS[fsm.RECOVERY_STATE]
