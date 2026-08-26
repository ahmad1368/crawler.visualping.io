"""Application settings loaded from environment variables / `.env`.

Never log or persist a `Settings` instance directly -- it holds Basic Auth
credentials for the target site.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    target_url: str = Field(alias="TARGET_URL")
    auth_username: str = Field(alias="AUTH_USERNAME")
    auth_password: str = Field(alias="AUTH_PASSWORD")
    context_chars: int = Field(default=80, alias="CONTEXT_CHARS")
    concurrency: int = Field(default=4, alias="CONCURRENCY")
    max_pages: int = Field(default=100, alias="MAX_PAGES")
