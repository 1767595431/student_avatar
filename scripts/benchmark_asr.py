#!/usr/bin/env python3
"""ASR burst benchmark → data/bench/<ts>/asr.json"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


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


async def one(client: httpx.AsyncClient, url: str, wav: Path, i: int) -> dict:
    t0 = time.perf_counter()
    with wav.open("rb") as f:
        files = {"audio": (wav.name, f, "audio/wav")}
        data = {"session_id": f"bench_{i}", "question_id": f"bq_{i}", "language": "zh"}
        resp = await client.post(url, data=data, files=files)
    ms = (time.perf_counter() - t0) * 1000
    ok = resp.status_code == 200
    body = resp.json() if ok else {"error": resp.text[:200]}
    return {
        "ok": ok,
        "wall_ms": ms,
        "processing_ms": body.get("processing_ms"),
        "text": (body.get("text") or "")[:80],
    }


async def run(args: argparse.Namespace) -> dict:
    url = f"{args.asr_url.rstrip('/')}/internal/asr/transcribe"
    wav = Path(args.wav)
    results: list[dict] = []
    sem = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(timeout=120.0) as client:

        async def wrapped(i: int) -> None:
            async with sem:
                results.append(await one(client, url, wav, i))

        await asyncio.gather(*(wrapped(i) for i in range(args.n)))

    walls = [r["wall_ms"] for r in results if r["ok"]]
    procs = [float(r["processing_ms"]) for r in results if r["ok"] and r.get("processing_ms") is not None]
    summary = {
        "n": args.n,
        "concurrency": args.concurrency,
        "ok": sum(1 for r in results if r["ok"]),
        "fail": sum(1 for r in results if not r["ok"]),
        "wall_ms": {
            "p50": percentile(walls, 50),
            "p95": percentile(walls, 95),
            "mean": statistics.fmean(walls) if walls else 0,
        },
        "processing_ms": {
            "p50": percentile(procs, 50),
            "p95": percentile(procs, 95),
            "mean": statistics.fmean(procs) if procs else 0,
        },
        "samples": results,
    }
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asr-url", default="http://127.0.0.1:8100")
    ap.add_argument("--wav", default=str(ROOT / "asr/data/sample_zh.wav"))
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--concurrency", type=int, default=30)
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()
    out = Path(args.out_dir) if args.out_dir else ROOT / "data" / "bench" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    summary = asyncio.run(run(args))
    (out / "asr.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("n", "concurrency", "ok", "fail", "wall_ms", "processing_ms")}, indent=2))
    print(f"wrote {out / 'asr.json'}")


if __name__ == "__main__":
    main()
