#!/usr/bin/env python3
"""Avatar preprocess: idle+talk mp4 → Avatar Package (原分辨率≤1080p @25fps)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2

# 旧单视频包：待机预览片头秒数（双视频包不用）
IDLE_LOOP_SECONDS = 1.0
# 1080p 上限：短边≤1080、长边≤1920；未超则保持原分辨率
MAX_1080P_SHORT = 1080
MAX_1080P_LONG = 1920
X264_QUALITY = ["-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p"]


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd)


def _probe_wh(src: Path) -> tuple[int, int]:
    cap = cv2.VideoCapture(str(src))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    if w <= 0 or h <= 0:
        raise RuntimeError(f"cannot probe size: {src}")
    return w, h


def _even(n: int) -> int:
    return max(2, int(n) - (int(n) % 2))


def target_canvas(src_w: int, src_h: int) -> tuple[int, int]:
    """保持原分辨率；仅当超出 1080p 盒（短≤1080、长≤1920）时等比缩小。yuv420 要偶数边。"""
    long_side = max(src_w, src_h)
    short_side = min(src_w, src_h)
    scale = 1.0
    if long_side > MAX_1080P_LONG or short_side > MAX_1080P_SHORT:
        scale = min(MAX_1080P_LONG / long_side, MAX_1080P_SHORT / short_side)
    return _even(round(src_w * scale)), _even(round(src_h * scale))


def _normalize_video(src: Path, dst: Path) -> tuple[int, int]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    sw, sh = _probe_wh(src)
    tw, th = target_canvas(sw, sh)
    # 不 pad：画布即目标分辨率，避免竖屏被塞进横屏黑边
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            f"scale={tw}:{th},fps=25",
            "-an",
            *X264_QUALITY,
            str(dst),
        ]
    )
    return tw, th


def _extract_png_frames(video: Path, frames_dir: Path) -> int:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("frame_*.png"):
        old.unlink()
    cap = cv2.VideoCapture(str(video))
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        cv2.imwrite(str(frames_dir / f"frame_{idx:05d}.png"), frame)
        idx += 1
    cap.release()
    if idx == 0:
        raise RuntimeError(f"no frames decoded: {video}")
    return idx


def make_pingpong_mp4(src: Path, dst: Path) -> None:
    """正放 + 倒放拼成循环片（浏览器 <video loop> 即可正放倒放）。"""
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-filter_complex",
            "[0:v]split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1:a=0",
            "-an",
            *X264_QUALITY,
            str(dst),
        ]
    )


def build_avatar_package_dual(
    idle_src: Path,
    talk_src: Path,
    *,
    avatar_id: str,
    version_id: str,
    out_root: Path,
    transition_frames: int = 4,
    voice_id: str = "",
    name: str = "",
) -> dict[str, Any]:
    """双视频：待机片（正放倒放）+ 动嘴片。"""
    idle_src = Path(idle_src)
    talk_src = Path(talk_src)
    if not idle_src.exists():
        raise FileNotFoundError(f"idle video missing: {idle_src}")
    if not talk_src.exists():
        raise FileNotFoundError(f"talk video missing: {talk_src}")

    out = Path(out_root) / avatar_id / version_id
    out.mkdir(parents=True, exist_ok=True)
    idle_norm = out / "idle_source.mp4"
    talk_norm = out / "talk.mp4"
    tw, th = _normalize_video(idle_src, idle_norm)
    _normalize_video(talk_src, talk_norm)

    idle_frames_dir = out / "idle_frames"
    talk_frames_dir = out / "frames"
    idle_n = _extract_png_frames(idle_norm, idle_frames_dir)
    talk_n = _extract_png_frames(talk_norm, talk_frames_dir)

    idle0 = cv2.imread(str(idle_frames_dir / "frame_00000.png"))
    if idle0 is None:
        raise RuntimeError("idle frame0 missing")
    cv2.imwrite(str(out / "idle.png"), idle0)
    th_px, tw_px = idle0.shape[:2]
    if (tw_px, th_px) != (tw, th):
        tw, th = tw_px, th_px

    # 学生端预览：已含正放+倒放，loop 即正放倒放循环
    make_pingpong_mp4(idle_norm, out / "idle.mp4")

    manifest: dict[str, Any] = {
        "avatar_id": avatar_id,
        "version_id": version_id,
        "name": name or avatar_id,
        "voice_id": voice_id,
        "mode": "dual",
        "source": "talk.mp4",
        "idle_source": "idle_source.mp4",
        "talk_video": "talk.mp4",
        "idle_video": "idle.mp4",
        "idle_pingpong": True,
        "fps": 25,
        "width": tw,
        "height": th,
        "idle_frame": 0,
        "idle_frame_count": idle_n,
        "talk_start": 0,
        "talk_end": max(0, talk_n - 1),
        "transition_frames": transition_frames,
        "frame_count": talk_n,
        "status": "ready",
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


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
    """旧单视频方案（兼容 CLI / 旧包）。"""
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"source missing: {src}")

    out = Path(out_root) / avatar_id / version_id
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    normalized = out / "avatar.mp4"
    tw, th = _normalize_video(src, normalized)
    idx = _extract_png_frames(normalized, frames_dir)

    end = talk_end if talk_end >= 0 else max(0, idx - 1)
    idle = min(max(0, idle_frame), idx - 1)
    start = min(max(0, talk_start), idx - 1)
    end = min(max(start, end), idx - 1)

    idle_img = cv2.imread(str(frames_dir / f"frame_{idle:05d}.png"))
    cv2.imwrite(str(out / "idle.png"), idle_img)
    if idle_img is not None:
        th, tw = idle_img.shape[:2]
    write_idle_mp4(out, frames_dir, fps=25, frame_count=idx, loop_seconds=IDLE_LOOP_SECONDS)
    # 旧包待机预览也做正放倒放
    fwd = out / "idle.mp4"
    ping = out / "idle_ping.mp4"
    make_pingpong_mp4(fwd, ping)
    ping.replace(fwd)

    manifest: dict[str, Any] = {
        "avatar_id": avatar_id,
        "version_id": version_id,
        "name": name or avatar_id,
        "voice_id": voice_id,
        "mode": "single",
        "source": "avatar.mp4",
        "idle_video": "idle.mp4",
        "idle_pingpong": True,
        "idle_loop_seconds": IDLE_LOOP_SECONDS,
        "fps": 25,
        "width": tw,
        "height": th,
        "idle_frame": idle,
        "talk_start": start,
        "talk_end": end,
        "transition_frames": transition_frames,
        "frame_count": idx,
        "status": "ready",
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def write_idle_mp4(
    pkg_dir: Path,
    frames_dir: Path,
    *,
    fps: int = 25,
    frame_count: int | None = None,
    loop_seconds: float = IDLE_LOOP_SECONDS,
) -> Path:
    """Encode muted idle.mp4 from the first `loop_seconds` of frames (single-video legacy)."""
    pkg_dir = Path(pkg_dir)
    frames_dir = Path(frames_dir)
    out = pkg_dir / "idle.mp4"
    fps = max(1, int(fps or 25))
    if frame_count is None:
        frame_count = len(sorted(frames_dir.glob("frame_*.png")))
    frame_count = max(1, int(frame_count))
    n = max(1, min(frame_count, int(round(fps * float(loop_seconds)))))
    frame0 = frames_dir / "frame_00000.png"
    if not frame0.exists():
        found = sorted(frames_dir.glob("frame_*.png"))
        if not found:
            raise FileNotFoundError(f"no frames in {frames_dir}")
        frame0 = found[0]
        n = 1
    if n <= 1:
        run(
            [
                "ffmpeg", "-y",
                "-loop", "1", "-i", str(frame0),
                "-t", str(max(loop_seconds, 1.0)), "-r", str(fps),
                "-an", *X264_QUALITY,
                str(out),
            ]
        )
    else:
        run(
            [
                "ffmpeg", "-y",
                "-framerate", str(fps),
                "-start_number", "0",
                "-i", str(frames_dir / "frame_%05d.png"),
                "-frames:v", str(n),
                "-an", *X264_QUALITY,
                str(out),
            ]
        )
    return out


def ensure_idle_mp4(pkg_dir: Path, *, force: bool = False) -> Path:
    """Ensure browser idle.mp4 exists (dual: ping-pong of idle_source; single: 1s ping-pong)."""
    pkg_dir = Path(pkg_dir)
    out = pkg_dir / "idle.mp4"
    man_path = pkg_dir / "manifest.json"
    if not man_path.exists():
        raise FileNotFoundError("manifest missing")
    man = json.loads(man_path.read_text(encoding="utf-8"))
    mode = man.get("mode") or "single"
    if not force and out.exists() and out.stat().st_size > 0 and man.get("idle_pingpong"):
        return out

    if mode == "dual":
        src = pkg_dir / (man.get("idle_source") or "idle_source.mp4")
        if not src.exists():
            raise FileNotFoundError("idle_source.mp4 missing")
        make_pingpong_mp4(src, out)
        man["idle_video"] = "idle.mp4"
        man["idle_pingpong"] = True
        man_path.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return out

    frames_dir = pkg_dir / "frames"
    write_idle_mp4(
        pkg_dir,
        frames_dir,
        fps=int(man.get("fps", 25) or 25),
        frame_count=int(man.get("frame_count") or 0) or None,
        loop_seconds=IDLE_LOOP_SECONDS,
    )
    tmp = pkg_dir / "idle_ping.mp4"
    make_pingpong_mp4(out, tmp)
    tmp.replace(out)
    man["idle_video"] = "idle.mp4"
    man["idle_pingpong"] = True
    man["idle_loop_seconds"] = IDLE_LOOP_SECONDS
    man_path.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


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
    if (manifest.get("mode") or "single") == "dual":
        # 双视频包区间固定：idle 全片正放倒放，talk 全片循环
        if transition_frames is not None:
            manifest["transition_frames"] = transition_frames
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest
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
    ensure_idle_mp4(pkg_dir, force=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="", help="legacy single video")
    ap.add_argument("--idle", default="", help="idle / closed-mouth video")
    ap.add_argument("--talk", default="", help="talking / mouth video")
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

    try:
        if args.idle and args.talk:
            manifest = build_avatar_package_dual(
                Path(args.idle),
                Path(args.talk),
                avatar_id=args.avatar_id,
                version_id=args.version_id,
                out_root=Path(args.out_root),
                transition_frames=args.transition_frames,
                voice_id=args.voice_id,
                name=args.name,
            )
        else:
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
