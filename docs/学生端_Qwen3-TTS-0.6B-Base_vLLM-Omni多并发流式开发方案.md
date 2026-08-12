# 学生端 Qwen3-TTS 0.6B Base + vLLM-Omni 多并发流式开发方案

> 日期：2026-08-09  
> 场景：30+ 学生在线，数字人问答，本地部署，中文，声音克隆，流式 TTS  
> GPU：2 × RTX 4090 48GB  
> TTS：Qwen/Qwen3-TTS-12Hz-0.6B-Base  
> Serving：vLLM-Omni  
> 输出：24 kHz mono PCM

## 1. 最终选型

固定使用：

```text
Qwen/Qwen3-TTS-12Hz-0.6B-Base
+
vLLM-Omni
```

选择 Base，是因为它就是声音克隆模型；官方列出的 0.6B Base 支持中文、多语言、流式和参考音频声音克隆。

生产上不用裸 `Qwen3TTSModel.generate_voice_clone()` 直接包 FastAPI，而是让 vLLM-Omni 负责多请求 Serving、流式输出和调度。

## 2. 总体架构

```text
Dify Streaming
      ↓
Text Chunker
      ↓
TTS Gateway / Scheduler
   ↙                 ↘
GPU0                 GPU1
Qwen3-TTS 0.6B       Qwen3-TTS 0.6B
Base + vLLM-Omni     Base + vLLM-Omni
   ↘                 ↙
      Streaming PCM
            ↓
   Student Audio Queue
            ↓
         WebRTC
```

原则：

```text
一张 GPU 一个 vLLM-Omni Server
```

不要把 0.6B 模型跨两张卡做 Tensor Parallel。两张卡分别服务不同学生请求，吞吐更合适。

## 3. 服务拆分

首版只需要：

```text
qwen-tts-gpu0
qwen-tts-gpu1
tts-gateway
avatar-voice-precompute
```

`tts-gateway` 使用 FastAPI + asyncio + WebSocket/httpx 即可。

首版单机无需 Kafka、RabbitMQ、Kubernetes。

## 4. 环境安装

当前 vLLM-Omni 最新文档使用 Linux + Python 3.12。

```bash
uv venv --python 3.12 --seed
source .venv/bin/activate

uv pip install vllm==0.26.0 --torch-backend=auto

git clone https://github.com/vllm-project/vllm-omni.git
cd vllm-omni
uv pip install -e .
```

开发验证通过后记录：

```bash
git rev-parse HEAD
```

生产 Docker 镜像固定：

```text
vLLM 版本
vLLM-Omni commit SHA
CUDA/Driver
PyTorch
模型 revision
```

不要生产环境永远跟 `main`。

## 5. 模型下载

模型：

```text
Qwen/Qwen3-TTS-12Hz-0.6B-Base
```

中国大陆可以：

```bash
pip install -U modelscope

modelscope download   --model Qwen/Qwen3-TTS-12Hz-0.6B-Base   --local_dir /models/Qwen3-TTS-12Hz-0.6B-Base
```

或者 Hugging Face：

```bash
huggingface-cli download   Qwen/Qwen3-TTS-12Hz-0.6B-Base   --local-dir /models/Qwen3-TTS-12Hz-0.6B-Base
```

## 6. 双 4090 启动

GPU0：

```bash
CUDA_VISIBLE_DEVICES=0 vllm serve Qwen/Qwen3-TTS-12Hz-0.6B-Base   --deploy-config vllm_omni/deploy/qwen3_tts.yaml   --omni   --host 0.0.0.0   --port 8091   --trust-remote-code   --enforce-eager
```

GPU1：

```bash
CUDA_VISIBLE_DEVICES=1 vllm serve Qwen/Qwen3-TTS-12Hz-0.6B-Base   --deploy-config vllm_omni/deploy/qwen3_tts.yaml   --omni   --host 0.0.0.0   --port 8092   --trust-remote-code   --enforce-eager
```

得到：

```text
GPU0: http://127.0.0.1:8091
GPU1: http://127.0.0.1:8092
```

## 7. 单请求先跑通

