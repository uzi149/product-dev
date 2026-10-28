"""One embedding setup per installation (ADR 0020): recorded on first use, a mismatch refused."""

import pytest
from sqlalchemy import select

from shared.database.index_settings import IndexSettingsMismatch, ensure_index_settings
from shared.database.models import IndexSetting

TEXT = "BAAI/bge-base-en-v1.5"
IMAGE = "google/siglip2-base-patch16-224"


def recorded(session) -> dict[str, tuple[str, int]]:
    return {
        row.index_name: (row.model_name, row.dimensions)
        for row in session.scalars(select(IndexSetting))
    }


def test_the_first_use_records_both_models(session):
    ensure_index_settings(session, TEXT, IMAGE)
    session.commit()
    assert recorded(session) == {"text": (TEXT, 768), "visual": (IMAGE, 768)}


def test_the_same_models_are_accepted_again(session):
    ensure_index_settings(session, TEXT, IMAGE)
    session.commit()
    ensure_index_settings(session, TEXT, IMAGE)
    assert recorded(session) == {"text": (TEXT, 768), "visual": (IMAGE, 768)}


def test_another_model_is_refused_and_nothing_changes(session):
    ensure_index_settings(session, TEXT, IMAGE)
    session.commit()
    with pytest.raises(IndexSettingsMismatch) as refused:
        ensure_index_settings(session, TEXT, "google/siglip-base-patch16-224")
    message = str(refused.value)
    assert IMAGE in message and "google/siglip-base-patch16-224" in message
    assert "visual index" in message and "text index" not in message
    session.rollback()
    assert recorded(session) == {"text": (TEXT, 768), "visual": (IMAGE, 768)}


def test_another_vector_size_is_refused(session):
    ensure_index_settings(session, TEXT, IMAGE)
    session.commit()
    with pytest.raises(IndexSettingsMismatch, match="1024"):
        ensure_index_settings(session, TEXT, IMAGE, dimensions=1024)
