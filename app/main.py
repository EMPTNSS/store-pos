from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    application = FastAPI(lifespan=lifespan)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    return application


app = create_app()
