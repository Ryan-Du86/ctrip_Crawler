"""
支持以 ``python -m ctrip_crawler`` 方式启动。

用法：
    uv run python -m ctrip_crawler "故宫" --pages 5
"""

from .cli import main

if __name__ == "__main__":
    main()
