"""Business API settings."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000

    dify_base_url: str = "http://117.50.223.142:7000"
    dify_api_key: str = ""
    dify_user_prefix: str = "student"
    # filled from GET /v1/info at runtime; override optional
    dify_agent_name: str = ""

    asr_url: str = "http://127.0.0.1:8100"
    # legacy single URL (fallback if tts_ws_urls empty)
    tts_ws_url: str = "ws://127.0.0.1:8200/internal/tts/stream"
    # Non-stream batch TTS: 4 workers (2/GPU), ports 8200-8203
    tts_ws_urls: str = (
        "ws://127.0.0.1:8200/internal/tts/stream,"
        "ws://127.0.0.1:8201/internal/tts/stream,"
        "ws://127.0.0.1:8202/internal/tts/stream,"
        "ws://127.0.0.1:8203/internal/tts/stream"
    )
    # derived HTTP base list optional; empty → convert from tts_ws_urls
    tts_http_urls: str = (
        "http://127.0.0.1:8200,"
        "http://127.0.0.1:8201,"
        "http://127.0.0.1:8202,"
        "http://127.0.0.1:8203"
    )
    # ponytail: = worker count; upgrade = more start_workers / GPU
    max_tts_active_jobs: int = 4

    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    avatar_root: Path = ROOT / "data" / "avatars"
    default_avatar_id: str = "avatar_001"
    default_avatar_version_id: str = "avv_001"

    media_idle_timeout_s: int = 90
    tts_sample_rate: int = 24000
    default_voice_id: str = "avatar_voice_001"


settings = Settings()
settings.avatar_root.mkdir(parents=True, exist_ok=True)


def tts_worker_urls() -> list[str]:
    urls = [u.strip() for u in (settings.tts_ws_urls or "").split(",") if u.strip()]
    if not urls and settings.tts_ws_url:
        urls = [settings.tts_ws_url.strip()]
    return urls


def tts_http_bases() -> list[str]:
    urls = [u.strip().rstrip("/") for u in (settings.tts_http_urls or "").split(",") if u.strip()]
    if urls:
        return urls
    # derive from ws urls: ws://host:port/... → http://host:port
    out: list[str] = []
    for w in tts_worker_urls():
        base = w.replace("ws://", "http://").replace("wss://", "https://")
        if "/internal/" in base:
            base = base.split("/internal/")[0]
        out.append(base.rstrip("/"))
    return out
