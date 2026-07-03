"""Транспорт печати чека (этап 2.1). Слой «КУДА печатать».

Отделён от формирования (``app/services/receipt_render.py``): получает готовый текст и
номер чека, ничего не знает о его составе. Подключение реального принтера сводится к смене
бэкенда в ``config.py`` — логика формирования при этом не трогается (CLAUDE.md, «Железо»).
"""

import logging
from pathlib import Path
from typing import Optional, Protocol

from app.config import settings

log = logging.getLogger(__name__)


class ReceiptPrinter(Protocol):
    """Транспорт печати чека."""

    def print(self, receipt_number: int, text: str) -> None: ...


class NullReceiptPrinter:
    """Печать отключена — ничего не делает (бэкенд ``null``)."""

    def print(self, receipt_number: int, text: str) -> None:
        return None


class FileReceiptPrinter:
    """Пишет чек в файл: человекочитаемый ``.txt`` + реальный ESC/POS-поток ``.escpos``.

    Пока нет принтера, это заменяет устройство и остаётся инспектируемым/тестируемым.
    """

    def __init__(self, receipts_dir: Optional[Path] = None) -> None:
        self._dir = receipts_dir or settings.receipts_dir

    def print(self, receipt_number: int, text: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        stem = f"чек-{receipt_number:04d}"

        # Человекочитаемый чек — пишем всегда, даже если ESC/POS-кодировка не удалась.
        (self._dir / f"{stem}.txt").write_text(text, encoding="utf-8")

        escpos_bytes = self._to_escpos(text)
        if escpos_bytes is not None:
            (self._dir / f"{stem}.escpos").write_bytes(escpos_bytes)

    @staticmethod
    def _to_escpos(text: str) -> Optional[bytes]:
        """Прогнать текст через python-escpos (Dummy) → реальные ESC/POS-байты."""
        try:
            from escpos.printer import Dummy

            device = Dummy()
            device.text(text + "\n")
            device.cut()
            return device.output
        except Exception:  # noqa: BLE001 — кодировка/окружение; .txt уже сохранён
            log.exception("Не удалось сформировать ESC/POS-поток чека")
            return None


class DeviceReceiptPrinter:
    """Реальный ESC/POS-принтер (USB/Serial/Lan). Точка расширения — включается с железом."""

    def print(self, receipt_number: int, text: str) -> None:
        raise NotImplementedError(
            "Печать на устройство ещё не настроена: подключите принтер и задайте "
            "параметры в config.py (бэкенд 'device' появится вместе с железом)."
        )


def get_receipt_printer() -> ReceiptPrinter:
    """Выбрать транспорт печати по ``settings.receipt_printer_backend``."""
    backend = settings.receipt_printer_backend
    if backend == "file":
        return FileReceiptPrinter()
    if backend == "device":
        return DeviceReceiptPrinter()
    if backend == "null":
        return NullReceiptPrinter()
    raise ValueError(f"Неизвестный бэкенд печати: {backend!r}")
