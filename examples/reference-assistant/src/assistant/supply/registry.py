# -*- coding: utf-8 -*-
"""Agent 扩展供应链：签名 → 验证 → 撤销（第 8 章 P1 · 13.7）。

第 8 章把每个 MCP Server 当"npm 依赖 + 数据通道"治理；本模块把那条纪律**推广到全部
可安装扩展**（MCP Server / Skill / Plugin / Prompt Pack / Agent Template / Tool Schema /
Model Adapter / Browser Extension / Sidecar），落成一条可测的信任链：

  Publisher → 签名密钥 → 包 → 版本 → 内容哈希 → 签名 → 验证 → 安装 → 激活 → 运行期策略 → 撤销

验证四关，任一不过即拒装：① 签名有效（真实性）；② 发布者在白名单（来源可信）；
③ 内容哈希与锁定值一致（防 rug pull，对应第 8 章 snapshot_digest）；④ 未被撤销。

教学简化：这里用 HMAC 作对称"签名"占位；生产用非对称签名（Ed25519 / Sigstore）与 SBOM
（供应链等级见 [C-22]）。验证逻辑与撤销语义与真实体系一致。
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from enum import Enum


class ExtensionKind(str, Enum):
    MCP_SERVER = "mcp_server"
    SKILL = "skill"
    PLUGIN = "plugin"
    PROMPT_PACK = "prompt_pack"
    AGENT_TEMPLATE = "agent_template"
    TOOL_SCHEMA = "tool_schema"
    MODEL_ADAPTER = "model_adapter"
    BROWSER_EXTENSION = "browser_extension"
    SIDECAR = "sidecar"


def content_hash(content: bytes) -> str:
    """包内容的 sha256 指纹（锁定与撤销都按它，不追 latest）。"""
    return hashlib.sha256(content).hexdigest()


def sign(content: bytes, key: bytes) -> str:
    """教学占位：HMAC-SHA256。生产换非对称签名。"""
    return hmac.new(key, content, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class Package:
    name: str
    kind: ExtensionKind
    version: str
    publisher: str
    content: bytes
    signature: str

    @property
    def digest(self) -> str:
        return content_hash(self.content)


class VerifyError(str, Enum):
    BAD_SIGNATURE = "签名无效"
    UNTRUSTED_PUBLISHER = "发布者不在白名单"
    HASH_MISMATCH = "内容哈希与锁定不符（疑似 rug pull）"
    REVOKED = "该版本/哈希已被撤销"


# region book:ch08-supply-verify
@dataclass
class Registry:
    """扩展信任库：发布者白名单 + 公钥 + 哈希锁定 + 撤销名单。"""
    trusted_keys: dict[str, bytes] = field(default_factory=dict)   # publisher -> key
    pinned: dict[str, str] = field(default_factory=dict)          # name -> 锁定的内容哈希
    revoked_hashes: set[str] = field(default_factory=set)

    def verify(self, pkg: Package) -> tuple[bool, str]:
        """四关顺序校验；任一不过即拒装，返回 (是否通过, 原因)。"""
        key = self.trusted_keys.get(pkg.publisher)
        if key is None:
            return False, VerifyError.UNTRUSTED_PUBLISHER.value
        # ① 真实性：签名必须由该发布者密钥产生（也就一并防了内容被改）
        if not hmac.compare_digest(pkg.signature, sign(pkg.content, key)):
            return False, VerifyError.BAD_SIGNATURE.value
        # ② 防 rug pull：若该扩展已锁定哈希，本次内容必须一致
        if pkg.name in self.pinned and pkg.digest != self.pinned[pkg.name]:
            return False, VerifyError.HASH_MISMATCH.value
        # ③ 撤销名单：被撤销的内容哈希一律拒装
        if pkg.digest in self.revoked_hashes:
            return False, VerifyError.REVOKED.value
        return True, "ok"

    def revoke(self, digest: str) -> None:
        self.revoked_hashes.add(digest)

    def pin(self, name: str, digest: str) -> None:
        self.pinned[name] = digest
# endregion book:ch08-supply-verify
