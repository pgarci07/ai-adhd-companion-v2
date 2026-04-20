from datetime import timedelta

from app.ui.state.timers import get_inactivity_timer, get_work_timer


class FakeClock:
    def __init__(self):
        self.value = 1000.0

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def test_work_timer_calls_pre_expiry_and_expiry_callbacks():
    state = {}
    clock = FakeClock()
    events = []
    timer = get_work_timer(state, now=clock.now)

    timer.start(
        duration=10,
        pre_expiry=3,
        on_pre_expiry=lambda timer: events.append(("pre", timer.name)),
        on_expiry=lambda timer: events.append(("expiry", timer.name)),
    )

    clock.advance(6)
    timer.tick()
    assert events == []

    clock.advance(1)
    timer.tick()
    assert events == [("pre", "work_timer")]

    clock.advance(3)
    timer.tick()
    assert events == [("pre", "work_timer"), ("expiry", "work_timer")]
    assert timer.snapshot().running is False


def test_timer_reset_keeps_existing_configuration_when_not_replaced():
    state = {}
    clock = FakeClock()
    events = []
    timer = get_work_timer(state, now=clock.now)

    timer.start(
        duration=timedelta(seconds=5),
        on_expiry=lambda: events.append("expired"),
    )
    clock.advance(3)
    timer.reset()
    clock.advance(4)
    timer.tick()
    assert events == []

    clock.advance(1)
    timer.tick()
    assert events == ["expired"]


def test_timer_reset_can_replace_duration_and_callback():
    state = {}
    clock = FakeClock()
    events = []
    timer = get_work_timer(state, now=clock.now)

    timer.start(duration=20, on_expiry=lambda: events.append("old"))
    timer.reset(duration=2, on_expiry=lambda: events.append("new"))

    clock.advance(2)
    timer.tick()

    assert events == ["new"]


def test_timer_stop_prevents_callbacks_until_restarted():
    state = {}
    clock = FakeClock()
    events = []
    timer = get_work_timer(state, now=clock.now)

    timer.start(duration=1, on_expiry=lambda: events.append("expired"))
    timer.stop()
    clock.advance(2)
    timer.tick()

    assert events == []
    timer.restart()
    clock.advance(1)
    timer.tick()
    assert events == ["expired"]


def test_inactivity_timer_can_reset_on_user_interaction():
    state = {}
    clock = FakeClock()
    events = []
    timer = get_inactivity_timer(state, now=clock.now)

    timer.start(duration=5, on_expiry=lambda: events.append("inactive"))
    clock.advance(3)
    timer.tick(user_interaction=True)
    clock.advance(3)
    timer.tick()
    assert events == []

    clock.advance(2)
    timer.tick()
    assert events == ["inactive"]


def test_disabled_timer_ignores_tick_and_user_interaction():
    state = {}
    clock = FakeClock()
    events = []
    timer = get_inactivity_timer(state, now=clock.now)

    timer.start(duration=5, on_expiry=lambda: events.append("inactive"))
    timer.disable()
    clock.advance(10)
    timer.tick(user_interaction=True)

    assert events == []
    assert timer.snapshot().enabled is False
    assert timer.snapshot().running is False


def test_reset_reenables_disabled_timer():
    state = {}
    clock = FakeClock()
    events = []
    timer = get_work_timer(state, now=clock.now)

    timer.start(duration=5, on_expiry=lambda: events.append("expired"))
    timer.disable()
    timer.reset()
    clock.advance(5)
    timer.tick()

    assert events == ["expired"]
    assert timer.snapshot().enabled is True
