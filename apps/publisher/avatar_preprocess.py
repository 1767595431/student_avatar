#!/usr/bin/env python3
"""Avatar preprocess: mp4 -> Avatar Package (720p@25fps frames + manifest)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd)


def build_avatar_package(
    src_path: Path,
    *,
    avatar_id: str,
    version_id: str,
    out_root: Path,
    idle_frame: int = 0,
    talk_start: int = 21,
    talk_end: int = -1,
    transition_frames: int = 4,
    voice_id: str = "",
    name: str = "",
) -> dict[str, Any]:
    """Transcode + extract frames. Returns manifest dict. Raises on failure."""
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"source missing: {src}")

    out = Path(out_root) / avatar_id / version_id
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    normalized = out / "avatar.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=25",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(normalized),
        ]
    )

    cap = cv2.VideoCapture(str(normalized))
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        cv2.imwrite(str(frames_dir / f"frame_{idx:05d}.png"), frame)
        idx += 1
    cap.release()
    if idx == 0:
        raise RuntimeError("no frames decoded")

    end = talk_end if talk_end >= 0 else max(0, idx - 1)
    idle = min(max(0, idle_frame), idx - 1)
    start = min(max(0, talk_start), idx - 1)
    end = min(max(start, end), idx - 1)

    idle_img = cv2.imread(str(frames_dir / f"frame_{idle:05d}.png"))
    cv2.imwrite(str(out / "idle.png"), idle_img)

    manifest: dict[str, Any] = {
        "avatar_id": avatar_id,
        "version_id": version_id,
        "name": name or avatar_id,
        "voice_id": voice_id,
        "source": "avatar.mp4",
        "fps": 25,
        "width": 1280,
        "height": 720,
        "idle_frame": idle,
        "talk_start": start,
        "talk_end": end,
        "transition_frames": transition_frames,
        "frame_count": idx,
        "status": "ready",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def update_frames(
    pkg_dir: Path,
    *,
    idle_frame: int,
    talk_start: int,
    talk_end: int,
    transition_frames: int | None = None,
) -> dict[str, Any]:
    manifest_path = pkg_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    n = int(manifest.get("frame_count") or 1)
    idle = min(max(0, idle_frame), n - 1)
    start = min(max(0, talk_start), n - 1)
    end = min(max(start, talk_end), n - 1)
    manifest["idle_frame"] = idle
    manifest["talk_start"] = start
    manifest["talk_end"] = end
    if transition_frames is not None:
        manifest["transition_frames"] = transition_frames
    idle_png = pkg_dir / "frames" / f"frame_{idle:05d}.png"
    if idle_png.exists():
        img = cv2.imread(str(idle_png))
        if img is not None:
            cv2.imwrite(str(pkg_dir / "idle.png"), img)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--avatar-id", default="avatar_001")
    ap.add_argument("--version-id", default="avv_001")
    ap.add_argument("--out-root", default=str(Path(__file__).resolve().parents[2] / "data" / "avatars"))
    ap.add_argument("--idle-frame", type=int, default=0)
    ap.add_argument("--talk-start", type=int, default=21)
    ap.add_argument("--talk-end", type=int, default=-1)
    ap.add_argument("--transition-frames", type=int, default=4)
    ap.add_argument("--voice-id", default="")
    ap.add_argument("--name", default="")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"[warn] input missing ({src}), generating synthetic avatar.mp4")
        src.parent.mkdir(parents=True, exist_ok=True)
        run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                str(src),
            ]
        )

    try:
        manifest = build_avatar_package(
            src,
            avatar_id=args.avatar_id,
            version_id=args.version_id,
            out_root=Path(args.out_root),
            idle_frame=args.idle_frame,
            talk_start=args.talk_start,
            talk_end=args.talk_end,
            transition_frames=args.transition_frames,
            voice_id=args.voice_id,
            name=args.name,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"OK package -> {Path(args.out_root) / args.avatar_id / args.version_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
