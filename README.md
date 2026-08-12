# 学生端数字人（student_avatar）

浏览器录音 → **ASR** → **Dify** → **TTS** → **Publisher** → **LiveKit** → 浏览器 WebRTC。

| 角色 | 说明 |
|------|------|
| 问答大模型 | 外部 **Dify**（通义等）；智能体只在管理端 `/admin` 登记 API Key |
| ASR / TTS / API | 本机 **conda** |
| LiveKit | **必须 Docker** |
| 远程浏览器 | Nginx 自签 HTTPS（麦克风 + WSS） |

本 README 是**部署 / 安装 / 调试总入口**；细节以对应文档为准，不要只看一处。

---

## 文档索引（有对应 md 的都在这）

| 文档 | 何时看 |
|------|--------|
| [`docs/学生端实时数字人接口文档.md`](docs/学生端实时数字人接口文档.md) | **业务 API 中文说明书**（与 `/docs` 一致） |
| 本 README「日常启动 / 停止」「调试」 | 启停顺序、单路闭环、打断冒烟 |
| [`docs/livekit-deploy.md`](docs/livekit-deploy.md) | LiveKit Docker 安装、密钥、端口、排障 |
| [`docs/nginx-https.md`](docs/nginx-https.md) | 远程 HTTPS / WSS（443、7443） |
| [`tts_qwen/README.md`](tts_qwen/README.md) | TTS 环境安装、启停、踩坑、压测命令 |
| [`docs/qwen3-tts-vllm-flash-attn验证报告.md`](docs/qwen3-tts-vllm-flash-attn验证报告.md) | FLASH_ATTN / 驱动 580 / 双卡并发与压测结论 |
| [`docs/dify-通义千问并发空回答说明.md`](docs/dify-通义千问并发空回答说明.md) | 「智能体无有效回答」根因（通义配额，非本地 TTS） |
| [`asr/README.md`](asr/README.md) | ASR 端口与接口 |
| [`DESIGN.md`](DESIGN.md) | 前端唯一主题（`apps/web/`） |
| [`docs/学生端_Qwen3-TTS-0.6B-Base_vLLM-Omni多并发流式开发方案.md`](docs/学生端_Qwen3-TTS-0.6B-Base_vLLM-Omni多并发流式开发方案.md) | TTS 方案设计全文 |
| [`docs/学生端开发需求与技术方案_v10.md`](docs/学生端开发需求与技术方案_v10.md) | 产品与技术方案总册 |
| [`ChatGPT分析的.md`](ChatGPT分析的.md) | 双卡 vLLM 拓扑分析备忘 |

---

## 架构与入口

```text
管理端 /admin
  形象 / 音模 / 智能体(Dify API Key) → 设默认
        │
        ▼
学生端 /  ·  并发 /concurrent  ·  总控 /monitor
  建会话 → ensure 媒体 → LiveKit
  录音 → ASR → Dify → TTS(音模) → Publisher 推流
```

| 入口 | 用途 |
|------|------|
| `/` | 单路学生：进会话、按住说话、停答 |
| `/concurrent` | 多路联调 |
| `/monitor` | 总控：会话 / ASR / Dify / TTS 并发 |
| `/admin` | 形象、音模、智能体、设默认 |

共用 FastAPI：`/api/v1/sessions*` · `avatars*` · `voices*` · `agents*` · `options` · `monitor/stats`。

### 目录

| 路径 | 说明 |
|------|------|
| `apps/api` | 业务 API + 管理 API（conda `student_api`） |
| `apps/web` | 学生 / 并发 / 总控 / 管理页 |
| `apps/publisher` | LiveKit 推流 + 形象预处理（原分辨率，≤1080p） |
| `asr/` | FunASR Paraformer（conda `student_asr`，**GPU0**） |
| `tts_qwen/` | **唯一 TTS**：Qwen3-TTS-0.6B-Base + vLLM-Omni |
| `data/avatars/` · `data/voices/` | 形象包 / 音模 |
| `deploy/livekit/` | LiveKit Compose |
| `docs/` | 运维与排障手册 |
| `scripts/start_speech_stack.sh` | ASR + TTS 适配层 + API（假定 vLLM 已起） |

---

## 端口

### 必须对公网 / 安全组放行（远程调试）

