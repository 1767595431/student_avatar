from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
REPO = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TTS_",
        env_file=str(REPO / ".env"),
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8300
    device: str = "cpu"
    worker_id: str = "qwen-adapter-0"
    backend: str = "vllm"

    # 空：启动时从 vLLM /v1/models 发现（本地 snapshot 时 id 是路径）
    model_id: str = ""
    language: str = "Chinese"
    task_type: str = "Base"

    vllm_urls: str = "http://127.0.0.1:8091,http://127.0.0.1:8092"

    voice_root: Path = REPO / "data" / "voices"
    default_voice_id: str = "voice_aacd9fa8"
    output_sample_rate: int = 24000


settings = Settings()
