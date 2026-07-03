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

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
