"""Reciprocal rank fusion of the two indexes' ranked lists."""

from uuid import uuid4

import pytest

from services.query_engine.retrieval import RRF_K, reciprocal_rank_fusion

A, B, C, D = (uuid4() for _ in range(4))


def order(*lists):
    return [item for item, _ in reciprocal_rank_fusion(lists)]


def test_first_in_both_lists_wins():
    assert order([A, B, C], [A, C, B])[0] == A


def test_found_by_both_beats_found_by_one():
    # B is second in both lists; A is first in one list only.
    assert order([A, B], [C, B])[:1] == [B]


def test_an_item_in_one_list_only_still_appears():
    assert set(order([A, B], [C])) == {A, B, C}


def test_scores_are_summed_reciprocal_ranks():
    scores = dict(reciprocal_rank_fusion([[A, B], [B]]))
    assert scores[A] == pytest.approx(1 / (RRF_K + 1))
    assert scores[B] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))


def test_ties_keep_a_stable_order():
    # A and C are both first in one list: the earlier list wins the tie.
    assert order([A, B], [C, D]) == [A, C, B, D]
    assert order([C, D], [A, B]) == [C, A, D, B]


def test_empty_lists_give_nothing():
    assert reciprocal_rank_fusion([[], []]) == []
