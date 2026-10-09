# -*- coding: utf-8 -*-
"""CLI 共用：配置组装、风格解析、运行 ID、时长闸门。"""
import datetime
import os
import re

from ..config import loader
from ..core import paths as pmod
from ..core import workflow as wfmod
from ..core.errors import ConfigError
from ..log.logger import Logger, Run

ROOT = pmod.PKG_ROOT

# chill 吟唱（第三张专辑）的前奏小节数。
#
# 这里曾是 16：想靠"加长前奏 + 前 20 秒纯器乐"把人声压下去。
# 实测 InstLowChill2 完全无效——vocals stem 仍然从 0.0 秒开始唱。
# 原因见 ironlaw.RULE_CHILL_INTRO 的注释：ACE-Step 按歌词体的结构标记顺序
# 分配段落，不按时长，标签里写多少小节都不起作用。
# 现在三种模式统一 8 小节，和标签、歌词头三处保持一致。
# 结构重排只降低人声密度，不推迟起点（见 ironlaw 顶部实测结论）。
CHILL_INTRO_BARS = 8


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|]', "_", s).strip()
    return s or "untitled"


def now_id(prefix, root=None):
    """生成运行 ID，并把 runs/<run_id>/ 目录原子占位，返回 run_id。

    旧实现只到秒，连跑三首会落进同一个 runs 目录，lyrics.txt / tags.txt /
    manifest.json 互相覆盖，运行记录直接失效。这里做三层保险：
      1. 毫秒时间戳，保证连续调用几乎不会撞；
      2. os.makedirs(exist_ok=False) 原子占位，同毫秒内也只有一个能拿到；
      3. 真撞上就加序号自增，绝不覆盖已有目录。
    """
    base = "%s-%s" % (
        prefix,
        datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3],
    )
    if not root:
        return base
    for n in range(0, 100):
        cand = base if n == 0 else "%s-%d" % (base, n + 1)
        try:
            os.makedirs(os.path.join(root, cand), exist_ok=False)
            return cand
        except FileExistsError:
            continue
    raise RuntimeError("无法为 %s 分配运行目录（root=%s）" % (prefix, root))


def build_cfg(args):
    ov = {}
    if getattr(args, "server", None):
        ov["engine.server"] = args.server
    if getattr(args, "shift", None) is not None:
        ov["sampler.shift"] = args.shift
    if getattr(args, "steps", None) is not None:
        ov["sampler.steps"] = args.steps
    if getattr(args, "cfg", None) is not None:
        ov["sampler.cfg"] = args.cfg
    if getattr(args, "temperature", None) is not None:
        ov["encode.temperature"] = args.temperature
    if getattr(args, "enc_cfg", None) is not None:
        ov["encode.cfg_scale"] = args.enc_cfg
    if getattr(args, "template", None):
        ov["paths.workflow_template"] = args.template
    if getattr(args, "runs_dir", None):
        ov["paths.runs_dir"] = args.runs_dir
    if getattr(args, "language", None):
        ov["song.language"] = args.language
    return loader.load(ov)


def style_of(cfg, name, args):
    """命令行覆盖优先于风格字典。"""
    st = dict(loader.styles().get(name) or {})
    if not st:
        raise ConfigError("未知风格 '%s'，用 `songforge styles` 查看全部" % name)
    if getattr(args, "bpm", None):
        st["bpm"] = args.bpm
    if getattr(args, "key", None):
        st["key"] = args.key
    if getattr(args, "dur", None):
        st["duration"] = args.dur
    if getattr(args, "instruments", None):
        st["instruments"] = args.instruments
    if getattr(args, "mood", None):
        st["mood"] = args.mood
    return st


def resolve_profile(args):
    """决定用哪个密度档。

    chill-vocal（英文低声吟唱）默认走 sparse：那一档的可唱小节只占 25%，
    允许整首只有几组 ooh ooh / mmm。之前它借用 default 档，而 default 档是
    按"人声填满全曲"反推的，会把 84 音节的低音吟唱判成"歌词过少"，
    逼着写满 254 音节——那正是"只有人声没有音乐"的直接来源。
    """
    prof = getattr(args, "density_profile", None)
    if prof:
        return prof
    if getattr(args, "chill_vocal", False):
        return "sparse"
    return None


def read_lyrics(args):
    if args.lyrics_file:
        with open(args.lyrics_file, encoding="utf-8") as f:
            return f.read()
    if args.lyrics:
        return args.lyrics.replace("\\n", "\n")
    if getattr(args, "instrumental", False):
        # 纯器乐没有可唱正文，由 assemble 用结构标记自动铺一段。
        return ""
    raise ConfigError("必须提供 --lyrics-file 或 --lyrics")


def chill_intro_bars(cfg, chill):
    """chill 吟唱的前奏长度。

    这里曾试过 16 小节：想靠"加长前奏 + 前 20 秒纯器乐"把人声压下去。
    实测两版（InstLowChill2 / 3）证明标签侧写多少小节都不起作用，
    ACE-Step 按歌词体结构标记顺序分配段落，不按时长描述。
    现在前奏回到 8 小节，和标签、歌词头三处保持一致。

    注意：不要再把这个函数当成"推迟人声"的手段。实测（chill 3）
    即使器乐段整体排在人声段之前，第一句真唱仍然落在 0 秒。
    人声起点无法靠 prompt 控制，只能靠 sparse 密度档把真唱压到稀疏，
    并用音频 QC 验证。
    """
    return CHILL_INTRO_BARS if chill else int(cfg["rules"]["intro_bars"])


def session_logger(args, cfg, prefix):
    """非 gen 命令的会话日志：落到 data/tmp/YYYY-MM-DD/<prefix>-ts/。"""
    layout = pmod.path_layout(cfg)
    day_root = layout["tmp_today"]
    pmod.ensure_dir(day_root)
    rid = now_id(prefix, day_root)
    run = Run(rid, day_root)
    log = Logger(run,
                 level=getattr(args, "log_level", "info"),
                 quiet=getattr(args, "quiet", False))
    return run, log


def runs_root(cfg):
    return pmod.under_workflows(cfg["paths"]["runs_dir"])


def gen_day_root(cfg):
    """data/runs/YYYY-MM-DD/"""
    return pmod.day_subdir(runs_root(cfg))