```bash
curl -X POST http://127.0.0.1:8091/v1/audio/speech   -H "Content-Type: application/json"   -d '{
    "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "input": "同学你好，我们现在开始回答这个问题。",
    "task_type": "Base",
    "language": "Chinese",
    "ref_audio": "https://your-server/voice/teacher.wav",
    "ref_text": "这里填写参考音频中实际说出的文字。",
    "response_format": "wav"
  }'   --output test.wav
```

先验证：

```text
中文
声音克隆
音质
稳定性
```

## 8. 生产不要每次传 ref_audio

每个 Avatar 资产：

```text
avatar_001/
├── avatar.mp4
├── voice.wav
└── voice.txt
```

发布 Avatar 时提前预计算声音 Profile，而不是每个学生请求都重新处理 `voice.wav`。

## 9. Avatar Voice 预计算

vLLM-Omni 提供 `precompute_custom_voice.py`。

本项目使用 0.6B Base：

```bash
python examples/online_serving/text_to_speech/qwen3_tts/precompute_custom_voice.py   --model Qwen/Qwen3-TTS-12Hz-0.6B-Base   --voice-name avatar_001_v1   --ref-audio /data/avatar/avatar_001/voice.wav   --ref-text "参考音频对应的准确文本"   --mode icl   --output-dir /data/qwen_custom_voices
```

生成：

```text
custom_voice_manifest.json
*.safetensors
```

建议 `--mode icl`，保存 speaker embedding + ref code。

两张 GPU Server 必须加载同一份：

```text
/data/qwen_custom_voices
```

之后请求只需：

```text
voice = avatar_001_v1
task_type = Base
```

## 10. Avatar Version Pinning

声音和视频统一版本：

```text
avatar_001_v1
avatar_001_v2
```

学生 Session 创建时固定：

```text
avatar_version_id = avatar_001_v1
voice = avatar_001_v1
```

后台发布 v2 后：

```text
已有 Session 继续 v1
新 Session 使用 v2
```

## 11. 推荐使用 WebSocket 流式路径

vLLM-Omni 提供：

```text
/v1/audio/speech/stream
```

WebSocket 支持增量输入文本。

协议核心：

```text
session.config
input.text
input.done
session.close
```

`input.done` 是 flush，不是断开连接，所以一个 Dify 回答可以保持同一个上游 TTS WebSocket。

Server 返回：

```text
audio.start
Binary PCM
audio.done
session.done
```

这条路径非常适合 Dify Streaming。

## 12. Dify → TTS

不要一个 token 就送一次 TTS。

使用 Text Chunker：

```text
Dify token
  ↓
文本缓冲
  ↓
遇到句号/问号/感叹号
或达到长度
  ↓
input.done
  ↓
Qwen3-TTS 生成 PCM
```

初始建议：

```text
soft_min_chars = 12
target_chars   = 24
hard_max_chars = 48
flush_wait_ms  = 180~250
```

优先断句：

```text
。！？；!?
```

没有强标点时，再按逗号或长度切。

## 13. TTS Gateway API

学生 Backend 对 Gateway 建议用：

```text
/ws/tts
```

开始：

```json
{
  "type": "start",
  "session_id": "s001",
  "question_id": "q001",
  "avatar_version_id": "avatar_001_v1",
  "voice": "avatar_001_v1",
  "language": "Chinese"
}
```

Dify 文本：

```json
{
  "type": "text",
  "text": "光合作用是植物利用光能"
}
```

结束：

```json
{
  "type": "done"
}
```

打断：

```json
{
  "type": "cancel"
}
```

Gateway 返回二进制 PCM 和：

```json
{"type":"tts_start"}
```

```json
{"type":"tts_done"}
```

## 14. GPU Scheduler

首版使用：

```text
least_active_sessions
```

例如：

```text
GPU0 active = 6
GPU1 active = 4
```

新问题：

```text
→ GPU1
```

关键要求：

```text
一个 question_id 从开始到结束固定同一个 GPU
```

也就是 Question Pinning。

不要每个句子重新选择 GPU，否则容易：

```text
乱序
额外握手
取消困难
状态难管理
```

## 15. Worker 状态

