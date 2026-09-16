import pytest

from api.db.db_models import (
    DB,
    FileReviewAnnotation,
    FileReviewRound,
    FileReviewTemplate,
    _seed_file_review_templates,
)


@pytest.fixture(scope="module", autouse=True)
def _ensure_file_review_tables():
    """跑测前确保 3 张表已建 + 5 套预置模板已写入（等价 migrate_db 对应片段，幂等）。"""
    DB.connect(reuse_if_open=True)
    if not FileReviewTemplate.table_exists():
        FileReviewTemplate.create_table(safe=True)
        _seed_file_review_templates()
    for m in (FileReviewRound, FileReviewAnnotation):
        if not m.table_exists():
            m.create_table(safe=True)
    yield
    DB.close()


def test_preset_templates_seeded():
    with DB.connection_context():
        rows = FileReviewTemplate.select().where(FileReviewTemplate.tenant_id == '')
        ids = {r.id for r in rows}
    assert ids == {'bid_doc_format', 'bid_response_complete', 'bid_substantive_clause',
                   'bid_qualification', 'bid_price_review'}
