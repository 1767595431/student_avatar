# Qwen3-TTS 0.6B Base + vLLM-Omni 验证报告

> 日期：2026-08-13  
> 机器：2 × RTX 4090 48GB（Ubuntu 22.04）  
> 依据：`ChatGPT分析的.md` 推荐拓扑 + 官方 vLLM-Omni Speech API  
> 结论：**驱动升到 580 / CUDA 13 并启用 FLASH_ATTN 后，官方路径音质与并发均验证通过。**

---

## 1. 验证目标

1. 按「每卡 1 个完整 Qwen3-TTS Base + vLLM-Omni」部署双卡服务。  
2. 摸清真实可同时合成的请求数与响应延迟。  
3. 尽量吃满两张 48G 显存。  
4. 确认能否跑官方推荐的 **FLASH_ATTN**（而非本机旧驱动被迫使用的 TRITON）。

---

## 2. 最终生效环境

| 项 | 值 |
|---|---|
| GPU | 2 × NVIDIA GeForce RTX 4090 48GB |
| 驱动 | **580.173.02**（由 570.153.02 / CUDA 12.8 升级） |
| `nvidia-smi` CUDA | **13.0** |
| cudart13 自检 | `cudaGetDeviceCount` → **rc=0, n=2** |
| 模型 | `Qwen/Qwen3-TTS-12Hz-0.6B-Base`（本地 snapshot） |
| 推理栈 | `vllm` / `vllm-omni` **0.26.0**，conda `student_tts_vllm` |
| Attention | **FLASH_ATTN**（FlashAttention v2） |
| 拓扑 | GPU0 → `:8091`；GPU1 → `:8092`（各跑完整 Stage0+Stage1） |
| Stage1 `max_num_seqs` | **10**（官方 Base 调度上限） |
| `async_chunk` | **true**（官方默认流式 chunk） |
| 显存分摊 | Stage0 `gpu_memory_utilization=0.58`，Stage1 `0.35` |
| 压测时显存 | 约 **35–40 GiB / 48 GiB** 每卡 |

启动命令（摘要）：

```bash
env -u HF_HOME -u HUGGINGFACE_HUB_CACHE bash tts_qwen/scripts/start_vllm_only.sh
# 默认 TTS_ATTENTION_BACKEND=FLASH_ATTN
```

配置文件：`tts_qwen/deploy/qwen3_tts.yaml`  
压测脚本：`tts_qwen/scripts/bench_vllm_speech.py`（直打 `/v1/audio/speech`，绕过业务适配层）

---

## 3. 关键踩坑（升级前）

### 3.1 驱动 CUDA 12.8 无法跑 FLASH_ATTN

旧驱动 `570.153.02` 只支持到 CUDA **12.8**，而 vLLM 的 FLASH_ATTN / 部分 custom op 链到 **`libcudart.so.13`**，会出现：

> CUDA driver version is insufficient for CUDA runtime version

被迫改用：

- `--attention-backend TRITON_ATTN`
- `enforce_eager` + `custom_ops: none`

### 3.2 TRITON 路径实测不可用作生产音质

同短句「今天天气不错，我们开始上课。」在 TRITON 下：

| 现象 | 表现 |
|---|---|
| 音频时长 | 常被拉到 **15–30s**（正常应约 3s） |
| 高并发 | 大量空 PCM / 失败 |
| 整段 wall | 单路也要 **20s+** |
| GPU | util 可打满，但产出质量差 |

另外文档建议的 `--no-async-chunk` 在本机 TRITON 下会让 talker 跑满 `max_tokens`（单请求 200s+），已放弃；保持官方 `async_chunk: true`。

### 3.3 驱动升级动作

1. 卸载旧 runfile 驱动（`nvidia-uninstall`）  
2. `apt install nvidia-driver-580`（**580.173.02** ≥ CUDA 13 最低要求 **580.65**）  
3. 重启后确认 `nvidia-smi` 显示 **CUDA Version: 13.0**  
4. 启动脚本默认改为 **FLASH_ATTN**，去掉强制 eager

---

## 4. FLASH_ATTN 验证结果（主结论）

测试文本（Base 克隆，音模 `voice_aacd9fa8` / 康辉）：

```text
今天天气不错，我们开始上课。
```

协议：`POST /v1/audio/speech`，`stream=true`，`response_format=pcm`，24 kHz mono。

### 4.1 单路样音

| 指标 | 结果 |
|---|---|
| TTFA（首包） | **0.107 s** |
| 整段 wall | **0.456 s** |
| 音频时长 | **2.88 s**（正常） |
| RTF | **0.158** |
| 样音文件 | `tts_qwen/data/sample_flash_c1.wav` |

### 4.2 单卡并发阶梯