| 协议 | 端口 | 用途 |
|------|------|------|
| TCP | **80** | HTTP → HTTPS |
| TCP | **443** | 网页 + API |
| TCP | **7443** | LiveKit 信令 WSS |
| TCP | **7881** | LiveKit WebRTC TCP |
| UDP | **50000–60000** | LiveKit 媒体 |

**不要**对公网开：`8000`（API）、`8100–8101`（ASR）、`8091–8092` / `8300–8301`（TTS）、`7880`（由 7443 反代）。详见 [`docs/nginx-https.md`](docs/nginx-https.md)。

### 本机监听

| 组件 | 端口 |
|------|------|
| Nginx | 80 / 443 / 7443 |
| API + Web | 8000 |
| ASR | **8100（GPU0）**、**8101（GPU1）**，各 `max_workers=2` |
| TTS vLLM | 8091（GPU0）、8092（GPU1） |
| TTS 适配层 | 8300→8091、8301→8092 |
| LiveKit | 7880 / 7881，UDP 50000–60000 |

---

## 硬件与驱动（本机已验证）

| 项 | 建议 |
|----|------|
| GPU | 2× RTX 4090（每卡：TTS + ASR 共存） |
| 驱动 | **580.x / CUDA 13** → 默认 **FLASH_ATTN** |
| 旧驱动 570 / CUDA 12.8 | 可跑但需 `TRITON_ATTN`；音质/并发差，见验证报告 |

结论与压测：[`docs/qwen3-tts-vllm-flash-attn验证报告.md`](docs/qwen3-tts-vllm-flash-attn验证报告.md)。TTS 安装细节：[`tts_qwen/README.md`](tts_qwen/README.md)。

---

## 一、首次安装

以下在仓库根目录执行。路径以本机为准时可写成绝对路径。

### 1. 系统依赖

- Docker + Compose v2（**LiveKit 必须**）→ [`docs/livekit-deploy.md`](docs/livekit-deploy.md)
- Miniconda / conda
- `ffmpeg`（形象转码）、NVIDIA 驱动
- 远程访问再装 Nginx → [`docs/nginx-https.md`](docs/nginx-https.md)

### 2. 环境变量

```bash
cp -n .env.example .env
# 编辑 .env：LIVEKIT_API_KEY / LIVEKIT_API_SECRET 必须与 deploy/livekit/livekit.yaml 一致
# 可选：DIFY_BASE_URL（管理端添加智能体时的默认 Base URL）
```

`.env` **只放密钥与可选覆盖**；ASR/TTS 端口、默认形象/音模在代码或管理端，不要往 `.env` 堆。

### 3. conda：业务 API（含 Publisher / LiveKit Python SDK）

```bash
conda create -y -n student_api python=3.11
conda activate student_api
pip install -r apps/api/requirements.txt
```

`apps/api/requirements.txt` 里与 LiveKit **必须**对齐的版本（勿擅自降回）：

| 包 | 版本 | 作用 |
|----|------|------|
| `livekit` | **1.1.14** | Publisher 推流（含起始码率提示，减轻开讲前几秒糊→清） |
| `livekit-api` | **1.0.7** | 签发 AccessToken |

自检推流参数：

```bash
conda activate student_api
python apps/publisher/check_video_publish_opts.py
# 期望：pub≈540x948、bitrate=1Mbps、source=screenshare
```

画面策略摘要（详解 [`docs/livekit-deploy.md`](docs/livekit-deploy.md) §8）：

- Publisher：推流短边≤**540**（1080 竖屏→约 540×948）、**1 Mbps**、`SOURCE_SCREENSHARE`、关 simulcast（素材包仍可原分辨率）  
- 学生端：进会话即暖推流，**锁定 WebRTC**，会话内不回切本地 idle  
- 改推流参数后须 **重启 API**，并 **结束旧会话再进**

### 4. conda：ASR

```bash
conda create -y -n student_asr python=3.10
conda activate student_asr
pip install torch==2.3.1 torchaudio==2.3.1 --index-url https://download.pytorch.org/whl/cu121
pip install -r asr/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host=mirrors.aliyun.com
bash asr/scripts/download_models.sh
```

