from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STORE_", env_file=".env", extra="ignore")

    app_name: str = "store-pos"
    debug: bool = False
    db_path: Path = Path("data/store.db")
    db_echo: bool = False
    host: str = "127.0.0.1"
    port: int = 8000

    # --- Печать чека (этап 2.1) ------------------------------------------
    # Бэкенд транспорта печати: "file" (по умолчанию — пишет чек в файл),
    # "device" (реальный ESC/POS-принтер, включается с железом), "null" (без печати).
    receipt_printer_backend: str = "file"
    receipts_dir: Path = Path("data/receipts")
    receipt_line_width: int = 48  # 80 мм, Шрифт A: 576 точек / 12 = 48 символов
    receipt_header: str = "МАГАЗИН"          # блок магазина / рекламный блок (макет 18.4)
    receipt_footer: str = "Спасибо за покупку!"

    # --- Накладная при продаже (этап 2.2) --------------------------------
    # Бэкенд транспорта: "file" (по умолчанию — пишет накладную в .txt),
    # "device" (реальное устройство, включается с железом), "null" (без вывода).
    # Устройство печати накладной ещё не выбрано — ESC/POS-поток не генерируется.
    invoice_printer_backend: str = "file"
    invoices_dir: Path = Path("data/invoices")
    invoice_line_width: int = 80             # учётный документ, шире чековой ленты
    invoice_title: str = "НАКЛАДНАЯ"         # заголовок документа (макет 18.6/18.7)

    # --- Экран покупателя (этап 2.3) -------------------------------------
    # Второе окно pywebview открывается в run.py только при customer_display_window.
    # Экран обновляется HTMX-опросом; после оплаты N секунд показывает благодарность.
    customer_display_window: bool = False        # открывать ли 2-е окно (по умолчанию нет)
    customer_display_poll_ms: int = 1000         # интервал HTMX-опроса состояния
    customer_display_thanks_seconds: int = 8     # сколько показывать «Спасибо за покупку»
    customer_display_welcome: str = "Добро пожаловать"      # экран ожидания
    customer_display_thanks_text: str = "Спасибо за покупку"  # после оплаты (макет 20.4)

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
