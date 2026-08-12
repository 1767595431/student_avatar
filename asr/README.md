# 学生端 ASR 独立服务

conda 环境：`student_asr`  
模型：FunASR Paraformer 68M + VAD + 标点  

## 双卡部署（推荐）

| 实例 | 端口 | GPU | workers |
|------|------|-----|---------|
| ASR0 | 8100 | GPU0（与 TTS0 共存） | 2 |
| ASR1 | 8101 | GPU1（与 TTS1 共存） | 2 |

业务 API 对两地址 **least-inflight**；总控 `max_asr_jobs=4`。

```bash
# 仓库根目录（须先起 TTS）
bash start_asr.sh
# 或
bash asr/scripts/start_dual.sh
```

单卡调试仍可用：

```bash
bash asr/scripts/download_models.sh   # 首次
ASR_PORT=8100 CUDA_VISIBLE_DEVICES=0 ASR_DEVICE=cuda:0 bash asr/scripts/start.sh
```

## 接口

`POST /internal/asr/transcribe` multipart：

- `audio`：完整录音
- `session_id` / `question_id`

健康检查：`GET /health`

## 冒烟

```bash
bash asr/scripts/smoke_test.sh
```
