import hashlib
import json
import time
from threading import RLock

_lock = RLock()
_cache = {}

def cache_get(key):
    now = time.time()
    with _lock:
        item = _cache.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at <= now:
            _cache.pop(key, None)
            return None
        return value

def cache_set(key, value, ttl_seconds):
    with _lock:
        _cache[key] = (time.time() + ttl_seconds, value)

def stable_key(namespace, payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"
