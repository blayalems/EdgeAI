"""Host model of firmware/main/detection_agg.c's bucket semantics.

The model deliberately accepts explicit timestamps so window boundaries,
zero-detection wakes, epoch bucket zero and clock rollback can be tested
without ESP-IDF hardware.
"""
from __future__ import annotations

from dataclasses import dataclass, field

AGG_WINDOW_MIN = 30
AGG_BUCKET_SEC = 300
AGG_BUCKET_COUNT = AGG_WINDOW_MIN * 60 // AGG_BUCKET_SEC
UINT16_MAX = 0xFFFF


@dataclass
class DetectionAggregator:
    buckets: dict[int, int] = field(default_factory=dict)

    @staticmethod
    def _bucket(unix_s: int) -> int:
        return max(0, unix_s) // AGG_BUCKET_SEC

    def _expire(self, now_bucket: int) -> None:
        oldest = max(0, now_bucket - (AGG_BUCKET_COUNT - 1))
        self.buckets = {
            idx: count for idx, count in self.buckets.items()
            if oldest <= idx <= now_bucket
        }

    def add_at(self, unix_s: int, detections: int) -> None:
        now = self._bucket(unix_s)
        self._expire(now)
        if detections <= 0:
            return
        self.buckets[now] = min(
            UINT16_MAX, self.buckets.get(now, 0) + detections
        )

    def count_at(self, unix_s: int) -> int:
        self._expire(self._bucket(unix_s))
        return min(UINT16_MAX, sum(self.buckets.values()))