说明见 [`asr/README.md`](asr/README.md)。每个 ASR 进程 `max_workers=2`；双卡各起一个（`:8100` / `:8101`）。

### 5. TTS（vLLM + 适配层）

```bash
bash tts_qwen/scripts/install_vllm.sh       # conda student_tts_vllm
bash tts_qwen/scripts/install_adapter.sh    # conda student_tts_qwen
bash tts_qwen/scripts/download_models.sh    # 权重先落盘，启动时勿再拉
```

完整步骤与踩坑：[`tts_qwen/README.md`](tts_qwen/README.md)。

### 6. LiveKit（Docker SFU + Python SDK）

**两层都要装齐**，缺一不可：

| 层 | 怎么装 | 文档 |
|----|--------|------|
| SFU 服务端 | Docker Compose（`deploy/livekit/`） | 本节 + [`docs/livekit-deploy.md`](docs/livekit-deploy.md) |
| Python SDK | conda `student_api` 的 `livekit` / `livekit-api`（见上文 §3） | 同左 |

```bash
cd deploy/livekit && bash start.sh
# 或由根目录 start_api.sh 在 TTS/ASR 就绪后自动拉起
curl -s http://127.0.0.1:7880/    # 期望 OK
```

详解（端口、密钥、推流画质、排障）：[`docs/livekit-deploy.md`](docs/livekit-deploy.md)。

### 7. 远程 HTTPS（可选，远程浏览器必做）

按 [`docs/nginx-https.md`](docs/nginx-https.md) 签发自签证书并反代 `443→8000`、`7443→7880`。浏览器信任证书后访问 `https://<IP>/`。

---

## 二、日常启动 / 停止

**启动顺序必须：TTS → ASR → 主服务**（先占好双卡 TTS，再在剩余显存挂 ASR，最后起 API）。

根目录三个脚本：

```bash
# ① TTS（双卡 vLLM :8091/:8092 + 适配层 :8300/:8301；首次约 3–6 分钟）
bash start_tts.sh

# ② ASR（GPU0→:8100、GPU1→:8101，各 2 workers）
bash start_asr.sh

# ③ 主服务（检查 TTS/ASR → LiveKit Docker → 业务 API :8000）
bash start_api.sh
```

| 脚本 | 作用 |
|------|------|
| [`start_tts.sh`](start_tts.sh) / [`stop_tts.sh`](stop_tts.sh) / [`logs_tts.sh`](logs_tts.sh) | TTS 启停 + 实时日志 |
| [`start_asr.sh`](start_asr.sh) / [`stop_asr.sh`](stop_asr.sh) / [`logs_asr.sh`](logs_asr.sh) | ASR 启停 + 实时日志 |
| [`start_api.sh`](start_api.sh) / [`stop_api.sh`](stop_api.sh) / [`logs_api.sh`](logs_api.sh) | LiveKit + API 启停 + 实时日志 |

停止顺序建议：**主服务 → ASR → TTS**（与启动相反）：

```bash
bash stop_api.sh
bash stop_asr.sh
bash stop_tts.sh
```

看日志（`Ctrl+C` 退出）：

```bash
bash logs_tts.sh          # 或 logs_tts.sh vllm0|adapter0 …
bash logs_asr.sh          # 或 logs_asr.sh 0|1
bash logs_api.sh          # 或 logs_api.sh livekit|all
```

API 默认：`MAX_TTS_ACTIVE_JOBS=8`，`MAX_ASR_JOBS=4`（总控展示）。业务侧 ASR/TTS 均为 least-inflight。

页面：

- 本机：`http://127.0.0.1:8000/`
- 远程：`https://<IP>/` · `/concurrent` · `/monitor` · `/admin`

---

## 三、首次业务配置（管理端）

新系统**无预置**形象 / 音模 / 智能体，必须先在 `/admin`：

1. **形象**：上传 idle + talk（或双 mp4）→ 异步预处理。竖/横屏均**保持原分辨率**，超过 1080p 才等比缩小。产物：`data/avatars/<id>/<version>/`。
2. **音模**：上传 wav + 提示文本 → `data/voices/`，TTS 热加载。
3. **智能体**：填 Dify Base URL + API Key → 自动取名。
4. 三项都点**设默认**。

然后学生端 / 并发页：选形象 → 音模 → 智能体 → 进入会话。

