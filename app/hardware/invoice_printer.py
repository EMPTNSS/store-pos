"""Транспорт печати накладной (этап 2.2). Слой «КУДА печатать».

Отделён от формирования (``app/services/invoice_render.py``): получает готовый текст и
номер чека, ничего не знает о составе накладной. Подключение реального устройства сводится
к смене бэкенда в ``config.py`` — логика формирования при этом не трогается (CLAUDE.md).

В отличие от чека (2.1), ESC/POS-поток не генерируется: устройство печати накладной ещё не
выбрано (возможен обычный A4-принтер, а не термо-ESC/POS). Пока пишем только текстовый файл.
"""

import logging
from pathlib import Path
from typing import Optional, Protocol

from app.config import settings

log = logging.getLogger(__name__)


class InvoicePrinter(Protocol):
    """Транспорт печати накладной."""

    def print(self, receipt_number: int, text: str) -> None: ...


class NullInvoicePrinter:
    """Вывод отключён — ничего не делает (бэкенд ``null``)."""

    def print(self, receipt_number: int, text: str) -> None:
        return None


class FileInvoicePrinter:
    """Пишет накладную в человекочитаемый ``.txt``.

    Пока нет устройства, это заменяет печать и остаётся инспектируемым/тестируемым.
    ESC/POS не генерируем — тип устройства накладной не выбран.
    """

    def __init__(self, invoices_dir: Optional[Path] = None) -> None:
        self._dir = invoices_dir or settings.invoices_dir

    def print(self, receipt_number: int, text: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"накладная-{receipt_number:04d}.txt"
        path.write_text(text, encoding="utf-8")


class DeviceInvoicePrinter:
    """Реальное устройство печати накладной. Точка расширения — включается с железом."""

    def print(self, receipt_number: int, text: str) -> None:
        raise NotImplementedError(
            "Печать накладной на устройство ещё не настроена: подключите принтер и "
            "задайте параметры в config.py (бэкенд 'device' появится вместе с железом)."
        )


def get_invoice_printer() -> InvoicePrinter:
    """Выбрать транспорт печати накладной по ``settings.invoice_printer_backend``."""
    backend = settings.invoice_printer_backend
    if backend == "file":
        return FileInvoicePrinter()
    if backend == "device":
        return DeviceInvoicePrinter()
    if backend == "null":
        return NullInvoicePrinter()
    raise ValueError(f"Неизвестный бэкенд печати накладной: {backend!r}")
