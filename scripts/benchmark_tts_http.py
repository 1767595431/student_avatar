#!/usr/bin/env python3
"""Batch HTTP TTS concurrency ladder (non-stream)."""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TEXT = "欢迎大家来体验本地语音合成服务。这是一段用于压测并发吞吐的测试文本。"


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


async def one_job(client: httpx.AsyncClient, base: str, job_id: int, text: str) -> dict:
    t0 = time.perf_counter()
    err = None
    pcm_bytes = 0
    try:
        resp = await client.post(
            f"{base.rstrip('/')}/internal/tts/synthesize",
            json={"text": text, "voice_id": "avatar_voice_001"},
        )
        resp.raise_for_status()
        data = resp.json()
        pcm_bytes = len(base64.b64decode(data.get("pcm_base64") or ""))
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
    wall_s = time.perf_counter() - t0
    audio_s = pcm_bytes / 2 / 24000 if pcm_bytes else 0.0
    rtf = (wall_s / audio_s) if audio_s > 0 else None
    return {
        "ok": err is None and pcm_bytes > 0,
        "base": base,
        "wall_s": wall_s,
        "audio_s": audio_s,
        "rtf": rtf,
        "pcm_bytes": pcm_bytes,
        "error": err,
    }


async def run_level(bases: list[str], concurrency: int, text: str) -> dict:
    async with httpx.AsyncClient(timeout=180.0) as client:
        tasks = [
            one_job(client, bases[i % len(bases)], i, text) for i in range(concurrency)
        ]
        t0 = time.perf_counter()
        results = await asyncio.gather(*tasks)
        batch_s = time.perf_counter() - t0
    ok = [r for r in results if r["ok"]]
    walls = [r["wall_s"] for r in ok]
    rtf = [r["rtf"] for r in ok if r["rtf"] is not None]
    stable = len(ok) == concurrency and (statistics.fmean(rtf) if rtf else 99) <= 1.5
    return {
        "concurrency": concurrency,
        "ok": len(ok),
        "fail": concurrency - len(ok),
        "batch_s": batch_s,
        "wall_s": {"p50": percentile(walls, 50), "p95": percentile(walls, 95)},
        "rtf": {
            "p50": percentile(rtf, 50),
            "p95": percentile(rtf, 95),
            "mean": statistics.fmean(rtf) if rtf else None,
        },
        "stable": stable,
        "samples": results,
    }


async def run(args: argparse.Namespace) -> dict:
    bases = [u.strip() for u in args.tts_http.split(",") if u.strip()]
    levels = []
    max_stable = 0
    for c in args.ladder:
        print(f"=== TTS HTTP concurrency={c} ===")
        level = await run_level(bases, c, args.text)
        levels.append(level)
        print(
            json.dumps(
                {k: level[k] for k in ("concurrency", "ok", "fail", "wall_s", "rtf", "stable", "batch_s")},
                indent=2,
            )
        )
        if level["stable"]:
            max_stable = c
        else:
            break
    return {
        "bases": bases,
        "levels": levels,
        "max_tts_active_jobs_suggested": max_stable or 1,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--tts-http",
        default=",".join(f"http://127.0.0.1:{p}" for p in range(8200, 8206)),
    )
    ap.add_argument("--text", default=TEXT)
    ap.add_argument("--ladder", default="1,2,4,6")
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()
    args.ladder = [int(x) for x in args.ladder.split(",") if x.strip()]
    out = Path(args.out_dir) if args.out_dir else ROOT / "data" / "bench" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    summary = asyncio.run(run(args))
    (out / "tts_http.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("suggested MAX_TTS_ACTIVE_JOBS=", summary["max_tts_active_jobs_suggested"])
    print("wrote", out / "tts_http.json")


if __name__ == "__main__":
    main()
