# -*- coding: utf-8 -*-
"""songforge CLI 驱动（薄调度层）。

命令：
    doctor / styles / target / check / gen / runs / show / catalog /
    prune / qc / selftest

设计约束：所有业务参数来自配置/命令行，本文件不写死任何模型或采样常量。
实现见 common / assemble / commands / selftest / qc。
"""
import argparse
import sys

from ..core.errors import (LyricDensityError, RuleViolation, SongForgeError)
from . import commands as cmds
from . import selftest as stmod


def build_parser():
    p = argparse.ArgumentParser(
        prog="songforge",
        description="ACE-Step 生歌引擎。配置优先级：命令行 > 环境变量 > "
                    "local.toml > default.toml")
    p.add_argument("--server", help="ComfyUI 地址")
    p.add_argument("--log-level", default="info",
                   choices=["debug", "info", "warn", "error"])
    p.add_argument("--quiet", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("doctor", help="环境自检")
    d.add_argument("--shift", type=float)
    d.add_argument("--steps", type=int)
    d.add_argument("--cfg", type=float)
    d.add_argument("--template")
    d.add_argument("--runs-dir", dest="runs_dir")
    d.set_defaults(func=cmds.cmd_doctor)

    s = sub.add_parser("styles", help="列出风格字典")
    s.set_defaults(func=cmds.cmd_styles)

    st = sub.add_parser("selftest", help="全链路自检（不连 ComfyUI 生成）")
    st.set_defaults(func=stmod.cmd_selftest)

    t = sub.add_parser("target", help="死心塌地字数目标")
    t.add_argument("--style", help="用风格字典的 bpm/时长；可再被 --bpm/--dur 覆盖")
    t.add_argument("--bpm", type=int)
    t.add_argument("--dur", type=int)
    t.add_argument("--key")
    t.add_argument("--chill-vocal", dest="chill_vocal", action="store_true",
                   help="英文低声吟唱：自动按 sparse 档（音乐主导）算目标字数")
    t.add_argument("--instrumental", action="store_true",
                   help="纯器乐：算纯器乐结构下的器乐容量（无可唱歌词）")
    t.add_argument("--density-profile", dest="density_profile",
                   help="密度档：default（人声填满）/ sparse（音乐主导、人声点缀）")
    t.set_defaults(func=cmds.cmd_target)

    c = sub.add_parser("check", help="纯本地校验，不连 ComfyUI")
    c.add_argument("--style", required=True)
    c.add_argument("--lyrics-file", dest="lyrics_file")
    c.add_argument("--lyrics")
    c.add_argument("--title")
    c.add_argument("--bpm", type=int)
    c.add_argument("--key")
    c.add_argument("--dur", type=int)
    c.add_argument("--instruments")
    c.add_argument("--mood")
    c.add_argument("--shift", type=float)
    c.add_argument("--steps", type=int)
    c.add_argument("--cfg", type=float)
    c.add_argument("--temperature", type=float)
    c.add_argument("--enc-cfg", dest="enc_cfg", type=float)
    c.add_argument("--template")
    c.add_argument("--runs-dir", dest="runs_dir")
    c.add_argument("--language")
    c.add_argument("--chill-vocal", dest="chill_vocal", action="store_true",
                   help="英文低声吟唱模式：放开 humming/melisma，"
                        "人声退到点缀位，突出音乐本身")
    c.add_argument("--instrumental", action="store_true",
                   help="纯器乐：整首无人声，只测 prompt 对乐器的控制力。"
                        "不需要 --lyrics-file")
    c.add_argument("--density-profile", dest="density_profile",
                   help="密度档：default（人声填满）/ sparse（音乐主导、人声点缀）")
    c.add_argument("--lean-tags", dest="lean_tags", action="store_true",
                   help="精简正向标签：保留'前奏器乐+第一拍鼓'硬约束，"
                        "但不注入 RULE_NEG 那 20+ 条人声否定句")
    c.set_defaults(func=cmds.cmd_check)

    g = sub.add_parser("gen", help="生成歌曲")
    g.add_argument("--style", required=True)
    g.add_argument("--lyrics-file", dest="lyrics_file", required=False)
    g.add_argument("--lyrics")
    g.add_argument("--title", required=True)
    g.add_argument("--bpm", type=int)
    g.add_argument("--key")
    g.add_argument("--dur", type=int)
    g.add_argument("--instruments")
    g.add_argument("--mood")
    g.add_argument("--seeds", type=int, default=1)
    g.add_argument("--seed", type=int)
    g.add_argument("--shift", type=float)
    g.add_argument("--steps", type=int)
    g.add_argument("--cfg", type=float)
    g.add_argument("--temperature", type=float)
    g.add_argument("--enc-cfg", dest="enc_cfg", type=float)
    g.add_argument("--template")
    g.add_argument("--runs-dir", dest="runs_dir")
    g.add_argument("--language")
    g.add_argument("--chill-vocal", dest="chill_vocal", action="store_true",
                   help="英文低声吟唱模式：放开 humming/melisma，"
                        "人声退到点缀位，突出音乐本身（第三张专辑用）")
    g.add_argument("--instrumental", action="store_true",
                   help="纯器乐：整首无人声，只测 prompt 对乐器的控制力。"
                        "不需要 --lyrics-file")
    g.add_argument("--density-profile", dest="density_profile",
                   help="密度档：default（人声填满）/ sparse（音乐主导、人声点缀）")
    g.add_argument("--lean-tags", dest="lean_tags", action="store_true",
                   help="精简正向标签：保留'前奏器乐+第一拍鼓'硬约束，"
                        "但不注入 RULE_NEG 那 20+ 条人声否定句")
    g.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="只写 workflow 与 API 预览，不提交 ComfyUI")
    g.set_defaults(func=cmds.cmd_gen)

    r = sub.add_parser("runs", help="列出历史运行")
    r.add_argument("--limit", type=int, default=20)
    r.add_argument("--day", help="只列某一天 YYYY-MM-DD")
    r.set_defaults(func=cmds.cmd_runs)

    w = sub.add_parser("show", help="查看某次运行")
    w.add_argument("run_id")
    w.set_defaults(func=cmds.cmd_show)

    cat = sub.add_parser("catalog", help="全局索引（歌名/风格/BPM/QC）")
    cat.add_argument("--limit", type=int, default=50)
    cat.add_argument("--day", help="只列某一天 YYYY-MM-DD")
    cat.add_argument("--rebuild", action="store_true",
                     help="从 data/runs 扫描重建 catalog.jsonl")
    cat.set_defaults(func=cmds.cmd_catalog)

    pr = sub.add_parser("prune", help="清理过期 data/tmp（不动 runs/音频）")
    pr.add_argument("--days", type=int, default=7,
                    help="保留最近 N 天的 tmp 日目录（默认 7）")
    pr.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="只列出将删除的目录")
    pr.add_argument("--include-migrated", dest="include_migrated",
                    action="store_true",
                    help="一并删除 tmp/_migrated 等非日期目录")
    pr.set_defaults(func=cmds.cmd_prune)

    from .qc import add_qc
    add_qc(sub)
    return p


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args) or 0
    except (RuleViolation, LyricDensityError) as e:
        print("\n[校验不通过]\n%s" % e)
        return 1
    except SongForgeError as e:
        detail = getattr(e, "detail", "")
        print("\n[错误] %s" % e)
        if detail:
            print("  详情: %s" % detail)
        return 2


if __name__ == "__main__":
    sys.exit(main())
