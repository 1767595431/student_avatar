"""TTS service configuration."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TTS_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8200
    device: str = "cuda:1"  # default GPU1 for TTS; GPU0 shared with ASR when needed
    sample_rate: int = 24000

    # CosyVoice paths
    root_dir: Path = Path(__file__).resolve().parent
    cosyvoice_dir: Path = root_dir / "CosyVoice"
    model_dir: Path = root_dir / "models" / "Fun-CosyVoice3-0.5B"
    prompt_wav: Path = root_dir / "voices" / "default_prompt.wav"
    prompt_text: str = "希望你以后能够做的比我还好呦。"
    instruct_prefix: str = "You are a helpful assistant.<|endofprompt|>"

    # Default voice registry key
    default_voice_id: str = "avatar_voice_001"


settings = Settings()
settings.model_dir.parent.mkdir(parents=True, exist_ok=True)
(settings.root_dir / "data").mkdir(parents=True, exist_ok=True)
(settings.root_dir / "voices").mkdir(parents=True, exist_ok=True)