| 并发 | 成功 | TTFA P50 | wall P50 | 音频均值 | RTF |
|---|---|---|---|---|---|
| 1 | 1/1 | 1.436 s* | 1.77 s | 2.88 s | 0.615 |
| 2 | 2/2 | **0.163 s** | 0.59 s | 2.84 s | 0.208 |
| 4 | 4/4 | **0.257 s** | 0.73 s | 2.82 s | 0.258 |

\* 首波冷启动 / cudagraph 预热偏高；热路径以 c≥2 为准。

### 4.3 双卡合计并发

| 合计并发 | 成功 | TTFA P50 | wall P50 | 音频均值 | 备注 |
|---|---|---|---|---|---|
| 2（1+1） | **2/2** | 0.107 s | 0.441 s | 2.84 s | |
| 4（2+2） | **4/4** | 0.184 s | 0.608 s | 2.86 s | util ~97% |
| 8（4+4） | **8/8** | **0.286 s** | 0.772 s | 2.83 s | 对齐文档生产首版建议 |
| 16（8+8） | **16/16** | **0.379 s** | 1.159 s | 2.83 s | RTF ≈ 0.40；显存峰值约 40 GiB |

原始 JSON：

- `tts_qwen/data/bench_flash_s1.json`
- `tts_qwen/data/bench_flash_dual.json`
- `tts_qwen/data/bench_flash_summary.json`

---

## 5. 与 ChatGPT / 官方依据对照

| 官方/文档说法 | 本机验证 |
|---|---|
| 每卡 1 个独立 vLLM-Omni 实例 | ✅ `:8091` + `:8092` |
| Stage1 `max_num_seqs=10` | ✅ 已配置 |
| 4090 上 QPS≈4 时 TTFB 可控 | ✅ 单卡 4 路 TTFA ≈ **0.26 s** |
| 生产首版建议 4+4=8 active | ✅ **8/8 全成功**，TTFA ≈ **0.29 s** |
| 可再压到更高 | ✅ **双卡 16 路全成功**，TTFA ≈ **0.38 s** |
| 必须 FLASH_ATTN 才有可用音质 | ✅ TRITON 失败路径已证实；升级后恢复 |

---

## 6. TRITON vs FLASH 对比（同机、同句）

| 维度 | TRITON（驱动 570 / CUDA 12.8） | FLASH（驱动 580 / CUDA 13） |
|---|---|---|
| 短句音频时长 | 15–30 s（异常） | **~2.8 s（正常）** |
| 单路 wall | 20 s+ | **< 0.5 s（热）** |
| 双卡 8 路 | 6/8，质量差 | **8/8** |
| 双卡 16 路 | 大量失败 / 空包 | **16/16** |
| RTF | 无意义（时长乱） | **0.16–0.40** |

---

## 7. 结论与建议

### 已验证通过

1. **官方推荐部署拓扑在本机可用**：双卡各一路 Qwen3-TTS 0.6B Base + vLLM-Omni。  
2. **FLASH_ATTN 音质/时长正常**，可作生产推理后端。  
3. **文档建议的 8 路同时合成（4/卡）完全站得住**；本机还可稳定到 **16 路**（短句压测）。  
4. 显存已用到约 **40 GiB / 48 GiB**，GPU util 高并发下可达 **95–100%**。

### 生产配置建议（当前）

```text
GPU0 / GPU1 各 1 个 vLLM-Omni
Engine Stage1 max_num_seqs = 10
业务 active 上限首版：4 + 4 = 8
可上探：8 + 8 = 16（需结合真实句长与业务 SLA 再压）
Attention = FLASH_ATTN（驱动 ≥ 580 / CUDA 13）
async_chunk = true
```

### 复现命令

```bash
# 启双卡
env -u HF_HOME -u HUGGINGFACE_HUB_CACHE bash tts_qwen/scripts/start_vllm_only.sh

# 压测
python tts_qwen/scripts/bench_vllm_speech.py \
  --urls http://127.0.0.1:8091,http://127.0.0.1:8092 \
  --dual --ladder 2,4,8,16 --stream
```

### 未纳入本次范围

- 业务 API 适配层（`:8300/:8301`）与学生端整条链路联调  
- 长句 / 多句分段队列优先级（P0 首段等）  
- 满 30 分钟稳定性 soak  

---

## 8. 关键文件

| 路径 | 说明 |
|---|---|
| `tts_qwen/deploy/qwen3_tts.yaml` | 生产 deploy（S1=10，高显存，async_chunk） |
| `tts_qwen/scripts/start_vllm_only.sh` | 仅起双卡 vLLM |
| `tts_qwen/scripts/start_vllm_gpu0.sh` / `gpu1.sh` | 默认 FLASH_ATTN |
| `tts_qwen/scripts/bench_vllm_speech.py` | 直打 Speech API 阶梯压测 |
| `tts_qwen/data/sample_flash_c1.wav` | FLASH 样音 |
| `tts_qwen/data/bench_flash_*.json` | 原始压测数据 |
| `ChatGPT分析的.md` | 部署依据文档 |