---

## 四、调试与验收

### 健康检查

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8100/health
curl -s http://127.0.0.1:8300/health
curl -s http://127.0.0.1:8301/health
curl -s http://127.0.0.1:8091/v1/models | head -c 120; echo
curl -s http://127.0.0.1:7880/
```

接口文档：

- 交互式（中文接口名）：`http://127.0.0.1:8000/docs`
- 说明书：[`docs/学生端实时数字人接口文档.md`](docs/学生端实时数字人接口文档.md)

总控页 `/monitor`：看会话占用、**ASR / TTS 真实槽位**、Dify **仅活跃+峰值**、各后端 **UP/DOWN**。

### 单路闭环清单

1. `GET /api/v1/health` → ok  
2. 管理端有默认形象 / 音模 / 智能体  
3. 学生端进会话 → ensure → LiveKit 订阅音视频  
4. 按住说话 → 识别文本 → 讲话中画面与声音  
5. 答完回待机  

### 打断

```bash
bash apps/api/scripts/smoke_interrupt.sh
```

### TTS 压测（直打引擎）

```bash
python tts_qwen/scripts/bench_vllm_speech.py --dual --ladder 2,4,8,12,16 --stream
```

更多：[`tts_qwen/README.md`](tts_qwen/README.md)、[`docs/qwen3-tts-vllm-flash-attn验证报告.md`](docs/qwen3-tts-vllm-flash-attn验证报告.md)。

### ASR 冒烟

```bash
bash asr/scripts/smoke_test.sh
```

---

## 五、常见故障对照

| 现象 | 先查 | 文档 |
|------|------|------|
| 远程打不开 / 没麦克风 | 443/7443/UDP、证书、Nginx | [`docs/nginx-https.md`](docs/nginx-https.md) |
| LiveKit 连不上 | Docker、密钥与 `.env` 一致、7880 | [`docs/livekit-deploy.md`](docs/livekit-deploy.md) |
| TTS 起不来 / 音质差 | 驱动、FLASH vs TRITON、HF 缓存 | [`tts_qwen/README.md`](tts_qwen/README.md)、验证报告 |
| 识别出字但「智能体无有效回答」 | **Dify/通义并发配额**，不是本地 TTS | [`docs/dify-通义千问并发空回答说明.md`](docs/dify-通义千问并发空回答说明.md) |
| 画面糊、切换讲话更糊 | 形象包分辨率；须重导后再**新开会话** | 管理端重传 / `avatar_preprocess`（原分辨率≤1080p） |
| 清晰↔模糊来回跳 | 是否回切本地 idle；须锁定 WebRTC | [`docs/livekit-deploy.md`](docs/livekit-deploy.md) §8；强刷学生页 |
| 开讲前几秒偏糊再变清 | WebRTC 码率爬升；确认 SDK≥1.1.14 + 推流 540p/1Mbps | `check_video_publish_opts.py`；**新开会话** |
| 多路同时问声音/画面卡 | 编码分辨率过高（非 TTS） | 确认短边≤540、1Mbps；结束旧会话重进 |
| 打断不停 | generation / cancel | `bash apps/api/scripts/smoke_interrupt.sh` |

---

## 六、并发与容量（当前默认）

| 项 | 默认 | 说明 |
|----|------|------|
| 会话硬顶 | 30 | 创建拦截 |
| ASR | 双服务 × `max_workers=2`（总控 `max_asr_jobs=4`） | API **不**二次硬拒；least-inflight |
| TTS 业务槽 | `MAX_TTS_ACTIVE_JOBS=8` | 真闸门；首句 P0 预留 2 |
| Dify | 无本地上限 | 总控只报活跃/峰值；受通义 RPM/TPM 限制 |

联调多标签空答：先看 Dify/通义，再看 TTS。

---

## 管理端能力摘要

| 能力 | 说明 |
|------|------|
| 形象 | 上传 mp4 → 转码抽帧 → `data/avatars/` |
| 音模 | wav + 提示文本 → `data/voices/`，热加载 TTS |
| 智能体 | Dify API Key → 学生端 / 并发可选 |

前端视觉约束：[`DESIGN.md`](DESIGN.md)（仅 `apps/web/`，勿另起主题）。
