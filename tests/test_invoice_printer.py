"""Транспорт печати накладной (этап 2.2): файловый бэкенд и фабрика."""

import pytest

from app.hardware.invoice_printer import (
    DeviceInvoicePrinter,
    FileInvoicePrinter,
    NullInvoicePrinter,
    get_invoice_printer,
)

SAMPLE = "НАКЛАДНАЯ к чеку №0007\nИТОГО 170.00\n"


def test_writes_text_file(tmp_path):
    printer = FileInvoicePrinter(invoices_dir=tmp_path)
    printer.print(7, SAMPLE)

    txt = tmp_path / "накладная-0007.txt"
    assert txt.exists()
    assert txt.read_text(encoding="utf-8") == SAMPLE


def test_no_escpos_stream(tmp_path):
    """В отличие от чека, ESC/POS-поток для накладной не создаётся."""
    FileInvoicePrinter(invoices_dir=tmp_path).print(7, SAMPLE)
    assert not (tmp_path / "накладная-0007.escpos").exists()
    assert list(tmp_path.glob("*.escpos")) == []


def test_dir_created_if_missing(tmp_path):
    target = tmp_path / "nested" / "invoices"
    assert not target.exists()
    FileInvoicePrinter(invoices_dir=target).print(1, SAMPLE)
    assert (target / "накладная-0001.txt").exists()


def test_null_backend_writes_nothing(tmp_path):
    NullInvoicePrinter().print(1, SAMPLE)
    assert list(tmp_path.iterdir()) == []


def test_device_backend_not_configured():
    with pytest.raises(NotImplementedError):
        DeviceInvoicePrinter().print(1, SAMPLE)


def test_factory_selects_backend(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "invoice_printer_backend", "file")
    assert isinstance(get_invoice_printer(), FileInvoicePrinter)

    monkeypatch.setattr(settings, "invoice_printer_backend", "null")
    assert isinstance(get_invoice_printer(), NullInvoicePrinter)

    monkeypatch.setattr(settings, "invoice_printer_backend", "device")
    assert isinstance(get_invoice_printer(), DeviceInvoicePrinter)

    monkeypatch.setattr(settings, "invoice_printer_backend", "bogus")
    with pytest.raises(ValueError):
        get_invoice_printer()
