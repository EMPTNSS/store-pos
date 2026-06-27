from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db
from app.routes.products import router as products_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    application = FastAPI(lifespan=lifespan)
    application.include_router(products_router)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    return application


app = create_app()
