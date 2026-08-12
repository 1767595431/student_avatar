#!/usr/bin/env python3
"""TTS concurrency ladder → TTFA / RTF → suggest MAX_TTS_ACTIVE_JOBS."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
TEXT = "欢迎大家来体验本地语音合成服务。这是一段用于压测首包时延的测试文本。"


def percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return float(s[f] + (s[c] - s[f]) * (k - f))


async def one_job(uri: str, job_id: int, text: str, sample_rate: int = 24000) -> dict:
    t0 = time.perf_counter()
    ttfa_ms = None
    pcm_bytes = 0
    err = None
    try:
        async with websockets.connect(uri, max_size=20_000_000) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "start",
                        "request_id": f"bench_{job_id}",
                        "session_id": f"tts_bench_{job_id}",
                        "question_id": f"tq_{job_id}",
                        "voice_id": "avatar_voice_001",
                        "sample_rate": sample_rate,
                    }
                )
            )
            ready = json.loads(await ws.recv())
            if ready.get("type") != "ready":
                raise RuntimeError(f"bad ready: {ready}")
            await ws.send(json.dumps({"type": "text", "text": text}))
            await ws.send(json.dumps({"type": "finish"}))
            while True:
                msg = await ws.recv()
                if isinstance(msg, bytes):
                    if ttfa_ms is None:
                        ttfa_ms = (time.perf_counter() - t0) * 1000
                    pcm_bytes += len(msg)
                    continue
                data = json.loads(msg)
                t = data.get("type")
                if t in ("audio_end", "cancelled"):
                    break
                if t == "error":
                    raise RuntimeError(data.get("message") or "tts error")
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
    wall_s = time.perf_counter() - t0
    audio_s = pcm_bytes / 2 / sample_rate if pcm_bytes else 0.0
    # synthesis RTF after first audio (excludes TTFA queue/warmup)
    synth_s = max(0.0, wall_s - (ttfa_ms or 0) / 1000.0)
    rtf = (synth_s / audio_s) if audio_s > 0 else None
    rtf_wall = (wall_s / audio_s) if audio_s > 0 else None
    return {
        "ok": err is None and ttfa_ms is not None,
        "uri": uri,
        "ttfa_ms": ttfa_ms,
        "wall_s": wall_s,
        "audio_s": audio_s,
        "rtf": rtf,
        "rtf_wall": rtf_wall,
        "pcm_bytes": pcm_bytes,
        "error": err,
    }


async def run_level(uris: list[str], concurrency: int, text: str) -> dict:
    tasks = []
    for i in range(concurrency):
        uri = uris[i % len(uris)]
        tasks.append(one_job(uri, i, text))
    t0 = time.perf_counter()
    results = await asyncio.gather(*tasks)
    batch_s = time.perf_counter() - t0
    ok = [r for r in results if r["ok"]]
    ttfa = [r["ttfa_ms"] for r in ok if r["ttfa_ms"] is not None]
    rtf = [r["rtf"] for r in ok if r["rtf"] is not None]
    # stable: all ok, synth RTF mean <= 1.35, TTFA p95 < 30s
    stable = (
        len(ok) == concurrency
        and (statistics.fmean(rtf) if rtf else 99) <= 1.35
        and percentile(ttfa, 95) < 30000
    )
    return {
        "concurrency": concurrency,
        "ok": len(ok),
        "fail": concurrency - len(ok),
        "batch_s": batch_s,
        "ttfa_ms": {"p50": percentile(ttfa, 50), "p95": percentile(ttfa, 95)},
        "rtf": {"p50": percentile(rtf, 50), "p95": percentile(rtf, 95), "mean": statistics.fmean(rtf) if rtf else None},
        "stable": stable,
        "samples": results,
    }


async def run(args: argparse.Namespace) -> dict:
    uris = [u.strip() for u in args.tts_urls.split(",") if u.strip()]
    levels = []
    max_stable = 0
    for c in args.ladder:
        print(f"=== TTS concurrency={c} ===")
        level = await run_level(uris, c, args.text)
        levels.append(level)
        print(
            json.dumps(
                {k: level[k] for k in ("concurrency", "ok", "fail", "ttfa_ms", "rtf", "stable")},
                indent=2,
            )
        )
        if level["stable"]:
            max_stable = c
        else:
            break
    return {
        "uris": uris,
        "text": args.text,
        "levels": levels,
        "max_tts_active_jobs_suggested": max_stable or 1,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--tts-urls",
        default="ws://127.0.0.1:8300/internal/tts/stream,ws://127.0.0.1:8301/internal/tts/stream",
    )
    ap.add_argument("--text", default=TEXT)
    ap.add_argument("--ladder", default="1,2,4", help="comma concurrency steps")
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()
    args.ladder = [int(x) for x in args.ladder.split(",") if x.strip()]
    out = Path(args.out_dir) if args.out_dir else ROOT / "data" / "bench" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    summary = asyncio.run(run(args))
    (out / "tts.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("suggested MAX_TTS_ACTIVE_JOBS=", summary["max_tts_active_jobs_suggested"])
    print(f"wrote {out / 'tts.json'}")


if __name__ == "__main__":
    main()
