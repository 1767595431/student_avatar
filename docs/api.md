# 业务 API 接口说明书

> 交互式文档（Swagger）：主服务启动后打开 `http://<主机>:8000/docs`  
> 备用 ReDoc：`http://<主机>:8000/redoc`  
> OpenAPI JSON：`http://<主机>:8000/openapi.json`

本文件与 `/docs` 中文分组一致。链路：**录音 → ASR → Dify → TTS → Publisher → LiveKit**。

---

## 1. 约定

| 项 | 说明 |
|----|------|
| Base | 本机 `http://127.0.0.1:8000`；远程经 Nginx 为 `https://<IP>` |
| 前缀 | 业务接口均在 `/api/v1/...` |
| 鉴权 | 当前实验室版无 Token；生产请自行加网关鉴权 |
| 上传 | `multipart/form-data` |
| JSON | `application/json` |
| 会话状态 | `idle` 待机 / `recognizing` 识别 / `thinking` 思考 / `speaking` 讲话 / `interrupting` 打断 / `closed` 已关闭 |

启动顺序见根目录：`start_tts.sh` → `start_asr.sh` → `start_api.sh`。

---

## 2. 健康检查

### `GET /api/v1/health` · 健康检查

返回服务摘要：ASR/TTS 地址列表、LiveKit、并发上限等。

---

## 3. 会话（学生端主流程）

### `POST /api/v1/sessions` · 创建会话

Body JSON：

| 字段 | 必填 | 说明 |
|------|------|------|
| `student_id` | 是 | 学生标识 |
| `avatar_id` / `avatar_version_id` | 否 | 缺省用管理端默认形象 |
| `voice_id` | 否 | 缺省用默认音模 |
| `agent_id` | 否 | 缺省用默认智能体 |
| `class_id` / `course_id` | 否 | 业务扩展字段 |

返回：`session_id`、`livekit_url`、`livekit_token`、`room_name`、待机图/视频 URL、初始 `state`。

### `GET /api/v1/sessions/{session_id}` · 查询会话状态

轮询心跳（刷新 `updated_at`）。返回 `state`、`recognized_text`、`pipeline_stage`、`generation`、`qa_to_speak_ms` 等。

### `POST /api/v1/sessions/{session_id}/media/ensure` · 确保媒体推流

拉起 / 复用 LiveKit Publisher，返回订阅用 Token 与媒体状态。

### `POST /api/v1/sessions/{session_id}/questions` · 提交语音问题

`multipart`：字段 `audio`（完整录音文件）。  
流程：ASR 识别 → Dify 流式回答 → 分句 TTS → 推流讲话。若上一轮未结束会先打断。

### `POST /api/v1/sessions/{session_id}/interrupt` · 打断回答

取消 Dify/TTS，`generation+1` 丢弃旧 PCM，状态回 `idle`。

### `DELETE /api/v1/sessions/{session_id}` · 结束会话  
### `POST /api/v1/sessions/{session_id}/close` · 结束会话（POST）

释放 Publisher 并从会话表删除（两路由等价，兼容关页信标）。

---

## 4. 总控

### `GET /api/v1/monitor/stats` · 总控统计与服务连通

| 块 | 含义 |
|----|------|
| `sessions` | 会话占用 / 容量 / 状态分布 |
| `asr` / `tts` | 并发活跃、峰值、上限、`by_base` 分实例在飞 |
| `dify` | 仅活跃与峰值（无本地假上限） |
| `publishers` | 当前推流 Publisher 数 |
| `services` | LiveKit / ASR×2 / TTS 适配×2 / TTS 引擎×2 / Dify 的 UP/DOWN 与延迟 |

前端页：`/monitor`。

---

## 5. 选项（学生端下拉）

### `GET /api/v1/options` · 学生端可选资源

返回就绪形象、音模、智能体列表及 `defaults`。

---

## 6. 形象管理

| 方法 | 路径 | 中文名 |
|------|------|--------|
| GET | `/api/v1/avatars` | 列出形象 |
| POST | `/api/v1/avatars` | 上传形象（`name` + `idle_video` + `talk_video`） |
| PATCH | `/api/v1/avatars/{avatar_id}` | 形象改名（form `name`） |
| DELETE | `/api/v1/avatars/{id}/versions/{vid}` | 删除形象版本 |
| POST | `.../default` | 设为默认形象 |
| PATCH | `.../frames` | 调整帧区间（旧单视频包） |
| GET | `.../idle.png` | 形象待机图 |
| GET | `.../idle.mp4` | 形象待机视频 |
| GET | `.../frames/{n}.png` | 形象帧图片 |

上传后异步预处理：保持原分辨率，超过 1080p 才缩小；`status=processing` → `ready`。

---

## 7. 音模管理

| 方法 | 路径 | 中文名 |
|------|------|--------|
| GET | `/api/v1/voices` | 列出音模 |
| POST | `/api/v1/voices` | 上传音模（`name` + `prompt_wav`，ASR 自动识提示文本） |
| GET | `/api/v1/voices/{id}/prompt.wav` | 试听音模 |
| POST | `/api/v1/voices/{id}/default` | 设为默认音模 |
| DELETE | `/api/v1/voices/{id}` | 删除音模 |

---

## 8. 智能体管理

| 方法 | 路径 | 中文名 |
|------|------|--------|
| GET | `/api/v1/agents` | 列出智能体（含 `api_key_masked`） |
| POST | `/api/v1/agents` | 添加智能体（form `api_key`） |
| POST | `/api/v1/agents/{id}/default` | 设为默认智能体 |
| DELETE | `/api/v1/agents/{id}` | 删除智能体 |

名称与 `mode` 从 Dify `/v1/info` 拉取；Base URL 用环境变量 `DIFY_BASE_URL`。

---

## 9. 典型调用顺序（单路）

```text
1. GET  /api/v1/options
2. POST /api/v1/sessions
3. POST /api/v1/sessions/{id}/media/ensure  → 浏览器连 LiveKit
4. POST /api/v1/sessions/{id}/questions     → 上传录音
5. GET  /api/v1/sessions/{id}               → 轮询直到 idle
6. （可选）POST .../interrupt
7. DELETE /api/v1/sessions/{id}
```

---

## 10. 相关文档

| 文档 | 内容 |
|------|------|
| [`README.md`](../README.md) | 部署 / 三脚本启动 |
| [`p1-runbook.md`](p1-runbook.md) | 联调清单 |
| [`dify-通义千问并发空回答说明.md`](dify-通义千问并发空回答说明.md) | 智能体空回答排查 |
| [`livekit-deploy.md`](livekit-deploy.md) | LiveKit |
| [`nginx-https.md`](nginx-https.md) | 远程 HTTPS |
