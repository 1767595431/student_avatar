# Nginx + 自签 HTTPS

远程浏览器访问本服务（麦克风 + LiveKit）用这一套。

证书：`/home/ubuntu/AI/ssl/server.crt`、`/home/ubuntu/AI/ssl/server.key`  
反代：`443` → `127.0.0.1:8000`，`7443` → `127.0.0.1:7880`  
页面：`https://<IP>/` 、`https://<IP>/concurrent`

## 必须开通的端口

| 协议 | 端口 | 用途 |
|------|------|------|
| TCP | **80** | HTTP → HTTPS |
| TCP | **443** | 网页 + API |
| TCP | **7443** | LiveKit 信令 WSS |
| TCP | **7881** | LiveKit WebRTC TCP |
| UDP | **50000–60000** | LiveKit 媒体 |

## 安装

```bash
PUBLIC_IP=你的公网IP
LAN_IP=$(hostname -I | awk '{print $1}')

sudo apt update
sudo apt install -y openssl nginx

mkdir -p /home/ubuntu/AI/ssl
cd /home/ubuntu/AI/ssl
sudo openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout server.key -out server.crt -days 3650 \
  -subj "/C=CN/ST=Shanghai/L=Shanghai/O=StudentAvatar/OU=Web/CN=${PUBLIC_IP}" \
  -addext "subjectAltName=IP:${PUBLIC_IP},IP:${LAN_IP},DNS:localhost"
sudo chmod 600 server.key && sudo chmod 644 server.crt
sudo chown -R ubuntu:ubuntu /home/ubuntu/AI/ssl

sudo tee /etc/nginx/sites-available/student-avatar >/dev/null <<EOF
server {
    listen 443 ssl;
    server_name ${PUBLIC_IP} ${LAN_IP};
    ssl_certificate     /home/ubuntu/AI/ssl/server.crt;
    ssl_certificate_key /home/ubuntu/AI/ssl/server.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    client_max_body_size 32m;
    add_header Permissions-Policy "microphone=*" always;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
}
server {
    listen 7443 ssl;
    server_name ${PUBLIC_IP} ${LAN_IP};
    ssl_certificate     /home/ubuntu/AI/ssl/server.crt;
    ssl_certificate_key /home/ubuntu/AI/ssl/server.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    location / {
        proxy_pass http://127.0.0.1:7880;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
server {
    listen 80;
    server_name ${PUBLIC_IP} ${LAN_IP};
    return 301 https://\$host\$request_uri;
}
EOF

sudo ln -sf /etc/nginx/sites-available/student-avatar /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl enable --now nginx && sudo systemctl reload nginx
```

## `.env`

Publisher 本机连 LiveKit（不要改成公网）：

```text
LIVEKIT_URL=ws://127.0.0.1:7880
```

浏览器在 HTTPS 下由前端自动改成 `wss://当前IP:7443`。

## 运维

```bash
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl status nginx
ls -l /home/ubuntu/AI/ssl/
```

自签证书需在浏览器点「继续访问」。
