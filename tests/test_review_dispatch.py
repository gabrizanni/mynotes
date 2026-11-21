"""Concurrency limits and fail-stop behavior without model calls."""
from threading import Barrier, Event, Lock

import pytest

from mynotes.review_dispatch import dispatch, cancel_event


def test_independent_packets_overlap_but_results_keep_source_order():
    gate = Barrier(2)
    lock = Lock()
    active = peak = 0
    def work(value):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        gate.wait(timeout=3)
        with lock:
            active -= 1
        return value * 10
    assert dispatch([1,2,3,4],work,lambda value:{value}) == [10,20,30,40]
    assert peak == 2
    assert cancel_event.get() is None


def test_overlapping_source_graphics_never_run_together():
    active = set()
    def work(value):
        assert not active
        active.add(value)
        active.remove(value)
        return value
    assert dispatch(range(4),work,lambda value:{'same-slide'}) == list(range(4))


def test_blocking_packet_cancels_running_transport_and_does_not_start_queue():
    gate = Barrier(2)
    cancelled = Event()
    started = []
    def work(value):
        started.append(value)
        gate.wait(timeout=3)
        if value == 0:
            raise ValueError('blocking source problem')
        assert cancel_event.get().wait(timeout=3)
        cancelled.set()
    with pytest.raises(ValueError,match='blocking source problem'):
        dispatch(range(4),work,lambda value:{value})
    assert set(started) == {0,1}
    assert cancelled.is_set()


def test_failed_packet_does_not_release_a_conflicting_queued_packet():
    called=[]
    def work(value):
        called.append(value)
        raise ValueError('stop')
    with pytest.raises(ValueError):
        dispatch([0,1],work,lambda value:{'same-block'})
    assert called == [0]


@pytest.mark.parametrize('workers',[0,3,-1])
def test_worker_limit_cannot_silently_exceed_two(workers):
    with pytest.raises(ValueError,match='one or two'):
        dispatch([1],lambda value:value,lambda value:{value},workers=workers)
