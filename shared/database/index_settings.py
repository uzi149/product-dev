"""One embedding setup per installation (ADR 0020).

Vectors from different models are in different spaces even when they have the same size, so an
index must only ever hold vectors from one model. The first process that embeds or searches
records its models in `index_settings`; every later one must be configured for the same models,
or it is refused before it writes anything.
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from shared.database.models import EMBEDDING_DIM, IndexName, IndexSetting


class IndexSettingsMismatch(Exception):
    """This process is configured for other embedding models than the indexes were built with."""


def ensure_index_settings(
    session: Session, text_model: str, image_model: str, dimensions: int = EMBEDDING_DIM
) -> None:
    """Record the models on first use; raise IndexSettingsMismatch when they differ.

    The caller commits (session_scope does), so two processes starting at once both see the
    first one's row.
    """
    wanted = {IndexName.TEXT: text_model, IndexName.VISUAL: image_model}
    for index, model in wanted.items():
        session.execute(
            insert(IndexSetting)
            .values(index_name=index.value, model_name=model, dimensions=dimensions)
            .on_conflict_do_nothing(index_elements=["index_name"])
        )
    recorded = {row.index_name: row for row in session.scalars(select(IndexSetting))}
    problems = [
        f"the {index} index was built with {recorded[index].model_name} "
        f"({recorded[index].dimensions} values), this process is configured for {model} "
        f"({dimensions})"
        for index, model in wanted.items()
        if (recorded[index].model_name, recorded[index].dimensions) != (model, dimensions)
    ]
    if problems:
        raise IndexSettingsMismatch(
            "; ".join(problems) + ". Set TEXT_EMBEDDING_MODEL / IMAGE_EMBEDDING_MODEL back, "
            "or re-analyse the footage with the new models."
        )
