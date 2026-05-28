from __future__ import annotations

import pytest

from app.application.use_cases import task_state_machine as fsm


class FakeResponse:
    """Tiny Supabase response object with only the `.data` field the FSM reads."""

    def __init__(self, data=None):
        self.data = data


class FakeRpcRequest:
    """Fluent RPC request that records the call and returns configured data."""

    def __init__(self, client, name, payload):
        self.client = client
        self.name = name
        self.payload = payload

    def execute(self):
        self.client.rpc_calls.append((self.name, self.payload))
        return FakeResponse(self.client.rpc_responses.get(self.name, []))


class FakeTableRequest:
    """Fluent table request used by the clone path in the task FSM."""

    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.insert_payload = None

    def insert(self, payload):
        self.insert_payload = payload
        return self

    def execute(self):
        self.client.table_inserts.append((self.table_name, self.insert_payload))
        return FakeResponse(self.client.table_insert_responses.get(self.table_name, []))


class FakeSupabase:
    """Supabase test double scoped to the calls made by LoggedTaskModel."""

    def __init__(self):
        self.rpc_calls = []
        self.table_inserts = []
        self.rpc_responses = {}
        self.table_insert_responses = {}

    def rpc(self, name, payload=None):
        return FakeRpcRequest(self, name, payload)

    def table(self, table_name):
        return FakeTableRequest(self, table_name)


def make_task_row(
    *,
    instance_id="instance-1",
    task_id="task-1",
    status=fsm.READY_STATUS,
    parent_instance_id=None,
    has_subtasks=False,
    rrule=None,
):
    """Build only the row fields the task FSM needs for transition tests."""

    return {
        "instance_id": instance_id,
        "task_id": task_id,
        "user_id": "user-1",
        "instance_number": 1,
        "parent_instance_id": parent_instance_id,
        "start_date": "2026-05-20T08:00:00+00:00",
        "due_date": "2026-05-20T18:00:00+00:00",
        "status": status,
        "has_subtasks": has_subtasks,
        "rrule": rrule,
        "WOBJ": 7,
        "WSUB": 3,
        "urgency": 5,
        "is_routine": False,
    }


@pytest.mark.parametrize(
    ("current_status", "target_status", "expected_event", "expected_action"),
    [
        (fsm.READY_STATUS, fsm.OPEN_STATUS, fsm.ACTIVATE_WORK_EVENT, 2),
        (fsm.OPEN_STATUS, fsm.ASLEEP_STATUS, fsm.PAUSE_TASK_EVENT, 3),
        (fsm.ASLEEP_STATUS, fsm.OPEN_STATUS, fsm.RESUME_TASK_EVENT, 4),
        (fsm.READY_STATUS, fsm.COMPLETED_STATUS, fsm.MARK_COMPLETED_EVENT, 5),
        (fsm.OPEN_STATUS, fsm.DEBT_STATUS, fsm.MARK_DEBT_EVENT, 6),
        (fsm.ASLEEP_STATUS, fsm.DEBT_STATUS, fsm.MARK_DEBT_FROM_ASLEEP_EVENT, 7),
        (fsm.DEBT_STATUS, fsm.OPEN_STATUS, fsm.REOPEN_DEBT_EVENT, 8),
        (fsm.DEBT_STATUS, fsm.STALE_STATUS, fsm.MARK_STALE_EVENT, 9),
    ],
)
def test_transition_matrix_maps_allowed_status_changes_to_domain_actions(
    current_status,
    target_status,
    expected_event,
    expected_action,
):
    # The static transition matrix is the core contract between UI buttons,
    # persisted status-log action ids, and scheduler-driven status aging.
    decision = fsm.LoggedTaskModel.transition(current_status, target_status)

    assert decision.event == expected_event
    assert decision.action_id == expected_action
    assert decision.target_status == target_status
    assert decision.requires_clone is False


def test_parent_tasks_with_subtasks_cannot_be_opened_directly():
    # Compound parents are containers. Work should happen on their child tasks,
    # so opening the parent directly would create an ambiguous active task.
    with pytest.raises(fsm.TaskTransitionError, match="parent task with subtasks"):
        fsm.LoggedTaskModel.transition(
            fsm.READY_STATUS,
            fsm.OPEN_STATUS,
            has_subtasks=True,
        )


def test_recurring_stale_task_cannot_be_reopened_manually():
    # A stale recurring task should wait for the scheduler to produce the next
    # occurrence; manually cloning it would bypass the recurrence policy.
    with pytest.raises(fsm.TaskTransitionError, match="Recurring stale tasks"):
        fsm.LoggedTaskModel.transition(
            fsm.STALE_STATUS,
            fsm.OPEN_STATUS,
            is_recurrent=True,
        )


