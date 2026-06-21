import pytest
from sqlalchemy import event
from sqlmodel import Session, create_engine


@pytest.fixture
def test_engine(tmp_path, monkeypatch):
    import app.database as db_module
    from app.config import settings

    db_file = tmp_path / "test.db"
    monkeypatch.setattr(settings, "db_path", db_file)

    eng = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    monkeypatch.setattr(db_module, "engine", eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(test_engine):
    with Session(test_engine) as s:
        yield s


@pytest.fixture
def client(test_engine):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
