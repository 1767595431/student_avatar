# 学生端 TTS 独立服务（CosyVoice3）

conda 环境：`student_tts`  
默认端口：`8200`（多 Worker：`8200–8203`）  
**主路径：非流式 HTTP 整段合成**

## 首次准备

```bash
bash scripts/install_deps.sh
bash scripts/download_models.sh
```

## 启动

```bash
bash scripts/start_workers.sh   # 推荐：多 Worker
# 或单进程：
bash scripts/start.sh
```

## 接口

- `POST /internal/tts/synthesize`：**主路径**，正文带 `voice_id`，用对应参考 wav 整段合成
- `POST /internal/tts/voices/{voice_id}`：注册/更新音模（管理端调用，**无需重启**）
- `DELETE /internal/tts/voices/{voice_id}`：删除音模
- `POST /internal/tts/voices/reload`：从磁盘扫一遍音模目录
- `GET /health`
- `WS /internal/tts/stream`：遗留，业务 API 不用
