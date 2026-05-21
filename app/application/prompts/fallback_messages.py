"""Local fallback copy used when OpenAI prompt generation is unavailable."""

from __future__ import annotations


LOGOUT_FAREWELL_PROMPT_FALLBACK = (
    "Write a concise British English farewell message for a user who is "
    "about to log out of AI-ADHD. Congratulate completed work, or "
    "encourage them to return without shame if nothing was completed."
)

REGISTRATION_WELCOME_PROMPT_FALLBACK = (
    "Write a warm, concise British English welcome message for a newly registered "
    "AI-ADHD user. Be encouraging and practical."
)


def build_logout_farewell_fallback(counters):
    """Return local logout farewell copy when OpenAI is unavailable."""

    completed_tasks = int(counters.get("completed_tasks", 0) or 0)
    microsteps_completed = int(counters.get("microsteps_completed", 0) or 0)
    if completed_tasks or microsteps_completed:
        progress_bits = []
        if completed_tasks:
            progress_bits.append(f"{completed_tasks} task{'s' if completed_tasks != 1 else ''}")
        if microsteps_completed:
            progress_bits.append(
                f"{microsteps_completed} micro-step{'s' if microsteps_completed != 1 else ''}"
            )
        return (
            f"Good work today. You completed {' and '.join(progress_bits)}, "
            "and that counts.\n\nCome back when you are ready; the next step can stay small."
        )
    return (
        "Thanks for checking in today. If the tasks did not move yet, that is still useful "
        "information: come back when you can and we will make the next step smaller."
    )


def build_registration_welcome_fallback(first_name):
    """Return local registration welcome copy when OpenAI is unavailable."""

    display_name = first_name or "there"
    return (
        f"Welcome, {display_name}. I am glad you are here: we will help you turn "
        "your tasks into clearer, smaller, more manageable steps.\n\n"
        "Before we begin, tell us how you are arriving to this session so we can "
        "adapt the plan to your energy and the time you have available."
    )


def build_open_task_guidance_fallback(task_title, use_pomodoro_sprints, use_body_doubling):
    """Return local open-task guidance copy when OpenAI is unavailable."""

    timing_text = (
        "Work with the sprint timer: focus on just the next small step until it rings."
        if use_pomodoro_sprints
        else "Use this work chunk to make steady progress without worrying about finishing everything."
    )
    body_doubling_text = (
        "If body-doubling helps, keep someone nearby or visible and let their presence anchor you."
        if use_body_doubling
        else "You can do this solo: keep the task visible and remove one distraction before starting."
    )
    return (
        f"Task opened: {task_title}.\n\n"
        f"{timing_text} {body_doubling_text}"
    )
