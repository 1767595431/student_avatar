# P2 打断与状态机

## 目标

讲话中点击「停止回答」：

- 快速停止声音
- Avatar 回 idle（crossfade）
- 旧回答 PCM 不再恢复（generation 丢弃）

## 处理顺序

```text
interrupt
 → cancel_event.set()
 → Dify /chat-messages/{task_id}/stop
 → TTS WS type=cancel（合成中可并发接收）
 → Publisher bump_generation + clear_pcm + stop_speaking
 → state = idle
```

## 验收

```bash
bash /home/ubuntu/AI/student_avatar/apps/api/scripts/smoke_interrupt.sh
python /home/ubuntu/AI/student_avatar/apps/publisher/check_generation.py
```

页面：`http://<ip>:8000/` → 思考/讲话中可点「停止回答」；讲话中再按住说话会先打断。
