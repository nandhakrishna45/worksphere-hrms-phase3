from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "WorkSphere HRMS"
    database_url: str = "sqlite:///./worksphere.db"
    jwt_secret_key: str = "unsafe-development-secret-change-me"
    jwt_expire_minutes: int = 60
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    upload_dir: str = "uploads"
    max_upload_mb: int = 10
    allowed_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
