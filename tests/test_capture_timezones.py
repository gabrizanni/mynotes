"""Calendar instants and source timezone survive host changes and DST."""
import os
import time
import pytest
from mynotes import capture
from test_capture import subscription, feed, component


def timed(start, end, extra=""):
    return component(extra=extra).replace("DTSTART:20261012T100000Z", "DTSTART"+start).replace("DTEND:20261012T110000Z", "DTEND"+end)


@pytest.mark.parametrize("mode", ["mapped", "all_timed"])
@pytest.mark.parametrize("host", ["UTC", "Europe/Rome", "Asia/Kolkata", "America/New_York"])
def test_floating_event_is_localized_before_window_filtering(mode, host):
    previous=os.environ.get("TZ")
    try:
        os.environ["TZ"]=host;time.tzset()
        source=subscription(importMode=mode,timezone="Asia/Kolkata",periodStart="2026-11-08T00:00:00Z",periodEnd="2026-11-08T16:00:00Z")
        events=capture.calendar_events(feed(timed(":20261108T190000",":20261108T200000")),source,[],{"MATH"})
        assert len(events)==1
        assert events[0]["startsAt"]=="2026-11-08T13:30:00Z"
        assert events[0]["timezone"]=="Asia/Kolkata"
    finally:
        if previous is None:os.environ.pop("TZ",None)
        else:os.environ["TZ"]=previous
        time.tzset()


@pytest.mark.parametrize("mode", ["mapped", "all_timed"])
def test_explicit_event_timezone_overrides_subscription_and_handles_dst(mode):
    source=subscription(importMode=mode,timezone="Asia/Kolkata")
    data=feed(timed(";TZID=Europe/Rome:20261023T100000",";TZID=Europe/Rome:20261023T110000","RRULE:FREQ=WEEKLY;COUNT=2\r\n"))
    events=capture.calendar_events(data,source,[],{"MATH"})
    assert [e["startsAt"] for e in events]==["2026-10-23T08:00:00Z","2026-10-30T09:00:00Z"]
    assert {e["timezone"] for e in events}=={"Europe/Rome"}
    assert [e["id"] for e in capture.calendar_events(data,source,events,{"MATH"})]==[e["id"] for e in events]


@pytest.mark.parametrize("zone,date", [("Unknown/Zone","20261012"),("Europe/Rome","20261025"),("Europe/Rome","20260329")])
def test_unsafe_explicit_zone_or_dst_time_is_reported_not_interpreted(zone,date):
    source=subscription(importMode="all_timed",timezone="Asia/Kolkata",periodStart="2026-01-01T00:00:00Z")
    report={}
    data=feed(timed(f";TZID={zone}:{date}T023000",f";TZID={zone}:{date}T033000"),component(uid="valid"))
    events=capture.calendar_events(data,source,[],{"MATH"},report=report)
    assert [e["calendarUID"] for e in events]==["valid"]
    assert report["issues"][0]["code"]=="INVALID_TIMEZONE"


def test_explicit_utc_does_not_adopt_subscription_timezone():
    events=capture.calendar_events(feed(component()),subscription(importMode="all_timed",timezone="Asia/Kolkata"),[],{"MATH"})
    assert events[0]["startsAt"]=="2026-10-12T10:00:00Z"
    assert events[0]["timezone"]=="UTC"


@pytest.mark.parametrize("mode", ["mapped", "all_timed"])
def test_floating_recurrence_keeps_wall_time_through_dst_and_exclusion(mode):
    source=subscription(importMode=mode,timezone="Europe/Rome")
    data=feed(timed(":20261023T100000",":20261023T110000","RRULE:FREQ=WEEKLY;COUNT=3\r\nEXDATE:20261106T100000\r\n"))
    events=capture.calendar_events(data,source,[],{"MATH"})
    assert [e["startsAt"] for e in events]==["2026-10-23T08:00:00Z","2026-10-30T09:00:00Z"]
    assert {e["timezone"] for e in events}=={"Europe/Rome"}


def test_existing_event_becomes_reviewable_when_its_zone_becomes_unknown():
    source=subscription(importMode="all_timed")
    old=capture.calendar_events(feed(component()),source,[],{"MATH"})
    broken=feed(timed(";TZID=Unknown/Zone:20261012T100000",";TZID=Unknown/Zone:20261012T110000"))
    new=capture.calendar_events(broken,source,old,{"MATH"})
    assert new[0]["id"]==old[0]["id"]
    assert new[0]["eligibility"]=="NEEDS_REVIEW"
    assert new[0]["reasonCode"]=="INVALID_TIMEZONE"
