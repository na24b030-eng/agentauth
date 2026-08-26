from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TRUSTCART_", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+psycopg://trustcart:trustcart@localhost:5432/trustcart"
    frontend_origin: str = "http://localhost:3000"
    merchant_api_url: str = "http://localhost:8000"
    agent_api_url: str = "http://localhost:8001"
    model_name: str = "gemini-3.5-flash-lite"
    model_thinking_level: str = "low"
    gemini_api_key: SecretStr | None = None
    agent_private_key_pem: SecretStr | None = None
    demo_auth_private_key_pem: SecretStr | None = None
    demo_auth_public_key_pem: str | None = None
    razorpay_key_id: SecretStr | None = None
    razorpay_key_secret: SecretStr | None = None
    razorpay_webhook_secret: SecretStr | None = None
    razorpay_previous_webhook_secret: SecretStr | None = None
    demo_passcode: SecretStr = Field(default=SecretStr("trustcart-demo"))
    pop_clock_skew_seconds: int = 300
    nonce_ttl_seconds: int = 600
    quote_ttl_seconds: int = 120
    cancel_window_seconds: int = 3
    payment_deadline_seconds: int = 600
    late_capture_grace_seconds: int = 300
    fault_drop_order_response: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
