# -*- coding: utf-8 -*-
"""songforge CLI 入口。

任意目录 clone 后：

    cd D:\\CodeFiles\\ProProjects\\songforge
    copy config\\local.toml.example config\\local.toml   # 填 comfy_root / server
    .\\.venv\\Scripts\\python.exe songforge.py doctor

也可在父目录执行：python songforge\\songforge.py ...
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
# 包名 = 文件夹名：把父目录加入 path，使 `import songforge` 指向本仓
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from songforge.cli.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
