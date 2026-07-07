from collections.abc import Generator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=settings.db_echo,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def init_db() -> None:
    import app.models  # noqa: F401 — registers all table classes with SQLModel.metadata
    from app.models.counter import ProductCodeCounter
    from app.models.receipt import ReceiptNumberCounter
    from app.models.return_receipt import ReturnNumberCounter

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        if session.get(ProductCodeCounter, 1) is None:
            session.add(ProductCodeCounter(id=1, last_value=0))
        if session.get(ReceiptNumberCounter, 1) is None:
            session.add(ReceiptNumberCounter(id=1, last_value=0))
        if session.get(ReturnNumberCounter, 1) is None:
            session.add(ReturnNumberCounter(id=1, last_value=0))
        session.commit()
