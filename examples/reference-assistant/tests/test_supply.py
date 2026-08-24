# -*- coding: utf-8 -*-
"""扩展供应链验证合同测试（第 8 章 P1 · 13.7）。"""
from __future__ import annotations

from assistant.supply.registry import (
    ExtensionKind, Package, Registry, sign, content_hash, VerifyError,
)

KEY = b"publisher-A-secret-key"


def make_pkg(content=b"tool-schema-v1", publisher="pub-A", name="ticket-mcp",
             version="1.0.0", key=KEY):
    return Package(name=name, kind=ExtensionKind.MCP_SERVER, version=version,
                   publisher=publisher, content=content, signature=sign(content, key))


def fresh_registry():
    return Registry(trusted_keys={"pub-A": KEY})


def test_valid_package_passes():
    ok, why = fresh_registry().verify(make_pkg())
    assert ok and why == "ok"


def test_untrusted_publisher_rejected():
    pkg = make_pkg(publisher="pub-unknown", key=KEY)
    ok, why = fresh_registry().verify(pkg)
    assert not ok and why == VerifyError.UNTRUSTED_PUBLISHER.value


def test_tampered_content_fails_signature():
    pkg = make_pkg()
    # 篡改内容但保留旧签名
    tampered = Package(pkg.name, pkg.kind, pkg.version, pkg.publisher,
                       content=b"tool-schema-EVIL", signature=pkg.signature)
    ok, why = fresh_registry().verify(tampered)
    assert not ok and why == VerifyError.BAD_SIGNATURE.value


def test_forged_signature_wrong_key_fails():
    pkg = make_pkg(key=b"attacker-key")     # 用错误密钥签
    ok, why = fresh_registry().verify(pkg)
    assert not ok and why == VerifyError.BAD_SIGNATURE.value


def test_rug_pull_hash_mismatch_rejected():
    reg = fresh_registry()
    good = make_pkg(content=b"reviewed-v1")
    reg.pin("ticket-mcp", good.digest)               # 评审后锁定哈希
    assert reg.verify(good)[0] is True
    # 发布者升级后内容变了（描述投毒），签名仍有效但哈希不符
    updated = make_pkg(content=b"reviewed-v1-POISONED")
    ok, why = reg.verify(updated)
    assert not ok and why == VerifyError.HASH_MISMATCH.value


def test_revoked_hash_rejected():
    reg = fresh_registry()
    pkg = make_pkg()
    assert reg.verify(pkg)[0] is True
    reg.revoke(pkg.digest)                            # 事后发现漏洞，撤销
    ok, why = reg.verify(pkg)
    assert not ok and why == VerifyError.REVOKED.value


def test_content_hash_deterministic():
    assert content_hash(b"abc") == content_hash(b"abc")
    assert content_hash(b"abc") != content_hash(b"abd")


def test_all_extension_kinds_supported():
    reg = fresh_registry()
    for kind in ExtensionKind:
        c = f"body-{kind.value}".encode()
        pkg = Package("ext", kind, "1.0.0", "pub-A", c, sign(c, KEY))
        assert reg.verify(pkg)[0] is True             # 同一条链覆盖所有扩展类型
