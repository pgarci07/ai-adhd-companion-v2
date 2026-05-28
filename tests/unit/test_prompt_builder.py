from app.ui.body_doubling import (
    BodyDoublingServices,
    build_body_doubling_microsteps_prompt,
    normalise_body_doubling_microsteps,
)


def make_services() -> BodyDoublingServices:
    """Create the minimal service bundle needed by prompt-builder tests."""

    return BodyDoublingServices(
        get_user_preferences=lambda: {
            "average_session_time": 45,
            "custom_sizes": [15, 30, 60, 180, 720],
        },
        update_task_status=lambda task, status: None,
        log_openai_event=lambda *args, **kwargs: None,
        get_openai_logger=lambda: None,
        extract_openai_text=lambda response: None,
        openai_class=None,
        openai_model="test-model",
        schedule_work_timer=lambda duration, callback, source: None,
        disable_work_timer=lambda: None,
        get_work_timer_snapshot=lambda: None,
        get_effective_current_state_name=lambda: "Frozen",
        get_current_persona_profile_context=lambda: {
            "persona_name": "hyper-focused",
            "persona_description": "Can sustain intense focus once momentum starts.",
            "age": 34,
        },
    )


def test_body_doubling_microsteps_prompt_contains_current_user_context():
    task_row = {
        "title": "Prepare tax folder",
        "description": "Collect receipts and bank statements.",
        "WSUB": 8,
        "size_weight": 4,
        "friction_weight": 2,
        "size_minutes": 60,
    }

    prompt = build_body_doubling_microsteps_prompt(
        task_row,
        "easy to begin right now",
        make_services(),
    )

    assert "Persona/profile: hyper-focused" in prompt
    assert "Persona/profile description: Can sustain intense focus once momentum starts." in prompt
    assert "- Age: 34" in prompt
    assert "- Current state: Frozen" in prompt
    assert "- Title: Prepare tax folder" in prompt
    assert "- Estimated duration in minutes: 60" in prompt
    assert "not silly, patronising, or trivial" in prompt


def test_normalise_body_doubling_microsteps_keeps_order_and_sanitises_duration():
    payload = {
        "microsteps": [
            {
                "order": 2,
                "name": "Open the folder",
                "description": "Just get the folder on screen.",
                "estimated_duration_minutes": "3",
            },
            {
                "order": 1,
                "title": "Find receipts",
                "details": "Locate the receipts pile first.",
                "duration_minutes": "oops",
            },
        ]
    }

    normalised = normalise_body_doubling_microsteps(payload)

    assert [step["order"] for step in normalised] == [1, 2]
    assert normalised[0]["name"] == "Find receipts"
    assert normalised[0]["description"] == "Locate the receipts pile first."
    assert normalised[0]["estimated_duration_minutes"] == 5
    assert normalised[1]["estimated_duration_minutes"] == 3
