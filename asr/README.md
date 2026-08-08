# 学生端 ASR 独立服务

conda 环境：`student_asr`  
默认端口：`8100`  
默认 GPU：`cuda:0`

## 启动

```bash
bash scripts/download_models.sh   # 首次下载模型
bash scripts/start.sh
```

## 接口

`POST /internal/asr/transcribe` multipart：

- `audio`：完整录音
- `session_id` / `question_id`

健康检查：`GET /health`
