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

当前 compose 固定镜像：`livekit/livekit-server:v1.8.4`。升级时只改镜像 tag 并 `docker compose pull && docker compose up -d`。
