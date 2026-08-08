# P1 单路闭环运行手册

## 组件与端口

| 组件 | 启动方式 | 端口 |
|---|---|---|
| LiveKit SFU | **Docker Compose（必须）** | 7880 / 7881 / UDP 50000-60000 |
| ASR | conda `student_asr` | 8100 |
| TTS | conda `student_tts` | 8200 |
| 业务 API + Web | conda `student_api` | 8000 |

Dify 外部服务：`http://117.50.223.142:7000/`（智能体：`聊天机器人`）

## 一键启动顺序

```bash
# 0) 环境变量
cp -n /home/ubuntu/AI/student_avatar/.env.example /home/ubuntu/AI/student_avatar/.env
# 编辑 .env 填入 DIFY_API_KEY 等

# 1) LiveKit（Docker 必须）
cd /home/ubuntu/AI/student_avatar/deploy/livekit
bash start.sh
curl -s http://127.0.0.1:7880/   # 期望 OK

# 2) ASR
bash /home/ubuntu/AI/student_avatar/asr/scripts/start.sh
# 另开终端

# 3) TTS（双卡 Worker，P3）
bash /home/ubuntu/AI/student_avatar/tts/scripts/start_gpu0.sh   # :8200 GPU0
# 另开终端
bash /home/ubuntu/AI/student_avatar/tts/scripts/start_gpu1.sh   # :8201 GPU1

# 4) 业务 API（托管最小 Web）
bash /home/ubuntu/AI/student_avatar/apps/api/scripts/start.sh
```

浏览器：`https://<IP>/` 、`https://<IP>/concurrent`、管理端 `https://<IP>/admin`（形象/音模；须开通端口见根目录 `README.md` / [`nginx-https.md`](nginx-https.md)）

## Avatar Package

默认路径：`data/avatars/avatar_001/avv_001/`

重新生成（无真实素材时会合成 6 秒测试视频）：

```bash
conda activate student_api
python /home/ubuntu/AI/student_avatar/apps/publisher/avatar_preprocess.py \
  --input /home/ubuntu/AI/student_avatar/data/avatars/_src/avatar.mp4 \
  --avatar-id avatar_001 --version-id avv_001
```

## 联调检查清单

1. `GET http://127.0.0.1:8000/health` → ok  
2. `POST /api/v1/sessions` → 返回 session + idle_image_url  
3. `POST .../media/ensure` → LiveKit publisher 进房  
4. 页面按住说话 → ASR 文本 → Dify → TTS 整段 PCM → 画面 speaking
5. 答完回 idle；`video_track_recreate_count` 保持 0  

## P2 打断验收

```bash
# 服务已启动后：
bash /home/ubuntu/AI/student_avatar/apps/api/scripts/smoke_interrupt.sh
```

期望：讲话中 `POST .../interrupt` → `state=idle`，`generation` 递增，之后不会再次回到 `speaking`。

页面：讲话中点「停止回答」，或讲话中再次按住说话（会先打断再录音）。
