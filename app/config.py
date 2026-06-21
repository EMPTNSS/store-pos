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

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