```python
WorkerState(
    id="gpu0",
    url="ws://127.0.0.1:8091",
    active_sessions=0,
    max_active_sessions=0,
    healthy=True,
)
```

最终 `max_active_sessions` 必须来自压测结果。

## 16. Queue

如果双 GPU 当前都达到稳定并发上限：

```text
新 TTS Job
↓
进入短 Queue
```

Queue 保存：

```text
session_id
question_id
voice
created_at
cancel_event
```

30+ 学生在线并不等于 30+ 同时 active TTS。

实际链路天然错峰：

```text
学生录音时间不同
ASR 时间不同
Dify 首 token 不同
Text Chunk flush 时间不同
```

因此：

```text
30+ Online Sessions
+
N Active TTS
+
Burst Queue
```

是正确容量模型。

## 17. Streaming PCM

Qwen3-TTS 在 vLLM-Omni 当前输出：

```text
24 kHz
mono
PCM
```

HTTP raw PCM：

```json
{
  "stream": true,
  "stream_format": "audio",
  "response_format": "pcm"
}
```

WebSocket 使用：

```text
stream_audio = true
```

PCM 一到：

```text
不要写磁盘
不要等完整 wav
```

直接：

```text
PCM
↓
Student Audio Queue
↓
WebRTC
```

如果你的 WebRTC Publisher 使用 48 kHz，则在 Publisher 前实时重采样：

```text
24k PCM → 48k PCM
```

## 18. 每学生独立 Audio Queue

每个 Media Session 独立：

```text
session_id
question_id
generation
audio_queue
cancel_event
tts_worker_id
tts_ws
```

禁止所有学生共享一个 audio queue。

## 19. TALKING 时机

正确：

```text
收到第一包可播放 PCM
↓
audio_ready
↓
Avatar IDLE → TRANSITION → TALKING
```

建议首播前保留：

```text
80~150ms
```

PCM 小缓冲。

## 20. 打断

学生重新开始录音：

```text
generation += 1
↓
cancel 当前 TTS
↓
关闭/取消上游 WS
↓
清空旧 audio_queue
↓
Avatar → IDLE
```

播放任何 PCM 前检查：

```text
packet.generation == current_generation
```

避免旧回答残音。

## 21. 并发配置

vLLM-Omni 当前 Qwen3-TTS 配置支持多请求，官方 `qwen3_tts.yaml` 已是 multi-request 配置，Stage 1 可以跨 in-flight requests 批处理 chunks。

官方当前文档提到 CustomVoice benchmark 中 Stage 1 `max_num_seqs=10` 表现较好。

注意：

```text
这是 CustomVoice benchmark
不是 RTX 4090 + 0.6B Base 的固定并发保证。
```

Base 必须自己压测。

第一阶段先跑官方默认 `qwen3_tts.yaml`。

确认功能无误后再测：

```text
max_num_seqs:
4
8
10
12
16
```

例如：

```bash
--stage-overrides '{"0":{"max_num_seqs":8},"1":{"max_num_seqs":8}}'
```

## 22. 双 4090 压测

先单卡：

```text
1
2
4
8
10
12
16
```

再双卡：

```text
8+8
10+10
12+12
16+16
```

真实文本长度：

```text
20字
50字
100字
200字
```

记录：

```text
queue_wait_ms
ttfa_ms
generation_ms
audio_duration_ms
rtf
gpu_id
error
cancelled
```

重点：

```text
P50/P95/P99 TTFA
RTF < 1
无 OOM
无持续断音
无学生串音
错误率稳定
```

最终确定：

```text
MAX_ACTIVE_TTS_PER_GPU
```

Scheduler 只允许：

```text
active < MAX_ACTIVE_TTS_PER_GPU
```

其余排队。

## 23. 官方性能数据如何理解

vLLM-Omni 团队曾公开 Qwen3-TTS 在 H100/H200 上的参考：

```text
Concurrency 1:
TTFP ~131 ms
RTF ~0.34

Concurrency 4:
TTFP median ~200 ms
RTF ~0.49
```

这说明 Qwen3-TTS + vLLM-Omni 确实在做多请求生产优化。

但不能据此推断：

