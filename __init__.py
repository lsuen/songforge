# -*- coding: utf-8 -*-
"""songforge 核心包重写 - 修复相对导入与模块管理"""

import os
import sys

# 确保正确添加工程根目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 统一重写 core/__init__.py -> core
from .core.workflow import build_workflow, convert, find_node, ROOT, WIDGET_MAP
from .core.errors import RuleViolation, LexiconError
from .rules.ironlaw import build_tags, build_lyrics, validate, TRIGGER
from .config.loader import load

__version__ = "1.0.0"

# 保持微内核结构：CLI -> Rules -> Lyrics -> Core -> Log -> Config
__all__ = [
    'build_workflow', 'convert', 'find_node', 'ROOT', 'WIDGET_MAP',
    'RuleViolation', 'LexiconError',
    'build_tags', 'build_lyrics', 'validate', 'TRIGGER',
    'load'
]
