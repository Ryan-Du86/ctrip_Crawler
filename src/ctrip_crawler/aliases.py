"""
别名映射库。

特色街区、子景点与著名别名往往不在携程搜索结果的前列，
这里维护一张映射表，把它们直接指向官方主干景区的点评主页。

映射表放在 ``data/alias_sight_map.json``，方便在不改动代码的情况下增补，
也方便按站点拆分维护。携程改版导致某个 URL 失效时，只需改这一个文件。
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# 映射表数据文件（随包一起分发）
DATA_FILE = Path(__file__).resolve().parent / "data" / "alias_sight_map.json"


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, str], dict[str, str]]:
    """
    读取并缓存映射表。

    :return: (别名 -> URL 映射, 别名 -> 提示文案)
    """
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        logger.warning("Alias map file not found: %s", DATA_FILE)
        return {}, {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read the alias map file %s: %s", DATA_FILE, exc)
        return {}, {}

    if not isinstance(payload, dict):
        logger.warning("Alias map file has an unexpected structure: %s", DATA_FILE)
        return {}, {}

    aliases = payload.get("aliases") or {}
    hints = payload.get("hints") or {}
    return (
        {str(k): str(v) for k, v in aliases.items()},
        {str(k): str(v) for k, v in hints.items()},
    )


def alias_map() -> dict[str, str]:
    """返回「别名 -> 携程景点主页 URL」的完整映射表副本。"""
    return dict(_load()[0])


def find_alias(spot_input: str) -> tuple[str, str] | None:
    """
    查找别名对应的官方主干景区页面。

    :param spot_input: 用户输入的景点名称
    :return: (别名键, 目标 URL)；未命中时返回 None
    """
    if not spot_input:
        return None
    key = spot_input.strip()
    if not key:
        return None
    url = _load()[0].get(key)
    if not url:
        return None
    return key, url


def get_hint(alias_key: str | None) -> str:
    """
    返回该别名对应的提示文案（抓不到点评时给用户的说明）。

    :param alias_key: 别名键，为 None 时返回空字符串
    :return: 提示文案，没有则为空字符串
    """
    if not alias_key:
        return ""
    return _load()[1].get(alias_key.strip(), "")
