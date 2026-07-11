from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routes.cashier import router as cashier_router
from app.routes.customer import router as customer_router
from app.routes.orders import router as orders_router
from app.routes.products import router as products_router
from app.routes.receiving import router as receiving_router
from app.routes.reports import router as reports_router
from app.routes.returns import router as returns_router
from app.routes.shell import router as shell_router
from app.routes.work_day import router as work_day_router

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    application = FastAPI(lifespan=lifespan)
    application.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    application.include_router(shell_router)
    application.include_router(products_router)
    application.include_router(receiving_router)
    application.include_router(orders_router)
    application.include_router(cashier_router)
    application.include_router(returns_router)
    application.include_router(customer_router)
    application.include_router(work_day_router)
    application.include_router(reports_router)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    return application


app = create_app()
