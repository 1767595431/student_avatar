"""Business API settings — .env 只放部署密钥与可选覆盖，其余用代码默认。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

# vLLM-Omni 双适配层：8300→GPU0(:8091)，8301→GPU1(:8092)
_DEFAULT_TTS_HTTP = "http://127.0.0.1:8300,http://127.0.0.1:8301"
# 每卡一个 ASR：8100→GPU0，8101→GPU1（各 max_workers=2 → 合计 4）
_DEFAULT_ASR_HTTP = "http://127.0.0.1:8100,http://127.0.0.1:8101"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000

    # 管理端添加智能体时 Base URL 的缺省（各智能体仍可单独填）
    dify_base_url: str = ""
    dify_user_prefix: str = "student"

    # 多实例逗号分隔；兼容旧字段 asr_url（单地址）
    asr_http_urls: str = _DEFAULT_ASR_HTTP
    asr_url: str = ""
    tts_http_urls: str = _DEFAULT_TTS_HTTP
    # 会话硬顶（总控 / 创建拦截）
    max_sessions: int = 30
    # 总控展示：2 服务 × max_workers=2（API 不二次硬拒）
    max_asr_jobs: int = 4
    # TTS 业务同时合成槽：生产首版 4+4=8（引擎每卡 Stage1 上限 10）
    max_tts_active_jobs: int = 8
    # 有首句（P0）在排队时，为 P0 预留的槽位数
    tts_p0_reserved_slots: int = 2
    tts_sample_rate: int = 24000

    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    avatar_root: Path = ROOT / "data" / "avatars"
    # Publisher 暖待机无媒体活动后回收（会话对象仍在，可再 ensure）
    media_idle_timeout_s: int = 90
    # 客户端心跳（GET session）中断后整会话删除+放 Publisher；防关页/刷新漏删
    # 并发联调开多路时建连可能 >45s；过短会误杀尚未开始轮询的会话
    session_orphan_timeout_s: int = 300


settings = Settings()
settings.avatar_root.mkdir(parents=True, exist_ok=True)


def asr_http_bases() -> list[str]:
    urls = [u.strip().rstrip("/") for u in (settings.asr_http_urls or "").split(",") if u.strip()]
    if urls:
        return urls
    legacy = (settings.asr_url or "").strip().rstrip("/")
    return [legacy] if legacy else ["http://127.0.0.1:8100"]


def tts_http_bases() -> list[str]:
    return [u.strip().rstrip("/") for u in (settings.tts_http_urls or "").split(",") if u.strip()]


def tts_worker_urls() -> list[str]:
    """兼容旧健康检查展示；业务主路径用 HTTP。"""
    return [f"{b}/internal/tts/stream".replace("http://", "ws://").replace("https://", "wss://") for b in tts_http_bases()]
