# Qwen3-TTS 0.6B Base（vLLM-Omni 多并发）

按 `学生端_Qwen3-TTS-0.6B-Base_vLLM-Omni多并发流式开发方案.md`。

```text
业务 API（多路上 TTS；分句有序合成）
   ↓  least-inflight
适配层 :8300 / :8301
   ↓  POST /v1/audio/speech  stream pcm 24k
vLLM-Omni GPU0 :8091   vLLM-Omni GPU1 :8092
   Qwen3-TTS-12Hz-0.6B-Base + deploy/qwen3_tts.yaml
```

## 本机已验证环境

| 项 | 值 |
|----|-----|
| GPU | 2× RTX 4090 |
| 驱动 | 570.153.02（nvidia-smi 报 CUDA 12.8） |
| conda | `student_tts_vllm`（vLLM / vLLM-Omni 0.26） |
| 适配层 | `student_tts_qwen`（或暂用已有 `student_tts_moss` conda 名） |
| 模型 | `Qwen/Qwen3-TTS-12Hz-0.6B-Base` → `tts_qwen/data/hf_cache` |

## 首次安装

```bash
bash tts_qwen/scripts/install_vllm.sh       # student_tts_vllm
bash tts_qwen/scripts/install_adapter.sh    # student_tts_qwen
bash tts_qwen/scripts/download_models.sh    # 权重落盘，勿启动时再拉
```

## 日常启停

```bash
# 仅双卡 vLLM（官方路径压测）
env -u HF_HOME -u HUGGINGFACE_HUB_CACHE bash tts_qwen/scripts/start_vllm_only.sh

# 或：vLLM + 适配层
env -u HF_HOME -u HUGGINGFACE_HUB_CACHE bash tts_qwen/scripts/start_workers.sh

curl -s http://127.0.0.1:8091/v1/models | head -c 80; echo
bash tts_qwen/scripts/stop_workers.sh
```

压测（直打 `/v1/audio/speech`）：

```bash
# 单卡 1→10
python tts_qwen/scripts/bench_vllm_speech.py --urls http://127.0.0.1:8091 --ladder 1,2,4,6,8,10 --stream
# 双卡合计
python tts_qwen/scripts/bench_vllm_speech.py --dual --ladder 2,4,8,12,16,20 --stream
```

结果摘要：`tts_qwen/data/bench_summary.json`。

> 驱动已升到 **580.173 / CUDA 13.0**，默认 **`FLASH_ATTN`**（勿再强制 TRITON）。旧机可设 `TTS_ATTENTION_BACKEND=TRITON_ATTN` + `TTS_ENFORCE_EAGER=1`。

## 协议 / 音模

- 对外：`WS /internal/tts/stream` + `POST /internal/tts/synthesize`（PCM **24k mono**）
- 后端固定 **vLLM-Omni**（本机需 `enforce_eager` + `TRITON_ATTN` + `custom_ops: none`，见踩坑）
- 音模：`data/voices/{id}/prompt.wav` 须真 RIFF PCM + `prompt_text`

冒烟：

```bash
conda activate student_tts_qwen   # 或 TTS_ADAPTER_ENV=…
python tts_qwen/scripts/smoke_stream.py ws://127.0.0.1:8300/internal/tts/stream
```

## 踩坑记录

### 1. deploy-config 路径被 INFO 日志污染

用仓库内 [`deploy/qwen3_tts.yaml`](deploy/qwen3_tts.yaml)，**不要** `import vllm_omni` 解析路径。

### 2. CUDA 驱动 12.8 vs vLLM custom op → cudart 13

本机已固化：`enforce_eager` + `custom_ops: none` + `--attention-backend TRITON_ATTN`。驱动升到 CUDA 13 后再试 FLASH_ATTN。

### 3. 启动时再拉权重 → HF Xet 401

先 `download_models.sh`，启动用本地 snapshot；`HF_HUB_DISABLE_XET=1` + `HF_HUB_OFFLINE=1`。

### 4. shell 残留 `HF_HOME`

启动前：`env -u HF_HOME -u HUGGINGFACE_HUB_CACHE bash tts_qwen/scripts/start_workers.sh`

### 5. 适配层 `model` 名要对上 `/v1/models`

本地 snapshot 时 `id` 是绝对路径；`TTS_MODEL_ID` 留空，由 `engine_vllm._discover_model_id` 发现。

## 关键文件

| 路径 | 作用 |
|------|------|
| `scripts/install_vllm.sh` / `install_adapter.sh` | 环境 |
| `scripts/download_models.sh` | 拉权重 |
| `scripts/start_workers.sh` / `stop_workers.sh` | 双卡 + 适配层 |
| `deploy/qwen3_tts.yaml` | eager + `custom_ops: none` |
| `service/engine_vllm.py` | least-inflight、24k PCM、Base 克隆 |
| `service/main.py` | 学生协议 |
