#!/usr/bin/env python3
"""直打 vLLM-Omni /v1/audio/speech 阶梯压测（绕过业务适配层）。

用法：
  python tts_qwen/scripts/bench_vllm_speech.py
  python tts_qwen/scripts/bench_vllm_speech.py --ladder 1,2,4,6,8,10 --dual
"""
from __future__ import annotations

import argparse
import base64
import json
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VOICE = ROOT / "data" / "voices" / "voice_aacd9fa8"
SHORT = "今天天气不错，我们开始上课。"  # ~14 字
MED = "人工智能正在改变课堂互动方式，请同学们认真听讲并积极提问。"  # ~28 字


def gpu_snap() -> list[dict]:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            rows.append(
                {
                    "gpu": int(parts[0]),
                    "mem_used_mib": int(parts[1]),
                    "mem_total_mib": int(parts[2]),
                    "util_pct": int(parts[3]),
                }
            )
    return rows


def discover_model(base: str) -> str:
    r = httpx.get(f"{base}/v1/models", timeout=10.0)
    r.raise_for_status()
    data = r.json().get("data") or []
    if not data or not data[0].get("id"):
        raise RuntimeError(f"no model at {base}")
    return str(data[0]["id"])


def one_req(
    *,
    base: str,
    model: str,
    text: str,
    ref_b64: str,
    ref_text: str,
    stream: bool,
) -> dict:
    payload = {
        "model": model,
        "input": text,
        "task_type": "Base",
        "language": "Chinese",
        "ref_audio": f"data:audio/wav;base64,{ref_b64}",
        "ref_text": ref_text,
        "stream": stream,
        "response_format": "pcm",
    }
    if stream:
        payload["stream_format"] = "audio"
    t0 = time.perf_counter()
    ttfa = None
    nbytes = 0
    err = None
    try:
        with httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            with client.stream("POST", f"{base}/v1/audio/speech", json=payload) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(f"HTTP {resp.status_code}: {body}")
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    if ttfa is None:
                        ttfa = time.perf_counter() - t0
                    nbytes += len(chunk)
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
    wall = time.perf_counter() - t0
    # pcm s16le mono 24k
    audio_s = (nbytes / 2) / 24000.0 if nbytes else 0.0
    return {
        "ok": err is None and nbytes > 0,
        "err": err,
        "ttfa_s": ttfa,
        "wall_s": wall,
        "bytes": nbytes,
        "audio_s": audio_s,
        "rtf": (wall / audio_s) if audio_s > 0 else None,
        "base": base,
    }


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    k = min(len(ys) - 1, max(0, int(round((p / 100.0) * (len(ys) - 1)))))
    return ys[k]


def run_wave(bases: list[str], models: dict[str, str], n: int, text: str, ref_b64: str, ref_text: str, stream: bool) -> dict:
    # round-robin across bases
    jobs = []
    for i in range(n):
        base = bases[i % len(bases)]
        jobs.append((base, models[base]))

    results: list[dict] = []
    lock = threading.Lock()
    gpu_peak = {"rows": gpu_snap()}

    def _watch() -> None:
        while True:
            time.sleep(0.5)
            with lock:
                if gpu_peak.get("done"):
                    break
                cur = gpu_snap()
                prev = gpu_peak["rows"]
                if not prev:
                    gpu_peak["rows"] = cur
                    continue
                merged = []
                for a, b in zip(prev, cur):
                    merged.append(
                        {
                            **a,
                            "mem_used_mib": max(a["mem_used_mib"], b["mem_used_mib"]),
                            "util_pct": max(a["util_pct"], b["util_pct"]),
                        }
                    )
                gpu_peak["rows"] = merged

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n) as pool:
        futs = [
            pool.submit(
                one_req,
                base=b,
                model=m,
                text=text,
                ref_b64=ref_b64,
                ref_text=ref_text,
                stream=stream,
            )
            for b, m in jobs
        ]
        for f in as_completed(futs):
            results.append(f.result())
    wall_batch = time.perf_counter() - t0
    with lock:
        gpu_peak["done"] = True
    watcher.join(timeout=2)

    oks = [r for r in results if r["ok"]]
    ttfa = [r["ttfa_s"] for r in oks if r["ttfa_s"] is not None]
    walls = [r["wall_s"] for r in oks]
    rtfs = [r["rtf"] for r in oks if r["rtf"] is not None]
    audio = [r["audio_s"] for r in oks]
    return {
        "concurrency": n,
        "bases": bases,
        "ok": len(oks),
        "fail": len(results) - len(oks),
        "batch_wall_s": round(wall_batch, 3),
        "ttfa_p50": round(pct(ttfa, 50) or 0, 3) if ttfa else None,
        "ttfa_p95": round(pct(ttfa, 95) or 0, 3) if ttfa else None,
        "wall_p50": round(pct(walls, 50) or 0, 3) if walls else None,
        "wall_p95": round(pct(walls, 95) or 0, 3) if walls else None,
        "rtf_mean": round(statistics.mean(rtfs), 3) if rtfs else None,
        "audio_s_mean": round(statistics.mean(audio), 3) if audio else None,
        "errors": [r["err"] for r in results if r["err"]][:3],
        "gpu_peak": gpu_peak["rows"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", default="http://127.0.0.1:8091,http://127.0.0.1:8092")
    ap.add_argument("--ladder", default="1,2,4,6,8,10")
    ap.add_argument("--dual", action="store_true", help="阶梯并发均分到双卡（否则只打第一张）")
    ap.add_argument("--text", default="", help="覆盖合成文本；空则用短句")
    ap.add_argument("--long", action="store_true", help="用中等长度句子")
    ap.add_argument("--voice-dir", default=str(DEFAULT_VOICE))
    ap.add_argument("--stream", action="store_true", help="stream=true（默认 false 整段）")
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    all_bases = [u.strip().rstrip("/") for u in args.urls.split(",") if u.strip()]
    bases = all_bases if args.dual else all_bases[:1]
    voice_dir = Path(args.voice_dir)
    wav = (voice_dir / "prompt.wav").read_bytes()
    assert wav[:4] == b"RIFF", "prompt.wav must be RIFF WAV"
    meta = json.loads((voice_dir / "meta.json").read_text(encoding="utf-8"))
    ref_text = (meta.get("prompt_text") or "").strip()
    assert ref_text, "prompt_text required"
    ref_b64 = base64.b64encode(wav).decode("ascii")
    text = args.text or (MED if args.long else SHORT)

    models = {b: discover_model(b) for b in bases}
    print(json.dumps({"bases": bases, "models": models, "text": text, "stream": args.stream}, ensure_ascii=False))

    # warmup
    for b in bases:
        for _ in range(max(0, args.warmup)):
            r = one_req(base=b, model=models[b], text=text, ref_b64=ref_b64, ref_text=ref_text, stream=args.stream)
            print("warmup", b, "ok" if r["ok"] else r["err"], f"ttfa={r['ttfa_s']} wall={r['wall_s']:.2f}")

    ladder = [int(x) for x in args.ladder.split(",") if x.strip()]
    rows = []
    for n in ladder:
        print(f"\n=== concurrency={n} bases={len(bases)} ===", flush=True)
        row = run_wave(bases, models, n, text, ref_b64, ref_text, args.stream)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    summary = {"text": text, "stream": args.stream, "dual": args.dual, "rows": rows}
    out = Path(args.out) if args.out else ROOT / "tts_qwen" / "data" / f"bench_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nWrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