def test_transition_to_completed_propagates_terminal_status_to_descendants():
    client = FakeSupabase()
    model = fsm.LoggedTaskModel(supabase_client=client)
    parent = make_task_row(instance_id="parent", task_id="parent-task", has_subtasks=True)
    child = make_task_row(instance_id="child", parent_instance_id="parent")
    grandchild = make_task_row(instance_id="grandchild", parent_instance_id="child")
    already_completed_child = make_task_row(
        instance_id="already-completed",
        parent_instance_id="parent",
        status=fsm.COMPLETED_STATUS,
    )

    result = model.transition_to_status(
        parent,
        fsm.COMPLETED_STATUS,
        task_rows=[parent, child, grandchild, already_completed_child],
    )

    # The parent changes first, then only descendants that are not already in
    # the target terminal state receive their own status-log entry.
    assert result.changed is True
    assert result.propagated_instance_ids == ("child", "grandchild")
    assert client.rpc_calls == [
        (
            "set_task_instance_status",
            {"p_instance_id": "parent", "p_new_status": fsm.COMPLETED_STATUS},
        ),
        (
            "set_task_instance_status",
            {"p_instance_id": "child", "p_new_status": fsm.COMPLETED_STATUS},
        ),
        (
            "set_task_instance_status",
            {"p_instance_id": "grandchild", "p_new_status": fsm.COMPLETED_STATUS},
        ),
    ]


def test_opening_ready_task_emits_user_state_task_opened_event():
    client = FakeSupabase()
    model = fsm.LoggedTaskModel(supabase_client=client)
    task_row = make_task_row(status=fsm.READY_STATUS)

    result = model.transition_to_status(task_row, fsm.OPEN_STATUS, task_rows=[task_row])

    # Task-status changes are the source of task-opened notifications consumed
    # by the user-state FSM during the current session.
    assert result.user_state_events[0].event_name == fsm.USER_STATE_TASK_OPENED_EVENT
    assert result.user_state_events[0].payload["previous_status"] == fsm.READY_STATUS
    assert result.user_state_events[0].payload["current_status"] == fsm.OPEN_STATUS


def test_reopening_completed_task_clones_historical_instance_then_opens_clone():
    client = FakeSupabase()
    client.table_insert_responses["task_instances"] = [
        {
            "id": "new-instance",
            "start_date": "2026-05-24T08:00:00+00:00",
            "due_date": "2026-05-24T18:00:00+00:00",
        }
    ]
    model = fsm.LoggedTaskModel(supabase_client=client)
    completed_row = make_task_row(status=fsm.COMPLETED_STATUS)

    result = model.transition_to_status(
        completed_row,
        fsm.OPEN_STATUS,
        task_rows=[completed_row],
        reopened_start_at="2026-05-24T08:00:00+00:00",
        reopened_due_at="2026-05-24T18:00:00+00:00",
    )

    # Completed history stays immutable. The FSM creates an exception instance
    # and applies the Open status only to that new row.
    assert result.created_instance_id == "new-instance"
    assert result.effective_task_row["instance_id"] == "new-instance"
    assert result.ui_message == "A new task instance was created and opened with the new schedule."
    assert client.table_inserts == [
        (
            "task_instances",
            {
                "task_id": "task-1",
                "user_id": "user-1",
                "instance_number": 1,
                "parent_instance_id": None,
                "start_date": "2026-05-24T08:00:00+00:00",
                "due_date": "2026-05-24T18:00:00+00:00",
                "is_exception": True,
                "original_start_date": "2026-05-20T08:00:00+00:00",
                "original_due_date": "2026-05-20T18:00:00+00:00",
            },
        )
    ]
    assert client.rpc_calls == [
        (
            "set_task_instance_status",
            {"p_instance_id": "new-instance", "p_new_status": fsm.OPEN_STATUS},
        )
    ]


def test_parent_completes_when_all_children_are_completed():
    client = FakeSupabase()
    client.rpc_responses["get_user_task_rows"] = [
        {
            "instance_id": "parent",
            "task_id": "parent-task",
            "status": fsm.READY_STATUS,
        },
        {
            "instance_id": "child-1",
            "task_id": "child-task-1",
            "parent_instance_id": "parent",
            "status": fsm.COMPLETED_STATUS,
        },
        {
            "instance_id": "child-2",
            "task_id": "child-task-2",
            "parent_instance_id": "parent",
            "status": fsm.COMPLETED_STATUS,
        },
    ]
    model = fsm.LoggedTaskModel(supabase_client=client)

    changed = model.maybe_complete_parent_from_children(
        {"instance_id": "child-1", "parent_instance_id": "parent"}
    )

    # Parent completion is derived from the current sibling snapshot, not from
    # the child row alone.
    assert changed is True
    assert client.rpc_calls[-1] == (
        "set_task_instance_status",
        {"p_instance_id": "parent", "p_new_status": fsm.COMPLETED_STATUS},
    )
