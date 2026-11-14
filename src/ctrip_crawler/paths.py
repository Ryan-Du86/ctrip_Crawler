"""
项目路径常量。

集中管理各处会用到的目录，避免路径散落在各处硬编码。
"""

from pathlib import Path

# 项目根目录（src/ctrip_crawler/paths.py -> 向上两级）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

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
