"""Self-check: talking frames ping-pong until stop."""
from __future__ import annotations

import numpy as np

from avatar_frame_controller import AvatarFrameController, AvatarState


class _Pkg:
    talk_start = 0
    talk_end = 3
    transition_frames = 0
    frames = [np.full((2, 2, 3), i, dtype=np.uint8) for i in range(4)]
    idle_frames = [np.zeros((2, 2, 3), dtype=np.uint8)]
    idle_frame = idle_frames[0]


def main() -> None:
    ctrl = AvatarFrameController(_Pkg())  # type: ignore[arg-type]
    ctrl.state = AvatarState.TALKING
    ctrl.talk_index = 0
    ctrl._talk_dir = 1
    seq = []
    for _ in range(10):
        ctrl.next_frame()
        seq.append((ctrl.talk_index, ctrl._talk_dir))
    # 0→1→2→3→2→1→0→1… 取帧后索引
    dirs = [d for _, d in seq]
    assert -1 in dirs and 1 in dirs, seq
    idxs = [i for i, _ in seq]
    assert max(idxs) <= 3 and min(idxs) >= 0, idxs
    # 正放到底后应倒放
    assert seq[3][1] == -1 or seq[4][1] == -1, seq
    print("ok", seq)


if __name__ == "__main__":
    main()
