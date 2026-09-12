import asyncio

from masyg_extractor.services.cache import OptionalRedisCache


class FailingRedis:
    def __init__(self):
        self.get_calls = 0
        self.set_calls = 0

    async def get(self, key):
        self.get_calls += 1
        raise OSError("redis unavailable")

    async def set(self, key, value, ex=None):
        self.set_calls += 1
        raise OSError("redis unavailable")


class WorkingRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value
        return True


def test_cache_outage_is_a_miss_not_an_exception():
    client = FailingRedis()
    cache = OptionalRedisCache(enabled=True, client=client, retry_after_seconds=30)

    assert asyncio.run(cache.get_json("analytics:user")) is None
    # Circuit breaker means repeated requests don't hammer an unavailable Redis.
    assert asyncio.run(cache.get_json("analytics:user")) is None
    assert client.get_calls == 1


def test_cache_write_outage_does_not_escape():
    client = FailingRedis()
    cache = OptionalRedisCache(enabled=True, client=client, retry_after_seconds=30)

    assert asyncio.run(cache.set_json("analytics:user", {"ok": True}, ttl_seconds=300)) is False
    assert client.set_calls == 1


def test_cache_round_trips_json_when_healthy():
    client = WorkingRedis()
    cache = OptionalRedisCache(enabled=True, client=client)

    assert asyncio.run(cache.set_json("analytics:user", {"value": 3}, ttl_seconds=300)) is True
    assert asyncio.run(cache.get_json("analytics:user")) == {"value": 3}
