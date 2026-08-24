# -*- coding: utf-8 -*-
"""Durable Execution 原语合同测试（第 12 章 P1 · 13.2）。"""
from __future__ import annotations

from assistant.durable.executor import (
    FailureLayer, RETRY_OWNER, owns_retry,
    IdempotencyStore, DurableQueue, DurableTask, TaskState, backoff_delay,
)


# 1) 重试归属：每层故障只有唯一责任方，其他 actor 不得重试
def test_retry_ownership_single_owner():
    assert owns_retry("transport_sdk", FailureLayer.TRANSPORT) is True
    assert owns_retry("agent_runtime", FailureLayer.TRANSPORT) is False  # 不越权重试下层
    assert owns_retry("durable_executor", FailureLayer.WORKER) is True
    # 每层恰有一个责任方
    assert len(set(RETRY_OWNER.values())) == len(RETRY_OWNER) == len(FailureLayer)


def test_no_two_layers_retry_same_failure():
    # 对任一故障层，只有一个 actor 拥有重试权
    for layer in FailureLayer:
        owners = [a for a in RETRY_OWNER.values() if owns_retry(a, layer)]
        assert len(owners) == 1, (layer, owners)


# 2) 幂等键：副作用只发生一次
def test_idempotency_runs_side_effect_once():
    store = IdempotencyStore()
    calls = []

    def charge():
        calls.append(1)
        return "charged"

    r1 = store.run_once("pay:order-42", charge)
    r2 = store.run_once("pay:order-42", charge)   # 重试/重放
    assert r1 == r2 == "charged"
    assert len(calls) == 1                         # 只扣一次款
    assert store.seen("pay:order-42")


# 3) 租约 + 心跳 + 崩溃回收
def test_lease_and_heartbeat():
    t = DurableTask("t1")
    t.lease("w1", now=0.0, ttl=10.0)
    assert t.state == TaskState.LEASED and t.attempts == 1
    assert t.heartbeat("w1", now=5.0, ttl=10.0) is True    # 续租成功
    assert t.heartbeat("w2", now=6.0, ttl=10.0) is False   # 非持租者不能续
    assert t.lease_expired(now=9.0) is False
    assert t.lease_expired(now=20.0) is True               # 超租 = worker 疑似崩溃


def test_crashed_worker_task_reclaimed():
    q = DurableQueue()
    q.submit(DurableTask("t1", max_attempts=5))
    a = q.acquire("w1", now=0.0, ttl=10.0)
    assert a.owner == "w1" and a.attempts == 1
    # w1 崩溃，不再心跳；租约在 t=20 过期，w2 接管
    b = q.acquire("w2", now=20.0, ttl=10.0)
    assert b.task_id == "t1" and b.owner == "w2" and b.attempts == 2


# 4) 死信：超过尝试上限
def test_dead_letter_after_max_attempts():
    q = DurableQueue()
    q.submit(DurableTask("t1", max_attempts=3))
    now = 0.0
    for _ in range(3):
        t = q.acquire("w", now=now, ttl=1.0)
        assert t is not None
        q.fail("t1")                                # 每次尝试都失败
        now += 10
    # 第 3 次 fail 时 attempts 已达 3 → 死信
    assert q.tasks["t1"].state == TaskState.DEAD
    assert [t.task_id for t in q.dead_letters()] == ["t1"]
    assert q.acquire("w", now=now, ttl=1.0) is None  # 死信不再被领取


# 5) 退避：指数增长且封顶
def test_backoff_exponential_capped():
    assert backoff_delay(1, base=1.0, cap=60.0) == 1.0
    assert backoff_delay(2, base=1.0, cap=60.0) == 2.0
    assert backoff_delay(3, base=1.0, cap=60.0) == 4.0
    assert backoff_delay(10, base=1.0, cap=60.0) == 60.0   # 封顶
    assert backoff_delay(0) == 0.0
