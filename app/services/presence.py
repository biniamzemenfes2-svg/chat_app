"""Who is connected right now.

A user is online while at least one of their browser tabs/devices holds a
Socket.IO connection. State lives in this process's memory, so it is only
correct with a single worker (documented in the README; a Redis-backed store is
the upgrade path).
"""
import threading


class PresenceTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._user_sids: dict[int, set[str]] = {}
        self._sid_user: dict[str, int] = {}

    def connect(self, user_id: int, sid: str) -> bool:
        """Register a connection. Returns True if the user just came online."""
        with self._lock:
            sids = self._user_sids.setdefault(user_id, set())
            came_online = not sids
            sids.add(sid)
            self._sid_user[sid] = user_id
            return came_online

    def disconnect(self, sid: str) -> tuple[int | None, bool]:
        """Forget a connection. Returns (user_id, went_offline)."""
        with self._lock:
            user_id = self._sid_user.pop(sid, None)
            if user_id is None:
                return None, False
            sids = self._user_sids.get(user_id, set())
            sids.discard(sid)
            if sids:
                return user_id, False
            self._user_sids.pop(user_id, None)
            return user_id, True

    def is_online(self, user_id: int) -> bool:
        with self._lock:
            return bool(self._user_sids.get(user_id))

    def online_among(self, user_ids) -> list[int]:
        with self._lock:
            return [uid for uid in user_ids if self._user_sids.get(uid)]

    def sids_for(self, user_id: int) -> list[str]:
        with self._lock:
            return list(self._user_sids.get(user_id, ()))

    def reset(self) -> None:
        with self._lock:
            self._user_sids.clear()
            self._sid_user.clear()


tracker = PresenceTracker()
