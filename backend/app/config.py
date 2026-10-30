from functools import lru_cache
from typing import Literal
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Журнал продаж"
    base_path: str = "/vr/sales"
    database_url: str = "postgresql+asyncpg://sales:sales@db:5432/sales"
    cors_origins: str = "https://kvasmix.ru"
    upload_dir: Path = Path("/data/imports")
    import_temp_dir: Path = Path("/tmp/sales-journal-imports")
    log_level: str = "INFO"
    churn_monitor_status_file: Path = Path("/run/sales-churn-monitor/status.json")
    analytics_automation_enabled: bool = False
    analytics_automation_manifest_key: str = ""
    analytics_signals_enabled: bool = False
    analytics_signals_mode: Literal["SHADOW"] = "SHADOW"
    analytics_signals_freshness_hours: int = Field(36, ge=1, le=168)
    autoload_timezone: str = "Europe/Moscow"
    clients_vr_api_url: str = "https://kvasmix.ru/vr/clients/api"
    clients_vr_api_token: str = ""
    calltrack_integration_token: str = ""
    vrcatalog_api_url: str = "https://kvasmix.ru/vr/catalog/api"
    vrcatalog_api_token: str = ""
    product_analytics_local_catalog_enabled: bool = True
    catalog_sync_batch_size: int = Field(100, ge=1, le=250)
    catalog_sync_pause_seconds: float = Field(1.0, ge=0.1, le=3600)
    catalog_sync_poll_seconds: float = Field(1.0, ge=0.1, le=10)
    catalog_sync_retry_limit: int = Field(3, ge=0, le=10)
    catalog_sync_retry_seconds: float = Field(30.0, ge=1, le=3600)
    catalog_sync_min_available_mb: int = Field(256, ge=256)
    catalog_sync_db_timeout_seconds: float = Field(10, ge=1, le=60)
    catalog_sync_db_lock_timeout_seconds: float = Field(2, ge=0.1, le=10)
    catalog_lookup_timeout_seconds: float = Field(15, ge=1, le=60)
    catalog_lookup_max_attempts: int = Field(3, ge=1, le=3)
    public_url: str = "https://kvasmix.ru/vr/sales"
    settings_admin_password_hash: str = ""
    settings_admin_session_secret: str = ""
    settings_admin_cookie_secure: bool = True
    mtls_helper_socket: Path = Path("/run/sales-mtls-helper/helper.sock")
    mtls_helper_secret: str = ""
    mtls_proxy_secret: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    @property
    def api_prefix(self) -> str: return f"{self.base_path.rstrip('/')}/api"
    @property
    def origins(self) -> list[str]: return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

@lru_cache
def get_settings() -> Settings: return Settings()
settings = get_settings()
