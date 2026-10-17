"""短时列表快照，仅保存不可变 URI，游标绑定身份/端点并逐页重新授权。"""

import secrets
import time
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class ListingSnapshot:
    uris: tuple[str, ...]
    position: int
    owner: str
    expires: float


class SkillListingCursors:
    """有界进程内分页状态，不存储正文，不把 token 当授权凭证。"""

    def __init__(self, *, capacity: int = 128, ttl_seconds: float = 60) -> None:
        if capacity < 1 or ttl_seconds < 0:
            raise ValueError("游标容量必须为正数，TTL 不能为负数")
        self.capacity, self.ttl_seconds = capacity, ttl_seconds
        self._snapshots: dict[str, ListingSnapshot] = {}
        self._lock = Lock()

    def save(self, uris: tuple[str, ...], position: int, owner: str) -> str:
        """保存后续位置；淘汰旧游标时客户端需从首页重新查询。"""
        with self._lock:
            self._prune()
            while len(self._snapshots) >= self.capacity:
                self._snapshots.pop(next(iter(self._snapshots)))
            token = secrets.token_urlsafe(32)
            self._snapshots[token] = ListingSnapshot(
                uris, position, owner, time.monotonic() + self.ttl_seconds
            )
            return token

    def get(self, token: str, owner: str) -> ListingSnapshot:
        """分页重试是幂等的；未知/过期/跨身份游标统一拒绝。"""
        with self._lock:
            self._prune()
            snapshot = self._snapshots.get(token)
            if snapshot is None or snapshot.owner != owner:
                raise ValueError("无效或过期的 Skills 游标")
            return snapshot

    def _prune(self) -> None:
        expired = [
            token
            for token, snapshot in self._snapshots.items()
            if snapshot.expires <= time.monotonic()
        ]
        for token in expired:
            self._snapshots.pop(token)
