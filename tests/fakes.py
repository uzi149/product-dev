"""Small stand-ins for services in unit tests (no new test packages)."""

import redis


class FakeRedis:
    """The few Redis commands the code uses. Expiry is recorded, not enforced."""

    def __init__(self):
        self.data: dict[str, bytes] = {}
        self.ttl: dict[str, int | None] = {}
        self.down = False  # True: every command raises, as when Redis can't be reached

    def _check(self):
        if self.down:
            raise redis.ConnectionError("Redis is down (test)")

    def set(self, key, value, ex=None, nx=False):
        self._check()
        if nx and key in self.data:
            return None
        self.data[key] = value.encode() if isinstance(value, str) else value
        self.ttl[key] = ex
        return True

    def get(self, key):
        self._check()
        return self.data.get(key)

    def mget(self, keys):
        self._check()
        return [self.data.get(k) for k in keys]

    def exists(self, *keys):
        self._check()
        return sum(k in self.data for k in keys)

    def delete(self, *keys):
        self._check()
        return sum(self.data.pop(k, None) is not None for k in keys)

    def eval(self, script, numkeys, key, token, *args):
        """The heartbeat's compare-and-expire / compare-and-delete scripts."""
        self._check()
        if self.data.get(key) != token.encode():
            return 0
        if "expire" in script:
            self.ttl[key] = int(args[0])
        else:
            del self.data[key]
        return 1
