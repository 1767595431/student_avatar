"""Publisher-local settings (imported when running publisher as package)."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class PubSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: str = "APIstudentboPs5W9J"
    livekit_api_secret: str = "fcJea_2jFHU5lTix_arQABRREMmyOeX3a2zrSN4RuLs"
    avatar_root: Path = ROOT / "data" / "avatars"
    tts_sample_rate: int = 24000


settings = PubSettings()
