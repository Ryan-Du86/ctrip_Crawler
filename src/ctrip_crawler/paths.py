"""
项目路径常量。

集中管理各处会用到的目录，避免路径散落在各处硬编码。
"""

import os
from pathlib import Path

# 可选的环境变量：指定数据根目录（安装成包后默认落在当前工作目录）
HOME_ENV_VAR = "CTRIP_CRAWLER_HOME"


def _find_project_root() -> Path | None:
    """
    从本文件所在位置向上查找项目根目录。

    源码检出时能找到带 pyproject.toml 与 src/ 的仓库根目录；
    安装成 wheel 之后找不到，此时由调用方回退到当前工作目录。

    :return: 项目根目录，未找到时返回 None
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src").is_dir():
            return parent
    return None


def _resolve_base_dir() -> Path:
    """
    决定数据根目录。

    优先级：环境变量 > 项目根目录 > 当前工作目录。
    """
    env_value = os.environ.get(HOME_ENV_VAR, "").strip()
    if env_value:
        return Path(env_value).expanduser()
    project_root = _find_project_root()
    if project_root is not None:
        return project_root
    return Path.cwd()


# 数据根目录（源码检出时即项目根目录；安装成包后为当前工作目录）
PROJECT_ROOT = _resolve_base_dir()

# 采集结果输出目录（默认导出位置）
OUTPUT_DIR = PROJECT_ROOT / "output"

# 示例数据目录（如景点名单模板）
EXAMPLES_DIR = PROJECT_ROOT / "examples"


def resolve_output_path(output_path: str) -> Path:
    """
    把导出路径解析为绝对路径。

    - 绝对路径：原样返回
    - 相对路径：相对于 output/ 目录（而非当前工作目录），
      这样无论从哪个目录调用，产物都统一落在 output/ 下。

    :param output_path: 用户指定的输出文件名或路径
    :return: 解析后的绝对路径
    """
    path = Path(output_path)
    if path.is_absolute():
        return path
    return OUTPUT_DIR / path
