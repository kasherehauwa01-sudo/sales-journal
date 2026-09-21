from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Журнал продаж"
    base_path: str = "/vr/sales"
    database_url: str = "postgresql+asyncpg://sales:sales@db:5432/sales"
    cors_origins: str = "https://kvasmix.ru"
    upload_dir: Path = Path("/data/imports")
    log_level: str = "INFO"
    autoload_timezone: str = "Europe/Moscow"
    clients_vr_api_url: str = "https://kvasmix.ru/vr/clients/api"
    clients_vr_api_token: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    @property
    def api_prefix(self) -> str: return f"{self.base_path.rstrip('/')}/api"
    @property
    def origins(self) -> list[str]: return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

@lru_cache
def get_settings() -> Settings: return Settings()
settings = get_settings()
