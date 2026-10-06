"""Replaceable, process-local sessions. No tokens are stored in cookies."""
import secrets
import threading
import time
from dataclasses import dataclass, field


@dataclass
class BrowserSession:
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    tokens: dict = field(default_factory=dict)
    oauth: dict = field(default_factory=dict)
    profile: dict = field(default_factory=dict)
    playlists: list = field(default_factory=list)
    review: dict | None = None
    result: dict | None = None
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    touched: float = field(default_factory=time.monotonic)


class MemorySessions:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()

    def get(self, identifier):
        with self.lock:
            now = time.monotonic()
            for key, value in list(self.sessions.items()):
                if now - value.touched > 86400 and not value.lock.locked():
                    del self.sessions[key]
            if identifier not in self.sessions:
                identifier = secrets.token_urlsafe(32)
                self.sessions[identifier] = BrowserSession()
            state = self.sessions[identifier]
            state.touched = now
            return identifier, state

    def remove(self, identifier):
        with self.lock:
            self.sessions.pop(identifier, None)
