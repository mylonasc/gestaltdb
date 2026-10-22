"""RE-194 decoded native Evidence path: pushdown, visibility, exact version IDs."""

import pytest

from gestaltdb.reasoning.adapters.native import (
    DecodedBatch,
    DecodedRow,
    RawBatch,
    iter_decoded_evidence,
    iter_locator_keys,
)
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError


def batch(rows, index=0, complete=True):
    return RawBatch(tuple(rows), index, complete)


def decode(row_key, row_value):
    subject, _, predicate = row_key.decode().partition("|")
    return DecodedRow(subject, predicate or "p", "o", "v-" + subject, "e-" + subject)


def scan_of(*batches):
    return list(batches)


def test_decode_with_pushdown_and_visibility():
    scan = scan_of(batch([(b"a|knows", b"1"), (b"b|likes", b"2")], 0, False),
                   batch([(b"c|knows", b"3")], 1, True))
    out = list(iter_decoded_evidence(scan, decode, predicate="knows",
                                     bindings={"subject": "a"}, visibility="snap-1"))
    assert len(out) == 1 and isinstance(out[0], DecodedBatch)
    assert [row.subject for row in out[0].rows] == ["a"]
    assert out[0].visibility == "snap-1"
    # complete mirrors the raw batch: the raw-complete tail filtered out,
    # so end-of-scan is generator exhaustion, not the flag.
    assert out[0].complete is False
    assert out[0].rows[0].version_id == "v-a"
    unfiltered = list(iter_decoded_evidence(scan, decode, visibility="snap-1"))
    assert [row.subject for batch in unfiltered for row in batch.rows] == ["a", "b", "c"]
    assert sum(1 for _ in unfiltered) == 2
    assert unfiltered[-1].complete is True  # raw-complete batch survived filtering
    assert [row.subject for row in list(iter_decoded_evidence(
        scan_of(batch([(b"a|knows", b"1")], 0, True)), decode,
        bindings={"object": "o"}, visibility="v"))[0].rows] == ["a"]


def test_raw_rows_never_leak_and_versions_stay_exact():
    dup = scan_of(batch([(b"a|p", b"1"), (b"a|p", b"2")], 0, True))
    with pytest.raises(ReasoningError, match="duplicate version id"):
        list(iter_decoded_evidence(dup, decode, visibility="v"))
    scan = scan_of(batch([(b"a|p", b"1"), (b"b|p", b"2")], 0, True))

    def corrupt(key, value):
        raise ValueError("bad payload")

    with pytest.raises(ReasoningError, match="native row decoding failed") as exc:
        list(iter_decoded_evidence(scan_of(batch([(b"k", b"v")], 0, True)), corrupt, visibility="v"))
    assert b"k" not in str(exc.value).encode() and "k" not in str(exc.value).replace("duplicate", "")

    def wrong_type(key, value):
        return ("not", "a", "row")

    with pytest.raises(ReasoningError, match="must return DecodedRow"):
        list(iter_decoded_evidence(scan_of(batch([(b"k", b"v")], 0, True)), wrong_type, visibility="v"))
    with pytest.raises(ReasoningError):
        DecodedRow("", "p", "o", "v", "e")
    with pytest.raises(ReasoningError):
        DecodedBatch((), 0, True, "v")
    with pytest.raises(ReasoningError, match="visibility"):
        list(iter_decoded_evidence(scan, decode, visibility=""))
    with pytest.raises(ReasoningError, match="bindings"):
        list(iter_decoded_evidence(scan, decode, bindings={"nope": "x"}, visibility="v"))
    with pytest.raises(ReasoningError, match="bindings"):
        list(iter_decoded_evidence(scan, decode, bindings="nope", visibility="v"))
    with pytest.raises(ReasoningError):
        list(iter_decoded_evidence(scan, "nope", visibility="v"))
    with pytest.raises(ReasoningError):
        list(iter_decoded_evidence(["nope"], decode, visibility="v"))
    with pytest.raises(ReasoningError, match="predicate"):
        list(iter_decoded_evidence(scan, decode, predicate="", visibility="v"))
    with pytest.raises(ReasoningLimitError, match="max_rows"):
        list(iter_decoded_evidence(scan, decode, visibility="v", max_rows=1))
    with pytest.raises(ReasoningError):
        list(iter_decoded_evidence(scan, decode, visibility="v", max_rows=True))


def test_locator_keys_skip_values_for_point_materialization():
    scan = scan_of(batch([(b"k1", b"v1"), (b"k2", b"v2")], 0, False),
                   batch([(b"k3", b"v3")], 1, True))
    assert list(iter_locator_keys(scan)) == [b"k1", b"k2", b"k3"]
    with pytest.raises(ReasoningLimitError, match="max_rows"):
        list(iter_locator_keys(scan, max_rows=2))
    with pytest.raises(ReasoningError):
        list(iter_locator_keys(["nope"]))
    assert list(iter_locator_keys([])) == []
