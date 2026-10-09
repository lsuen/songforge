# -*- coding: utf-8 -*-
"""songforge CLI 入口（被 ComfyUI 升级清空后重建）。

    python songforge\songforge.py doctor
    python songforge\songforge.py gen --style pop --title 夜班 --lyrics-file a.txt
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from songforge.cli.main import main   # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
