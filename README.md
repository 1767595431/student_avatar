# 学生端数字人（student_avatar）

浏览器录音 → ASR → Dify → TTS → Publisher → LiveKit → 浏览器 WebRTC。

问答大模型：外部 [Dify](http://117.50.223.142:7000)（`.env` 配 `DIFY_API_KEY`）；当前智能体 **聊天机器人**（`GET /v1/info`）。

ASR / TTS / API：本机 conda。**LiveKit：必须 Docker。** 远程访问走 Nginx 自签 HTTPS。

## 必须开通的端口

安全组 / 防火墙按此放行：

| 协议 | 端口 | 用途 |
|------|------|------|
| TCP | **80** | HTTP → HTTPS |
| TCP | **443** | 网页 + API |
| TCP | **7443** | LiveKit 信令 WSS |
| TCP | **7881** | LiveKit WebRTC TCP |
| UDP | **50000–60000** | LiveKit 媒体 |

不要对公网开放：`8000`（API）、`8100`（ASR）、`8200–8203`（TTS）、`7880`（由 7443 反代）。

## 本机端口

| 组件 | 端口 |
|------|------|
| Nginx | TCP 80 / 443 / 7443 |
| API + Web | TCP 8000 |
| ASR | TCP 8100 |
| TTS Workers | TCP 8200–8203 |
| LiveKit | TCP 7880 / 7881，UDP 50000–60000 |

## 三端怎么串

```text
管理端 `/admin`
  形象 / 音模 / 智能体(Dify API Key) → 设默认
        │
        ▼  学生端与并发页必选：形象 + 音模 + 智能体
学生端 /  与  并发页 /concurrent
  建会话(带三项选择) → ensure 媒体 → LiveKit
  录音 → ASR → 所选智能体 → TTS(所选音模) → 推流
```

| 入口 | 业务 |
|------|------|
| `/` | 单路学生：进入会话、按住说话、停答、看数字人 |
| `/concurrent` | 多路联调：N 路画面、录一段、按间隔交替提交、全打断 |
| `/admin` | 后台：形象、音模、**智能体(Dify API Key)**、设默认 |
| `/` / `/concurrent` | 选形象 → 选音模 → 选智能体 → 再问答/并发 |

共用同一套 FastAPI：`/api/v1/sessions*` + `/api/v1/avatars*` + `/api/v1/voices*` + `/api/v1/agents*` + `/api/v1/options`。

## 管理端

只做两件事（无审核/发布、无豆包对接）：

| 能力 | 说明 |
|------|------|
| **形象** | 上传 mp4 → 异步转码抽帧 → `data/avatars/`；滑块调 idle/talk |
| **音模** | 上传 wav + 提示文本 → `data/voices/`，并热加载到 TTS |
| **智能体** | 登记 Dify API Key → 自动取名称 → 学生端/并发页可选 |

## 目录

| 路径 | 说明 |
|------|------|
| `apps/api` | 业务 API + 管理 API |
| `apps/web` | 学生页 / 并发页 / 管理页 |
| `apps/publisher` | LiveKit 推流 + 形象预处理 |
| `asr/` | FunASR |
| `tts/` | CosyVoice3 |
| `data/avatars/` | 形象 Package |
| `data/voices/` | 音模 |
| `deploy/livekit/` | LiveKit Compose |
| `docs/` | 手册 |

## 启动

```bash
cd deploy/livekit && bash start.sh
bash asr/scripts/start.sh
bash tts/scripts/start_workers.sh
bash apps/api/scripts/start.sh
```

`.env.example` → `.env`。更多：[`docs/p1-runbook.md`](docs/p1-runbook.md) · [`docs/livekit-deploy.md`](docs/livekit-deploy.md)
