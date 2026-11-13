"""Deterministic bounded overlap; no models and no timing benchmarks."""
from threading import Event, Thread
import pytest
from mynotes.section_pipeline import SectionPipeline


def test_first_gate_and_two_unverified_sections_apply_backpressure():
    entered=Event();release=Event();returned=Event();order=[]
    def check(item):
        if item==1:
            entered.set()
            assert release.wait(3)
        order.append(item)
    pipeline=SectionPipeline(check)
    try:
        pipeline.submit(0,first=True)
        assert order==[0]
        pipeline.submit(1)
        assert entered.wait(3)
        def writer():
            pipeline.submit(2)
            returned.set()
        thread=Thread(target=writer);thread.start()
        assert not returned.wait(.05)
        release.set();thread.join(3)
        assert returned.is_set()
        pipeline.drain()
        assert order==[0,1,2]
    finally:
        release.set();pipeline.close(cancel=True)


def test_blocking_review_stops_later_sections():
    def check(item):raise ValueError('missing source')
    pipeline=SectionPipeline(check)
    try:
        with pytest.raises(ValueError,match='missing source'):
            pipeline.submit(0,first=True)
        assert pipeline.stopped.is_set()
        with pytest.raises(ValueError):pipeline.submit(1)
    finally:pipeline.close(cancel=True)
