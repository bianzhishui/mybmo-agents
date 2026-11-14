#!/usr/bin/env python3
"""ECWF 脚本共享的字段枚举表（2026-11-14 按 mybmo-mcp 0.3.2 口径校正）。

为什么不放在 schema 里就算完：`snapshot_diff.py` 比对的是**服务端读回值**，
它不经过 JSON Schema；`validate_payloads.py` 除了 schema 外还要对"已有白名单但枚举漏项"
的字段做一次业务枚举核对。两处共用同一张表，避免"schema 改了脚本没改 → 真值被判非法"。

`excludeTrans` 是重灾区：官方枚举只写 1/4，但服务端实测接受并默认 "0"（=不限）。
"""

from __future__ import annotations

from typing import Any

# 值统一按字符串比较前先做 str()，同时容纳接口回显的字符串与模板里的整数
AUDIENCE_FIELD_ENUMS: dict[str, set[str]] = {
    "regionType": {"0", "1"},
    "keywordsExtend": {"1", "2"},
    "intelligentWeight": {"0", "1"},
    "excludeTrans": {"0", "1", "4"},  # 0=不限（官方漏写）
}

# 顶部/计划层的少量枚举，供快照比对时做温和核对
CAMPAIGN_FIELD_ENUMS: dict[str, set[str]] = {
    "targetType": {"1", "2"},
    "eshopType": {"1", "4"},
    "campaignType": {"1", "4"},
    "ideaType": {"1", "2"},
    "transType": {"14", "90", "80", "81", "82", "83", "84"},
}

# pictures 精确宽高白名单（方向固定），与 schemas/addCampaignShopping.schema.json 的
# definitions.material.properties.pictures["x-platform-allowedSizes"] 保持一致
PICTURES_ALLOWED_SIZES: set[tuple[int, int]] = {(370, 245), (1140, 640), (750, 1000)}
MAX_PICTURES = 18


def check_enum(table: dict[str, set[str]], field: str, value: Any) -> str | None:
    """值在枚举内返回 None，否则返回一句可读错误。"""
    if field not in table:
        return None
    if value is None:
        return None
    allowed = table[field]
    if str(value) in allowed:
        return None
    return f"{field}={value!r} 不在允许枚举 {sorted(allowed)} 内"
