# -*- coding: utf-8 -*-
"""内核异常。cli_driver 只捕获这些，不吞系统异常。"""


class SongForgeError(Exception):
    """所有内核错误的基类。"""


class ConfigError(SongForgeError):
    """配置非法，或试图使用被禁用的模型。"""


class RuleViolation(SongForgeError):
    """违反生歌铁律。"""


class LyricDensityError(RuleViolation):
    """歌词字数与目标时长不匹配（死心塌地时长对应关系）。"""


class LexiconError(RuleViolation):
    """歌词命中身份禁用词。"""


class ServerError(SongForgeError):
    """ComfyUI 不可达或返回异常。"""

    def __init__(self, msg, detail=""):
        super().__init__(msg)
        self.detail = detail


class JobFailed(SongForgeError):
    """任务在 ComfyUI 侧执行失败。"""

    def __init__(self, msg, detail=""):
        super().__init__(msg)
        self.detail = detail
