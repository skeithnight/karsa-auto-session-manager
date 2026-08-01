"""Unit tests for spoofing rejection in EntryFilter."""

import pytest
from app.alpha.entry_filter import EntryFilter


def test_entry_filter_rejects_bid_spoofing_for_long():
    ef = EntryFilter()
    passed, reason = ef.check(
        direction="LONG",
        is_spoofing_bid=True,
        is_spoofing_ask=False,
    )
    assert passed is False
    assert "spoofing detected on bid side" in reason


def test_entry_filter_rejects_ask_spoofing_for_short():
    ef = EntryFilter()
    passed, reason = ef.check(
        direction="SHORT",
        is_spoofing_bid=False,
        is_spoofing_ask=True,
    )
    assert passed is False
    assert "spoofing detected on ask side" in reason


def test_entry_filter_passes_when_no_spoofing():
    ef = EntryFilter()
    passed, reason = ef.check(
        direction="LONG",
        is_spoofing_bid=False,
        is_spoofing_ask=False,
    )
    assert passed is True
    assert reason == "passed"
