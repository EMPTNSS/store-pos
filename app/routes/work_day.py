"""Открытие/закрытие рабочего дня (смены).

Тонкий роутер: дёрнуть сервис → вернуть фрагмент статуса смены для строки управления в
оболочке. Закрытие смены (макет 23.1) формирует документ завершения дня (23.2–23.5) одним
согласованным действием — через ``day_report_service.close_day_and_report`` (этап 7.1).
Инвариант «одна открытая смена» держит сервис.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.database import get_session
from app.services.day_report_service import close_day_and_report
from app.services.work_day_service import open_day

router = APIRouter()


@router.post("/work-day/open")
async def open_work_day(session: Session = Depends(get_session)):
    """Начать рабочий день, затем перезагрузить раму (POST-redirect-GET).

    Полная перезагрузка даёт единый источник правды: рама перерисовывается с реальным
    состоянием дня из БД — баннер начала дня исчезает, кнопка «Завершить день» становится
    активной. Нативная форма работает и без htmx (офлайн-касса). «Уже открыт» из UI
    недостижим (при открытом дне баннер не показан), поэтому ошибку молча игнорируем.
    """
    try:
        open_day(session)
    except ValueError:
        pass
    return RedirectResponse("/", status_code=303)


@router.post("/work-day/close")
async def close_work_day(session: Session = Depends(get_session)):
    """Завершить рабочий день (макет 23.1) и сформировать документ дня (23.2–23.5).

    Закрытие смены и запись документа — одна транзакция (``close_day_and_report``, 7.1).
    Затем перезагрузка рамы с ?report=N, чтобы вернуть баннер начала дня и показать ссылку
    на только что сформированный документ.
    """
    try:
        report = close_day_and_report(session)
    except ValueError:
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/?report={report.report_number}", status_code=303)
