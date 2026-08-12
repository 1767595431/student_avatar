# LiveKit SFU 安装与部署（必须使用 Docker）

> 项目：学生端数字人 P1  
> 部署目录：`deploy/livekit/`  
> 运行方式：**仅支持 Docker Compose + `network_mode: host`**

## 1. 前置条件

- Linux 主机（本机已验证 Ubuntu）
- 已安装 **Docker** 与 **Docker Compose v2**（**必须用 Docker 运行 LiveKit**）
- 若拉取 Docker Hub 超时，配置镜像加速（本机已用 DaoCloud）：

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://mirror.ccs.tencentyun.com"
  ]
}
EOF
sudo systemctl restart docker
```

`docker-compose.yml` 默认镜像：

`docker.m.daocloud.io/livekit/livekit-server:v1.8.4`

开放端口（防火墙 / 安全组）：

| 端口 | 协议 | 用途 |
|---|---|---|
| 7880 | TCP | LiveKit HTTP / WebSocket 信令 |
| 7881 | TCP | WebRTC over TCP |
| 50000-60000 | UDP | WebRTC 媒体 |

安装 Docker（Ubuntu 示例）：

```bash
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# 重新登录后生效；临时可用：
sudo chmod 666 /var/run/docker.sock
```

**不要**使用非 Docker 的二进制直装作为本项目标准路径。

## 2. 配置说明

文件：

- `docker-compose.yml`：拉取并运行 `livekit/livekit-server`，**必须** `network_mode: host`
- `livekit.yaml`：端口、UDP 区间、API Key/Secret

默认密钥（P1 实验室用，生产请轮换）：

```text
LIVEKIT_API_KEY=APIstudentboPs5W9J
LIVEKIT_API_SECRET=fcJea_2jFHU5lTix_arQABRREMmyOeX3a2zrSN4RuLs
```

业务侧 `.env` 应对齐上述密钥，以及：

```text
LIVEKIT_URL=ws://127.0.0.1:7880
# 浏览器访问时改为公网/局域网 IP，例如 ws://10.60.89.85:7880
```

`livekit.yaml` 中 `rtc.use_external_ip: true`：云主机常见配置，用 STUN 发现公网 IP。若纯内网联调可改为 `false`。

## 3. 启动 / 停止

```bash
cd /home/ubuntu/AI/student_avatar/deploy/livekit
bash start.sh
# 或
docker compose up -d

# 停止
bash stop.sh
```

健康检查：

```bash
curl -sS http://127.0.0.1:7880/
docker compose -f /home/ubuntu/AI/student_avatar/deploy/livekit/docker-compose.yml ps
docker logs student-livekit --tail 50
```

## 4. 本机联调

1. 启动 LiveKit（Docker）
2. 业务 API 用 `LIVEKIT_API_KEY/SECRET` 签发 token
3. Publisher 以房间身份 publish 音视频轨
4. 学生端 Web 以 subscriber 身份 join 同一房间

Token 房间名建议：`session_{session_id}`。

## 5. HTTPS / 麦克风

见 [nginx-https.md](nginx-https.md)。须开通：TCP `80/443/7443/7881`，UDP `50000–60000`。

## 6. 常见问题

**Q: 为什么必须 Docker + host 网络？**  
WebRTC 需要大范围 UDP 端口。Docker bridge NAT 对媒体端口不友好；`network_mode: host` 是 LiveKit 官方推荐的自建方式。

**Q: 容器起来但浏览器连不上？**  
检查安全组是否放行 7880/7881/UDP 50000-60000；核对 `LIVEKIT_URL` 是否使用客户端可达的 IP。

**Q: 如何换密钥？**  
1. 修改 `livekit.yaml` 的 `keys`  
2. 同步修改项目 `.env`  
3. `docker compose up -d` 重启容器  

## 7. 版本

| 组件 | 版本 | 备注 |
|------|------|------|
| LiveKit Server（Docker） | `livekit/livekit-server:v1.8.4` | compose 固定 tag；升级只改 tag 后 `pull && up -d` |
| Python `livekit` | **1.1.14** | `apps/api/requirements.txt`；Publisher 用 |
| Python `livekit-api` | **1.0.7** | 签发 Token；与 `livekit` 一起装进 `student_api` |

**不要**把 `livekit` 降回 `0.17.x`：旧版缺少起始码率 / `degradation_preference`，开讲更容易「先糊后清」。

升级 Server：

```bash
# 只改 docker-compose.yml 镜像 tag 后：
cd /home/ubuntu/AI/student_avatar/deploy/livekit
docker compose pull && docker compose up -d
```

升级 Python SDK（在仓库根目录）：

```bash
conda activate student_api
pip install -r apps/api/requirements.txt
# 然后重启主服务，旧会话作废重进
bash stop_api.sh && bash start_api.sh
```

## 8. 推流画质（Publisher + 学生端）

数字人画面走 **一条 WebRTC 视频轨**。安装/联调时注意：

### 8.1 Publisher（`apps/publisher/publisher.py`）

安装 `student_api` 依赖后，推流默认：

| 项 | 值 | 原因 |
|----|----|------|
| `max_bitrate` | 按像素缩放，**上限 12 Mbps**（1080×1896 打满） | 竖屏像素多，低码率会糊 |
| `max_framerate` | 25 | 与形象包 fps 一致 |
| `simulcast` | `false` | 避免订到低清层 |
| `source` | `SOURCE_SCREENSHARE` | 拥堵时优先保分辨率（非摄像头跟手） |
| `degradation_preference` | `MAINTAIN_RESOLUTION` | 同上；需 `livekit≥1.1` |

自检（无需连房）：

```bash
conda activate student_api
python apps/publisher/check_video_publish_opts.py
```

改参数或升级 SDK 后：**重启 API**，浏览器 **结束会话再进入**（旧 Publisher 进程仍是旧编码参数）。

### 8.2 学生端页面（`apps/web/index.html`）

- 进入会话后立刻 `ensure` 暖推流；订阅到视频后 **锁定 WebRTC 层**  
- 会话内 **不回切** 本地 `idle.mp4`（本地片更清晰，来回切会「一会儿清一会儿糊」）  
- 本地 idle 仅作进房前占位  

并发页 `concurrent.html` 同样：媒体回收后立刻重连暖推流，不把画面藏掉。

### 8.3 现象对照

| 现象 | 是否正常 | 处理 |
|------|----------|------|
| 会话内清晰↔模糊来回跳 | 否 | 确认已部署 §8.2；强刷页面；新开会话 |
| 开讲前 1–几秒偏软再变清 | 码率爬升，减轻后仍可能有极短收敛 | 确认 §7 SDK 版本 + §8.1；局域网差时更明显 |
| 整体始终偏糊 | 否 | 形象是否 ≤1080p 原分辨率包；码率/SDK；新开会话 |
