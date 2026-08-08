#!/usr/bin/env python3
"""P3 load test: N sessions × media ensure × questions × random interrupt + GPU sample."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import statistics
import subprocess
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


def sample_gpu() -> list[dict]:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]
    rows = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        rows.append(
            {
                "index": int(parts[0]),
                "util": float(parts[1]),
                "mem_used_mb": float(parts[2]),
                "mem_total_mb": float(parts[3]),
                "ts": time.time(),
            }
        )
    return rows


async def gpu_sampler(path: Path, stop: asyncio.Event, interval: float = 2.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "index", "util", "mem_used_mb", "mem_total_mb"])
        w.writeheader()
        while not stop.is_set():
            for row in sample_gpu():
                if "error" in row:
                    continue
                w.writerow(row)
            f.flush()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass


async def create_session(client: httpx.AsyncClient, api: str, i: int) -> dict:
    r = await client.post(
        f"{api}/api/v1/sessions",
        json={
            "student_id": f"load_{i:03d}",
            "avatar_id": "avatar_001",
            "avatar_version_id": "avv_001",
        },
    )
    r.raise_for_status()
    return r.json()


async def ensure_media(client: httpx.AsyncClient, api: str, sid: str) -> dict:
    r = await client.post(f"{api}/api/v1/sessions/{sid}/media/ensure")
    r.raise_for_status()
    return r.json()


async def ask(client: httpx.AsyncClient, api: str, sid: str, wav: Path) -> dict:
    with wav.open("rb") as f:
        r = await client.post(
            f"{api}/api/v1/sessions/{sid}/questions",
            files={"audio": (wav.name, f, "audio/wav")},
        )
    r.raise_for_status()
    return r.json()


async def get_state(client: httpx.AsyncClient, api: str, sid: str) -> dict:
    r = await client.get(f"{api}/api/v1/sessions/{sid}")
    r.raise_for_status()
    return r.json()


async def interrupt(client: httpx.AsyncClient, api: str, sid: str) -> dict:
    r = await client.post(f"{api}/api/v1/sessions/{sid}/interrupt")
    r.raise_for_status()
    return r.json()


async def wait_speaking_or_idle(
    client: httpx.AsyncClient,
    api: str,
    sid: str,
    t_ask: float,
    timeout: float = 90.0,
) -> dict:
    deadline = time.time() + timeout
    first_speaking = None
    while time.time() < deadline:
        st = await get_state(client, api, sid)
        if st["state"] == "speaking" and first_speaking is None:
            first_speaking = time.time()
            return {
                "state": st["state"],
                "ttfa_proxy_ms": (first_speaking - t_ask) * 1000,
                "generation": st.get("generation"),
                "reached_speaking": True,
            }
        if st["state"] == "idle" and first_speaking is None and (time.time() - t_ask) > 2:
            # finished without observing speaking (very short) or still thinking→idle error
            return {
                "state": st["state"],
                "ttfa_proxy_ms": None,
                "generation": st.get("generation"),
                "reached_speaking": False,
            }
        await asyncio.sleep(0.25)
    st = await get_state(client, api, sid)
    return {
        "state": st["state"],
        "ttfa_proxy_ms": None,
        "generation": st.get("generation"),
        "reached_speaking": False,
        "timeout": True,
    }


async def session_loop(
    client: httpx.AsyncClient,
    api: str,
    sid: str,
    wav: Path,
    end_at: float,
    interrupt_prob: float,
    results: list,
    idx: int,
    ask_sem: asyncio.Semaphore,
) -> None:
    while time.time() < end_at:
        try:
            async with ask_sem:
                # 空闲回收后可能 CLOSED；提问前强制 ensure
                await ensure_media(client, api, sid)
                t_ask = time.time()
                q = await ask(client, api, sid, wav)
                asr_ms = float(q.get("processing_ms") or 0)
                spoke = await wait_speaking_or_idle(client, api, sid, t_ask, timeout=45.0)
                interrupted = False
                if spoke.get("reached_speaking") and random.random() < interrupt_prob:
                    await interrupt(client, api, sid)
                    interrupted = True
                    await asyncio.sleep(0.3)
                else:
                    for _ in range(20):
                        st = await get_state(client, api, sid)
                        if st["state"] == "idle":
                            break
                        await asyncio.sleep(0.4)
            ens = await ensure_media(client, api, sid)
            metrics = ens.get("publisher_metrics") or {}
            results.append(
                {
                    "session_i": idx,
                    "session_id": sid,
                    "asr_ms": asr_ms,
                    "ttfa_proxy_ms": spoke.get("ttfa_proxy_ms"),
                    "reached_speaking": spoke.get("reached_speaking"),
                    "interrupted": interrupted,
                    "video_track_recreate_count": metrics.get("video_track_recreate_count"),
                    "ok": True,
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"session_i": idx, "session_id": sid, "ok": False, "error": str(exc)})
            await asyncio.sleep(0.5)
        await asyncio.sleep(random.uniform(1.0, 2.5))


async def run(args: argparse.Namespace) -> dict:
    api = args.api.rstrip("/")
    wav = Path(args.wav)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stop = asyncio.Event()
    gpu_task = asyncio.create_task(gpu_sampler(out / "gpu.csv", stop, interval=args.gpu_interval))

    results: list[dict] = []
    sessions: list[dict] = []
    async with httpx.AsyncClient(timeout=180.0) as client:
        # create + ensure media (30 publishers)
        print(f"creating {args.sessions} sessions…")
        for i in range(args.sessions):
            s = await create_session(client, api, i)
            ens = await ensure_media(client, api, s["session_id"])
            sessions.append({"session": s, "ensure": ens})
            print(f"  [{i+1}/{args.sessions}] {s['session_id']} media={ens.get('media_state')}")

        end_at = time.time() + args.duration
        ask_sem = asyncio.Semaphore(args.max_inflight)
        print(
            f"load loop duration={args.duration}s interrupt_prob={args.interrupt_prob} max_inflight={args.max_inflight}"
        )
        loops = [
            asyncio.create_task(
                session_loop(
                    client,
                    api,
                    sessions[i]["session"]["session_id"],
                    wav,
                    end_at,
                    args.interrupt_prob,
                    results,
                    i,
                    ask_sem,
                )
            )
            for i in range(args.sessions)
        ]
        await asyncio.gather(*loops)

        # final publisher metrics
        finals = []
        for s in sessions:
            sid = s["session"]["session_id"]
            try:
                ens = await ensure_media(client, api, sid)
                st = await get_state(client, api, sid)
                finals.append({"session_id": sid, "state": st, "metrics": ens.get("publisher_metrics")})
            except Exception as exc:  # noqa: BLE001
                finals.append({"session_id": sid, "error": str(exc)})

    stop.set()
    await gpu_task

    ok = [r for r in results if r.get("ok")]
    asr = [r["asr_ms"] for r in ok if r.get("asr_ms") is not None]
    ttfa = [r["ttfa_proxy_ms"] for r in ok if r.get("ttfa_proxy_ms") is not None]
    recreates = [
        f.get("metrics", {}).get("video_track_recreate_count", 0)
        for f in finals
        if isinstance(f.get("metrics"), dict)
    ]
    summary = {
        "sessions": args.sessions,
        "duration_s": args.duration,
        "questions": len(results),
        "ok_questions": len(ok),
        "fail_questions": sum(1 for r in results if not r.get("ok")),
        "interrupted": sum(1 for r in ok if r.get("interrupted")),
        "asr_ms": {"p50": percentile(asr, 50), "p95": percentile(asr, 95), "mean": statistics.fmean(asr) if asr else 0},
        "ttfa_proxy_ms": {
            "p50": percentile(ttfa, 50),
            "p95": percentile(ttfa, 95),
            "mean": statistics.fmean(ttfa) if ttfa else 0,
            "n": len(ttfa),
        },
        "publishers_alive": sum(1 for f in finals if "error" not in f),
        "video_track_recreate_max": max(recreates) if recreates else None,
        "finals": finals,
        "samples": results,
    }
    (out / "load.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--sessions", type=int, default=30)
    ap.add_argument("--duration", type=int, default=180, help="seconds")
    ap.add_argument("--interrupt-prob", type=float, default=0.35)
    ap.add_argument("--max-inflight", type=int, default=4, help="max concurrent ask+TTS pipelines")
    ap.add_argument("--wav", default=str(ROOT / "asr/data/sample_zh.wav"))
    ap.add_argument("--gpu-interval", type=float, default=2.0)
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()
    if not args.out_dir:
        args.out_dir = str(
            ROOT / "data" / "bench" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )
    summary = asyncio.run(run(args))
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "sessions",
                    "duration_s",
                    "questions",
                    "ok_questions",
                    "fail_questions",
                    "interrupted",
                    "asr_ms",
                    "ttfa_proxy_ms",
                    "publishers_alive",
                    "video_track_recreate_max",
                )
            },
            indent=2,
        )
    )
    print("wrote", Path(args.out_dir) / "load.json")


if __name__ == "__main__":
    main()
