"""The transient-retry loop every adapter shares."""

import pytest

from skill_lens.runners.retry import TRANSIENT_STATUSES, run_with_retries, transient_status


class Transient(Exception):
    pass


class Permanent(Exception):
    pass


def is_transient(exc: Exception) -> bool:
    return isinstance(exc, Transient)


def test_a_call_that_succeeds_is_returned_without_sleeping():
    slept = []
    value = run_with_retries(lambda: "ok", is_transient, 2, 1.0, slept.append)
    assert value == "ok"
    assert slept == []


def test_a_transient_failure_is_retried_and_can_succeed():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise Transient()
        return "recovered"

    slept = []
    assert run_with_retries(flaky, is_transient, 2, 0.01, slept.append) == "recovered"
    assert slept == [0.01]


def test_backoff_doubles_between_attempts_and_the_last_failure_is_raised():
    def always():
        raise Transient("still down")

    slept = []
    with pytest.raises(Transient, match="still down"):
        run_with_retries(always, is_transient, 3, 1.0, slept.append)
    assert slept == [1.0, 2.0, 4.0]


def test_a_permanent_failure_is_raised_at_once():
    attempts = {"n": 0}

    def unauthorized():
        attempts["n"] += 1
        raise Permanent()

    slept = []
    with pytest.raises(Permanent):
        run_with_retries(unauthorized, is_transient, 3, 0.01, slept.append)
    assert attempts["n"] == 1
    assert slept == []


def test_zero_retries_means_one_attempt():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        raise Transient()

    with pytest.raises(Transient):
        run_with_retries(flaky, is_transient, 0, 0.01, lambda _: None)
    assert attempts["n"] == 1


@pytest.mark.parametrize("status", sorted(TRANSIENT_STATUSES) + [500, 502, 503, 599])
def test_rate_limits_timeouts_conflicts_and_server_errors_are_transient(status):
    assert transient_status(status) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_transient(status):
    assert transient_status(status) is False
