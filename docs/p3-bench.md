# P3 压测报告

> 数据目录：`data/bench/20260808T191246Z_short/`  
> 说明：按需求改为**短测**（未跑满 30 分钟）。30 路 WebRTC 在线与 TTS 稳定并发已区分测量。

## 环境

| 组件 | 配置 |
|---|---|
| ASR | GPU0 `:8100` |
| TTS Worker0 | GPU0 `:8200` |
| TTS Worker1 | GPU1 `:8201` |
| Business API | 最少连接调度（least in-flight + RR） |
| LiveKit | Docker `student-livekit` |
| `MAX_TTS_ACTIVE_JOBS` | **2**（双 Worker，进程内 CosyVoice 串行） |

## 1. ASR burst

命令：`benchmark_asr.py --n 20 --concurrency 10`

| 指标 | 值 |
|---|---|
| 成功/失败 | 20 / 0 |
| wall P50 / P95 | 2874 ms / 4190 ms |
| processing P50 / P95 | 2397 ms / 3150 ms |

相对工程目标（P95 \< 800 ms）偏高：单卡 FunASR + 与 TTS0 共享 GPU0，burst 排队明显。

## 2. TTS 并发阶梯

命令：`benchmark_tts.py --ladder 1,2`（已修复 `wall_s` NameError）

| 并发 | ok | TTFA P95 | synth RTF mean | stable |
|---|---|---|---|---|
| 1 | 1 | 15.8 s | 0.21 | yes |
| 2 | 2 | 13.4 s | 0.18 | yes |

建议：

```text
MAX_TTS_ACTIVE_JOBS = 2
```

**30 路在线 ≠ 30 路同时说话**。双 4090 当前稳定实时 speaking 并发为 **2**（一路一 Worker）。TTFA 含 CosyVoice 首包冷启动，后续句级 RTF \< 1。

## 3. 多路会话短测

命令：`load_test.py --sessions 10 --duration 60 --max-inflight 2`

（先前 30×120 曾因 `MEDIA_IDLE_TIMEOUT_S=90` 在建连阶段回收 Publisher；已将超时调到 600s，并在每次提问前 `ensure`。）

| 指标 | 值 |
|---|---|
| Session / 时长 | 10 / 60 s |
| 提问成功/失败 | 14 / 0 |
| 随机打断 | 6 |
| Publisher 存活 | 10 / 10 |
| `video_track_recreate_max` | **0** |
| ASR（链路内）P50 / P95 | 157 ms / 345 ms |
| 整体首响代理 ask→speaking P50 / P95 | 16.3 s / 21.3 s |
| GPU0 util/mem avg | 33.5% / 7932 MiB |
| GPU1 util/mem avg | 33.3% / 5353 MiB |

## 4. 结论

1. **WebRTC 多路**：Publisher + Avatar frames 共享可支撑多 Session 在线。  
2. **TTS 稳定实时并发**：**2**（写入 `MAX_TTS_ACTIVE_JOBS=2`）。  
3. **ASR P95**：burst 下约 **3.2 s（processing）/ 4.2 s（wall）**，未达 \<800 ms 目标。  
4. **TTS TTFA P95**：约 **13–16 s**（首包），未达 \<600 ms 目标；合成段 RTF 健康（~0.2）。  
5. 满 30 分钟稳定性验收可后续按同一脚本 `--sessions 30 --duration 1800` 补跑。

## 5. 首响优化（已落地）

改动：

- TTS 启动 `load` 后执行短文本 `warmup()`（预热约 28s，一次性）
- Chunker 更早吐首句（首句 6 字、超时 flush 120ms）

对比（同机短测）：

| 指标 | 优化前 | 优化后 |
|---|---|---|
| TTS TTFA P95（纯合成） | ~15.8 s | **~2.5 s** |
| 整体 ask→speaking P95 | ~21.3 s | **~4.9 s** |
| synth RTF | ~0.2 | ~0.15–0.18 |

数据：`data/bench/20260808T194736Z_opt/`

## 6. 非流式 + 提并发（已落地）

策略：ASR/TTS **不做流式**；TTS 改 **HTTP 整段合成**；双卡各 **2 个 Worker**（共 4 路），串行启动避免预热 OOM。

| 项 | 值 |
|---|---|
| Worker | `:8200-:8203`（GPU0×2 + GPU1×2） |
| `MAX_TTS_ACTIVE_JOBS` | **4** |
| 并发 1/2/4 HTTP TTS | 全 stable，RTF mean ≈ 0.41 / 0.54 / 0.58 |
| 说明 | 3/GPU 并行预热会 CUDA OOM，故定为 2/GPU |

启动：

```bash
bash tts/scripts/stop_workers.sh
bash tts/scripts/start_workers.sh   # TTS_WORKERS_PER_GPU=2
```

数据：`data/bench/20260808T200420Z_conc4/`

```bash
bash tts/scripts/start_gpu0.sh   # :8200
bash tts/scripts/start_gpu1.sh   # :8201
bash asr/scripts/start.sh
bash apps/api/scripts/start.sh

python scripts/benchmark_asr.py --n 20 --concurrency 10 --out-dir data/bench/run1
python scripts/benchmark_tts.py --ladder 1,2 --out-dir data/bench/run1
python scripts/load_test.py --sessions 10 --duration 60 --max-inflight 2 --out-dir data/bench/run1
```
