"""Alpha crossfade between two RGB frames."""
from __future__ import annotations

import numpy as np


def crossfade_frames(a: np.ndarray, b: np.ndarray, steps: int = 4) -> list[np.ndarray]:
    """Generate `steps` blended frames from a -> b (uint8 HxWx3)."""
    if steps <= 0:
        return [b.copy()]
    out: list[np.ndarray] = []
    a_f = a.astype(np.float32)
    b_f = b.astype(np.float32)
    for i in range(steps):
        t = (i + 1) / steps
        blend = (1.0 - t) * a_f + t * b_f
        out.append(np.clip(blend, 0, 255).astype(np.uint8))
    return out