```text
RTX 4090 一定多少路
```

你的 4090 必须实际压测。

## 24. 推荐目录

```text
student-system/
├── services/
│   ├── tts-gateway/
│   │   ├── app.py
│   │   ├── scheduler.py
│   │   ├── tts_session.py
│   │   ├── text_chunker.py
│   │   └── metrics.py
│   └── avatar-preprocess/
│       └── precompute_voice.py
├── deploy/
│   ├── qwen3_tts_gpu0.sh
│   ├── qwen3_tts_gpu1.sh
│   └── qwen3_tts.yaml
├── data/
│   ├── avatars/
│   └── qwen_custom_voices/
└── benchmark/
    └── tts_concurrency.py
```

## 25. 开发顺序

```text
1. 单卡 0.6B Base 中文声音克隆
2. PCM Streaming
3. Voice Precompute
4. Dify → TextChunker
5. WebSocket TTS
6. PCM → WebRTC
7. 第二张 GPU
8. least-active Scheduler
9. Question Pinning
10. Queue + Cancel
11. 双卡并发压测
12. 固化 MAX_ACTIVE_TTS_PER_GPU
```

## 26. 最终部署

```text
CPU
├── Student Backend
├── Dify Client
├── Text Chunker
├── TTS Gateway
├── Queue
└── WebRTC Control

RTX 4090 48GB #0
└── Qwen3-TTS-12Hz-0.6B-Base
    └── vLLM-Omni :8091

RTX 4090 48GB #1
└── Qwen3-TTS-12Hz-0.6B-Base
    └── vLLM-Omni :8092
```

最终链路：

```text
学生录音
  ↓
ASR
  ↓
Dify Streaming
  ↓
TextChunker
  ↓
TTS Gateway
  ↓
least-active + question pinning
  ↓
GPU0 / GPU1
  ↓
Qwen3-TTS 0.6B Base + vLLM-Omni
  ↓
Streaming PCM
  ↓
Student Audio Queue
  ↓
WebRTC
```

## 27. 官方项目与参考地址

Qwen3-TTS 官方 GitHub：

https://github.com/QwenLM/Qwen3-TTS

Qwen3-TTS 0.6B Base 模型：

https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base

vLLM-Omni 官方 GitHub：

https://github.com/vllm-project/vllm-omni

vLLM-Omni 官方文档：

https://docs.vllm.ai/projects/vllm-omni/en/latest/

vLLM-Omni Speech API：

https://docs.vllm.ai/projects/vllm-omni/en/latest/serving/speech_api/

Qwen3-TTS Online Serving：

https://docs.vllm.ai/projects/vllm-omni/en/latest/user_guide/examples/online_serving/text_to_speech/

vLLM-Omni 安装：

https://docs.vllm.ai/projects/vllm-omni/en/latest/getting_started/installation/

Qwen3-TTS Technical Report：

https://arxiv.org/abs/2601.15621

## 28. 当前资料注意事项

Qwen3-TTS 官方 GitHub README 的 `vLLM Usage` 段落仍保留较早的 “only offline inference is supported” 描述。

但 vLLM-Omni 当前 2026 年最新官方文档已经明确提供：

```text
Online Serving
/v1/audio/speech
/v1/audio/speech/stream
Base Voice Clone
PCM Streaming
WebSocket Streaming
Precomputed Voices
Multi-request config
```

所以本项目开发在线 Serving 时：

```text
以当前 vLLM-Omni 官方文档和源码为准
```

## 29. 最终结论

学生端 TTS 固定采用：

```text
Qwen3-TTS-12Hz-0.6B-Base
+
vLLM-Omni
+
双 RTX 4090
+
每卡一个独立 Serving
+
TTS Gateway
+
least-active
+
Question Pinning
+
Burst Queue
+
预计算 Avatar Voice
+
WebSocket 增量文本
+
Streaming PCM
```

这套设计的重点不是单纯“模型能跑”，而是让 30+ 学生共享一个真正可调度、可排队、可打断、可流式的双 GPU TTS 池。

生产并发上限必须通过真实双 4090 压测确定，不直接拿 `max_num_seqs` 当作并发承诺。
