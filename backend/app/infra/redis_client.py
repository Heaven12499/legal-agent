# -*- coding: utf-8 -*-
"""Redis 统一入口：运行时控制面共享连接池，不承载权威业务状态。"""
import os

_client = None
_client_url = None
_stream_client = None
_stream_client_url = None


def get_redis():
    global _client, _client_url
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    if _client is None or _client_url != url:
        import redis

        _client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=2,
            health_check_interval=30,
        )
        _client_url = url
    return _client


def ping() -> bool:
    client = get_redis()
    return bool(client and client.ping())


def get_stream_redis():
    """供 XREAD BLOCK 使用；socket timeout 必须大于服务端阻塞时间。"""
    global _stream_client, _stream_client_url
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    if _stream_client is None or _stream_client_url != url:
        import redis

        _stream_client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=20,
            health_check_interval=30,
        )
        _stream_client_url = url
    return _stream_client


def reset_for_tests() -> None:
    global _client, _client_url, _stream_client, _stream_client_url
    _client = None
    _client_url = None
    _stream_client = None
    _stream_client_url = None
