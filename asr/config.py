"""ASR service configuration."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASR_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8100
    device: str = "cuda:0"

    # FunASR Paraformer 68M（单段 ≤20s）；非 paraformer-zh(220M large)
    asr_model: str = "iic/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1"
    vad_model: str = "fsmn-vad"
    punc_model: str = "ct-punc"

    # Audio limits (ms / seconds) — 模型硬限 20s
    min_duration_ms: int = 300
    max_duration_s: int = 20
    suggest_max_duration_s: int = 15

    # Concurrency
    max_workers: int = 2
    queue_timeout_s: float = 3.0
    inference_timeout_s: float = 5.0
    request_timeout_s: float = 8.0

    # Paths
    root_dir: Path = Path(__file__).resolve().parent
    model_dir: Path = root_dir / "models"
    data_dir: Path = root_dir / "data"
    temp_dir: Path = data_dir / "temp_audio"


settings = Settings()
settings.model_dir.mkdir(parents=True, exist_ok=True)
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.temp_dir.mkdir(parents=True, exist_ok=True)
