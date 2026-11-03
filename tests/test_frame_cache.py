"""Tests for the on-disk frame cache.

Downloads used to live in memory only, so every restart fetched every dataset
again. Entries are keyed by a fingerprint of what the page says its content
is, so a dataset that changed is downloaded again rather than served stale.
"""

import pandas as pd
import pytest

from opensemantic.batteries.view._frame_cache import FrameCache, fingerprint_of


@pytest.fixture
def cache(tmp_path):
    return FrameCache(tmp_path / "frames")


def _frame():
    return pd.DataFrame(
        {
            "test_time": pd.Series([0.0, 1.0], dtype="pint[second]"),
            "voltage": pd.Series([3.0, 3.1], dtype="pint[volt]"),
            "note": pd.Series(["a", "b"], dtype="object"),
        }
    )


def test_a_frame_survives_a_round_trip(cache):
    cache.put("Item:D1", "fp1", _frame())

    restored = cache.get("Item:D1", "fp1")

    assert list(restored.columns) == ["test_time", "voltage", "note"]
    assert str(restored["voltage"].pint.units) == "volt"
    assert restored["voltage"].pint.magnitude.tolist() == pytest.approx([3.0, 3.1])
    assert restored["note"].tolist() == ["a", "b"]


def test_nothing_cached_is_a_miss(cache):
    assert cache.get("Item:D1", "fp1") is None


def test_a_changed_fingerprint_is_a_miss(cache):
    """A re-uploaded dataset must not be served from the old entry."""
    cache.put("Item:D1", "fp1", _frame())

    assert cache.get("Item:D1", "fp2") is None


def test_a_damaged_entry_costs_a_download_not_an_error(cache, caplog):
    cache.put("Item:D1", "fp1", _frame())
    data_path, _ = cache._paths("Item:D1")
    data_path.write_bytes(b"not,a,valid\nframe")

    assert cache.get("Item:D1", "fp1") is None


def test_an_unwritable_cache_is_not_an_error(tmp_path):
    """Caching is an optimization; losing it must not break a load."""
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")

    FrameCache(blocked / "frames").put("Item:D1", "fp1", _frame())


def test_clear_drops_the_entries(cache):
    cache.put("Item:D1", "fp1", _frame())
    cache.clear()

    assert cache.get("Item:D1", "fp1") is None


class _Dist:
    def __init__(self, url=None, size=None, media_type=None):
        self.download_url = url
        self.byte_size = size
        self.media_type = media_type
        self.file = None


class _Entity:
    def __init__(self, distributions=None, data=None):
        self.distributions = distributions
        self.data = data


def test_fingerprint_follows_the_distribution():
    one = fingerprint_of(_Entity([_Dist("http://w/File:a.csv", 100, "text/csv")]))
    same = fingerprint_of(_Entity([_Dist("http://w/File:a.csv", 100, "text/csv")]))
    bigger = fingerprint_of(_Entity([_Dist("http://w/File:a.csv", 200, "text/csv")]))

    assert one == same
    assert one != bigger


def test_an_inline_dataset_is_fingerprinted_by_its_rows():
    assert fingerprint_of(_Entity(data=[1, 2])) != fingerprint_of(
        _Entity(data=[1, 2, 3])
    )


def test_the_same_title_on_two_wikis_does_not_collide(cache):
    """The cache is shared, and a page title means different data per wiki."""
    cache.put("wiki-a|Item:D1", "fp", _frame())

    assert cache.get("wiki-b|Item:D1", "fp") is None
