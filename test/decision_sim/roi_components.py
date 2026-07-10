"""Host model of roi_diff.c's bounded eight-connected component pass."""
from __future__ import annotations


def retained_component_sizes(mask: list[bool], width: int, height: int, *,
                             min_pixels: int, capacity: int) -> list[int]:
    if len(mask) != width * height:
        raise ValueError("mask dimensions do not match")
    seen = [False] * len(mask)
    sizes: list[int] = []
    for seed, changed in enumerate(mask):
        if not changed or seen[seed]:
            continue
        seen[seed] = True
        queue = [seed]
        size = 0
        while queue:
            pos = queue.pop()
            size += 1
            x, y = pos % width, pos // width
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < width and 0 <= ny < height):
                        continue
                    nxt = ny * width + nx
                    if mask[nxt] and not seen[nxt]:
                        seen[nxt] = True
                        queue.append(nxt)
        if size >= min_pixels:
            sizes.append(size)
    return sorted(sizes, reverse=True)[:capacity]
