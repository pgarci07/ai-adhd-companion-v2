from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, MutableMapping
import logging
import time


TimerCallback = Callable[..., None]
INACTIVITY_TIMER_KEY = "inactivity_timer"
WORK_TIMER_KEY = "work_timer"
ENABLE_TIMER_LOGGING = True
TIMER_LOG_PATH = Path(__file__).resolve().parents[3] / "logs" / "timers.log"


def get_timer_logger() -> logging.Logger:
    logger = logging.getLogger("ai_adhd.timers")
    if logger.handlers:
        return logger

    TIMER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(TIMER_LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_timer_event(timer: "StreamlitTimer", event: str) -> None:
    if not ENABLE_TIMER_LOGGING:
        return

    state = timer.state
    now = timer._now()
    expires_at = state["expires_at"]
    pre_expires_at = state["pre_expires_at"]

    get_timer_logger().info(
        (
            "%s | timer=%s enabled=%s running=%s duration_seconds=%s "
            "pre_expiry_seconds=%s now=%s expires_at=%s expires_in_seconds=%s "
            "pre_expires_at=%s pre_expires_in_seconds=%s"
        ),
        event,
        timer.name,
        state["enabled"],
        state["running"],
        state["duration_seconds"],
        state["pre_expiry_seconds"],
        _format_timestamp(now),
        _format_timestamp(expires_at),
        _seconds_until(expires_at, now),
        _format_timestamp(pre_expires_at),
        _seconds_until(pre_expires_at, now),
    )


def _format_timestamp(value: float | None) -> str | None:
    if value is None:
        return None

    return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds")


def _seconds_until(value: float | None, now: float) -> float | None:
    if value is None:
        return None

    return round(value - now, 3)


def _normalise_seconds(value: float | int | timedelta | None) -> float | None:
    if value is None:
        return None

    if isinstance(value, timedelta):
        seconds = value.total_seconds()
    else:
        seconds = float(value)

    if seconds < 0:
        raise ValueError("Timer durations cannot be negative.")

    return seconds


def _call_callback(callback: TimerCallback | None, timer: "StreamlitTimer") -> None:
    if callback is None:
        return

    try:
        callback(timer)
    except TypeError:
        callback()


@dataclass
class TimerSnapshot:
    name: str
    enabled: bool
    running: bool
    duration_seconds: float | None
    pre_expiry_seconds: float | None
    expires_at: float | None
    pre_expires_at: float | None
    pre_callback_fired: bool
    expiry_callback_fired: bool


class StreamlitTimer:
    """State-backed timer designed to be checked from Streamlit reruns."""

    def __init__(
        self,
        name: str,
        state: MutableMapping[str, Any],
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.name = name
        self._state_root = state
        self._now = now or time.time
        self._state_root.setdefault(name, self._initial_state())

    @staticmethod
    def _initial_state() -> dict[str, Any]:
        return {
            "enabled": False,
            "running": False,
            "duration_seconds": None,
            "pre_expiry_seconds": None,
            "expires_at": None,
            "pre_expires_at": None,
            "pre_callback_fired": False,
            "expiry_callback_fired": False,
            "callbacks": {
                "on_expiry": None,
                "on_pre_expiry": None,
            },
        }

    @property
    def state(self) -> dict[str, Any]:
        timer_state = self._state_root[self.name]
        initial_state = self._initial_state()

        for key, value in initial_state.items():
            timer_state.setdefault(key, value)

        for key, value in initial_state["callbacks"].items():
            timer_state["callbacks"].setdefault(key, value)

        return timer_state

    def configure(
        self,
        *,
        duration: float | int | timedelta | None = None,
        on_expiry: TimerCallback | None = None,
        pre_expiry: float | int | timedelta | None = None,
        on_pre_expiry: TimerCallback | None = None,
    ) -> None:
        duration_seconds = _normalise_seconds(duration)
        pre_expiry_seconds = _normalise_seconds(pre_expiry)

        if duration_seconds is not None:
            self.state["duration_seconds"] = duration_seconds

        if pre_expiry_seconds is not None:
            self.state["pre_expiry_seconds"] = pre_expiry_seconds

        if on_expiry is not None:
            self.state["callbacks"]["on_expiry"] = on_expiry

        if on_pre_expiry is not None:
            self.state["callbacks"]["on_pre_expiry"] = on_pre_expiry

    def start(
        self,
        *,
        duration: float | int | timedelta | None = None,
        on_expiry: TimerCallback | None = None,
        pre_expiry: float | int | timedelta | None = None,
        on_pre_expiry: TimerCallback | None = None,
    ) -> None:
        self.configure(
            duration=duration,
            on_expiry=on_expiry,
            pre_expiry=pre_expiry,
            on_pre_expiry=on_pre_expiry,
        )
        self._schedule()
        log_timer_event(self, "start")

    def enable(self) -> None:
        self.state["enabled"] = True

    def disable(self) -> None:
        self.stop()
        self.state["enabled"] = False
        log_timer_event(self, "disable")

    def stop(self) -> None:
        self.state["running"] = False
        log_timer_event(self, "stop")

    def restart(self) -> None:
        self._schedule()
        log_timer_event(self, "restart")

    def reset(
        self,
        *,
        duration: float | int | timedelta | None = None,
        on_expiry: TimerCallback | None = None,
        pre_expiry: float | int | timedelta | None = None,
        on_pre_expiry: TimerCallback | None = None,
    ) -> None:
        self.configure(
            duration=duration,
            on_expiry=on_expiry,
            pre_expiry=pre_expiry,
            on_pre_expiry=on_pre_expiry,
        )
        self._schedule()
        log_timer_event(self, "reset")

    def _schedule(self) -> None:
        duration_seconds = self.state["duration_seconds"]
        if duration_seconds is None:
            raise ValueError("Timer duration must be configured before starting.")

        self.enable()
        pre_expiry_seconds = self.state["pre_expiry_seconds"]
        now = self._now()
        expires_at = now + duration_seconds

        self.state.update(
            {
                "running": True,
                "expires_at": expires_at,
                "pre_expires_at": (
                    max(now, expires_at - pre_expiry_seconds)
                    if pre_expiry_seconds is not None
                    else None
                ),
                "pre_callback_fired": False,
                "expiry_callback_fired": False,
            }
        )

    def tick(self) -> None:
        if not self.state["enabled"] or not self.state["running"]:
            return

        now = self._now()

        if (
            self.state["pre_expires_at"] is not None
            and not self.state["pre_callback_fired"]
            and now >= self.state["pre_expires_at"]
        ):
            self.state["pre_callback_fired"] = True
            _call_callback(self.state["callbacks"]["on_pre_expiry"], self)

        if self.state["expires_at"] is not None and now >= self.state["expires_at"]:
            self.state["expiry_callback_fired"] = True
            self.state["running"] = False
            _call_callback(self.state["callbacks"]["on_expiry"], self)

    def remaining_seconds(self) -> float | None:
        if not self.state["running"] or self.state["expires_at"] is None:
            return None

        return max(0.0, self.state["expires_at"] - self._now())

    def snapshot(self) -> TimerSnapshot:
        return TimerSnapshot(
            name=self.name,
            enabled=self.state["enabled"],
            running=self.state["running"],
            duration_seconds=self.state["duration_seconds"],
            pre_expiry_seconds=self.state["pre_expiry_seconds"],
            expires_at=self.state["expires_at"],
            pre_expires_at=self.state["pre_expires_at"],
            pre_callback_fired=self.state["pre_callback_fired"],
            expiry_callback_fired=self.state["expiry_callback_fired"],
        )


class InactivityTimer(StreamlitTimer):
    def mark_user_interaction(self) -> None:
        if not self.state["enabled"]:
            return

        self.reset()

    def tick(self, *, user_interaction: bool = False) -> None:
        super().tick()

        if user_interaction and self.state["running"]:
            self.mark_user_interaction()


class WorkTimer(StreamlitTimer):
    pass


def get_inactivity_timer(
    state: MutableMapping[str, Any],
    *,
    now: Callable[[], float] | None = None,
) -> InactivityTimer:
    return InactivityTimer(INACTIVITY_TIMER_KEY, state, now=now)


def get_work_timer(
    state: MutableMapping[str, Any],
    *,
    now: Callable[[], float] | None = None,
) -> WorkTimer:
    return WorkTimer(WORK_TIMER_KEY, state, now=now)
