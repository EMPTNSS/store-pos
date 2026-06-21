from sqlalchemy import text


def test_init_db_creates_db_file(test_engine, tmp_path):
    from app.database import init_db
    init_db()
    assert (tmp_path / "test.db").exists()


def test_select_one(session):
    result = session.execute(text("SELECT 1")).scalar()
    assert result == 1


def test_foreign_keys_enabled(session):
    fk = session.execute(text("PRAGMA foreign_keys")).scalar()
    assert fk == 1
