"""
Unit tests for the Voyage embedding batcher.

``_iter_batches`` is the one piece of the embedding path with non-obvious logic:
Voyage enforces a text-count limit AND a token limit on the same request, and
getting either wrong produces intermittent 400s that only show up on
token-dense repositories. It is a pure function over token counts, so it is
tested here without a network call or an API key.
"""

import pytest

from app.services.vector_store.voyage_client import _iter_batches


def _batches(token_counts, max_texts, max_tokens):
    return list(_iter_batches(token_counts, max_texts=max_texts, max_tokens=max_tokens))


def test_empty_input_yields_no_batches():
    assert _batches([], max_texts=10, max_tokens=100) == []


def test_single_batch_when_everything_fits():
    assert _batches([10, 20, 30], max_texts=10, max_tokens=100) == [(0, 3)]


def test_splits_on_the_text_count_limit():
    assert _batches([1] * 7, max_texts=3, max_tokens=1000) == [(0, 3), (3, 6), (6, 7)]


def test_splits_on_the_token_limit():
    # 40 + 40 = 80 fits; adding the third would reach 120 > 100.
    assert _batches([40, 40, 40, 40], max_texts=100, max_tokens=100) == [(0, 2), (2, 4)]


def test_whichever_limit_binds_first_wins():
    # Text limit (2) bites before the token limit (1000) would.
    assert _batches([10, 10, 10], max_texts=2, max_tokens=1000) == [(0, 2), (2, 3)]


def test_oversized_single_item_gets_its_own_batch():
    # A text bigger than max_tokens cannot be split — it must still be yielded
    # alone, and must not drag its neighbours into an over-limit request.
    # The API truncates it to the model's context window (truncation=True).
    assert _batches([10, 500, 10], max_texts=100, max_tokens=100) == [(0, 1), (1, 2), (2, 3)]


def test_first_item_oversized():
    assert _batches([500, 10], max_texts=100, max_tokens=100) == [(0, 1), (1, 2)]


@pytest.mark.parametrize(
    "token_counts,max_texts,max_tokens",
    [
        ([1] * 50, 7, 1000),
        ([37] * 23, 5, 90),
        ([1, 500, 2, 3, 400, 5], 3, 100),
        (list(range(1, 60)), 4, 120),
    ],
)
def test_batches_are_contiguous_and_complete(token_counts, max_texts, max_tokens):
    """Every index appears exactly once, in order, across the yielded slices."""
    batches = _batches(token_counts, max_texts, max_tokens)
    assert batches, "at least one batch expected for non-empty input"
    assert batches[0][0] == 0
    assert batches[-1][1] == len(token_counts)
    for (_, prev_end), (next_start, _) in zip(batches, batches[1:]):
        assert prev_end == next_start
    for start, end in batches:
        assert start < end, "no empty batches"


@pytest.mark.parametrize(
    "token_counts,max_texts,max_tokens",
    [
        ([1] * 50, 7, 1000),
        ([37] * 23, 5, 90),
        ([1, 2, 3, 4, 5] * 10, 8, 40),
        (list(range(1, 60)), 4, 120),
    ],
)
def test_batches_respect_both_limits(token_counts, max_texts, max_tokens):
    """No batch exceeds either limit, except a lone item that cannot be split."""
    for start, end in _batches(token_counts, max_texts, max_tokens):
        assert end - start <= max_texts
        if end - start > 1:
            assert sum(token_counts[start:end]) <= max_tokens
