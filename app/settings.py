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
    concurrency: int = Field(default=4, alias="CONCURRENCY")
    # None by default (issue #71): a fixed page-count guess was cutting
    # real crawls short before the frontier actually emptied. Set either
    # as an explicit opt-in ceiling if desired -- see Orchestrator.
    max_pages: int | None = Field(default=None, alias="MAX_PAGES")
    max_duration_seconds: float | None = Field(default=None, alias="MAX_DURATION_SECONDS")
