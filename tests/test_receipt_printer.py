"""Транспорт печати чека (этап 2.1): файловый бэкенд и фабрика."""

import pytest

from app.hardware.receipt_printer import (
    DeviceReceiptPrinter,
    FileReceiptPrinter,
    NullReceiptPrinter,
    get_receipt_printer,
)

SAMPLE = "МАГАЗИН\nЧек №0007\nИТОГО 170.00\n"


def test_writes_text_file(tmp_path):
    printer = FileReceiptPrinter(receipts_dir=tmp_path)
    printer.print(7, SAMPLE)

    txt = tmp_path / "чек-0007.txt"
    assert txt.exists()
    assert txt.read_text(encoding="utf-8") == SAMPLE


def test_writes_escpos_stream_with_cut(tmp_path):
    printer = FileReceiptPrinter(receipts_dir=tmp_path)
    printer.print(7, SAMPLE)

    escpos = tmp_path / "чек-0007.escpos"
    assert escpos.exists()
    data = escpos.read_bytes()
    assert data, "ESC/POS-поток пуст"
    assert b"\x1dV" in data  # команда реза GS V в конце


def test_dir_created_if_missing(tmp_path):
    target = tmp_path / "nested" / "receipts"
    assert not target.exists()
    FileReceiptPrinter(receipts_dir=target).print(1, SAMPLE)
    assert (target / "чек-0001.txt").exists()


def test_null_backend_writes_nothing(tmp_path):
    NullReceiptPrinter().print(1, SAMPLE)
    assert list(tmp_path.iterdir()) == []


def test_device_backend_not_configured():
    with pytest.raises(NotImplementedError):
        DeviceReceiptPrinter().print(1, SAMPLE)


def test_factory_selects_backend(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "receipt_printer_backend", "file")
    assert isinstance(get_receipt_printer(), FileReceiptPrinter)

    monkeypatch.setattr(settings, "receipt_printer_backend", "null")
    assert isinstance(get_receipt_printer(), NullReceiptPrinter)

    monkeypatch.setattr(settings, "receipt_printer_backend", "device")
    assert isinstance(get_receipt_printer(), DeviceReceiptPrinter)

    monkeypatch.setattr(settings, "receipt_printer_backend", "bogus")
    with pytest.raises(ValueError):
        get_receipt_printer()
