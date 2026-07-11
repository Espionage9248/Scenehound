from scenehound.rate_limiter import TokenBucket


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_burst_then_deny():
    clock = FakeClock()
    b = TokenBucket(burst=4, refill_seconds=15.0, clock=clock)
    assert [b.try_acquire() for _ in range(4)] == [True] * 4
    assert b.try_acquire() is False


def test_refills_one_token_per_interval():
    clock = FakeClock()
    b = TokenBucket(burst=4, refill_seconds=15.0, clock=clock)
    for _ in range(4):
        b.try_acquire()
    clock.now = 14.9
    assert b.try_acquire() is False
    clock.now = 15.0
    assert b.try_acquire() is True
    assert b.try_acquire() is False


def test_never_exceeds_burst_capacity():
    clock = FakeClock()
    b = TokenBucket(burst=2, refill_seconds=1.0, clock=clock)
    clock.now = 1000.0
    assert [b.try_acquire() for _ in range(3)] == [True, True, False]
