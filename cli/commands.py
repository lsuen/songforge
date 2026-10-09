# -*- coding: utf-8 -*-
"""CLI 命令实现：doctor / styles / target / check / gen / runs / show。"""
import datetime
import json
import os
import random

from ..config import loader
from ..core import catalog as catmod
from ..core import paths as pmod
from ..core import workflow as wfmod
from ..core.engine import Engine
from ..core.errors import ConfigError, ServerError
from ..lyrics import meter
from ..log.logger import Logger, Run
from ..rules import ironlaw
from .assemble import (_extra_hint, assemble, do_validate, duration_allowed)
from .common import (ROOT, build_cfg, chill_intro_bars, gen_day_root, now_id,
                     read_lyrics, resolve_profile, runs_root, safe_name,
                     session_logger, style_of)


def cmd_doctor(args):
    cfg = build_cfg(args)
    run, log = session_logger(args, cfg, "doctor")
    layout = pmod.path_layout(cfg)
    root = layout["comfy_root"]
    log.info("doctor 开始", server=cfg["engine"]["server"])
    print("== 路径 ==")
    print("  ComfyUI 根  %s" % root)
    print("  workflows   %s" % ROOT)
    print("  runs        %s  (按日 YYYY-MM-DD/)" % layout["runs"])
    print("  tmp         %s" % layout["tmp"])
    print("  lyrics      %s" % layout["lyrics"])
    print("  baseline    %s" % layout["baseline"])
    print("  catalog     %s" % layout["catalog"])
    print("  qc_cache    %s" % layout["qc_cache"])
    print("  音频输出    %s" % layout["audio_out"])
    local_cfg = os.path.join(ROOT, "songforge", "config", "local.json")
    print("  local.json  %s" % ("有" if os.path.isfile(local_cfg) else "无（可用 local.json.example）"))

    print("== 根目录卫生 ==")
    pkg = layout["pkg"]
    junk = []
    for name in pmod.ROOT_JUNK_NAMES:
        p = os.path.join(pkg, name)
        if os.path.isfile(p):
            junk.append(name)
    for name in ("qc_baseline.json", "qc_baseline_old.json", "style_ref.json"):
        if os.path.isfile(os.path.join(pkg, name)):
            junk.append(name + "（应在 config/baseline/）")
    if junk:
        print("  [WARN] 包根有过程/错位文件: %s" % ", ".join(junk))
    else:
        print("  [ok] 包根无过程垃圾")

    print("== ComfyUI ==")
    ok, info = Engine(cfg["engine"]["server"]).health()
    if not ok:
        print("  [FAIL] %s" % info)
        print("  排查：确认 ComfyUI 已启动且端口 8188 可访问")
        log.error("ComfyUI 不可达", detail=str(info))
        log.close()
        return 2
    print("  [ok] %s  显存 %.1fGB  队列 运行%d/等待%d"
          % (info["device"], info["vram_total_gb"],
             info["queue_running"], info["queue_pending"]))
    print("  [ok] ComfyUI %s" % info["comfyui_version"])

    print("== 模型 ==")
    ck = pmod.model_path(root, "checkpoints", cfg["model"]["checkpoint"])
    lora = pmod.model_path(root, "loras", cfg["model"]["lora"])
    tpl = os.path.join(ROOT, cfg["paths"]["workflow_template"])
    rc = 0
    for label, p in (("checkpoint", ck), ("lora", lora),
                     ("template", tpl)):
        good = os.path.exists(p)
        print("  [%s] %-11s %s" % ("ok" if good else "FAIL", label, p))
        if not good:
            rc = 2
    if cfg["model"]["checkpoint"] in cfg["model"]["denied_checkpoints"]:
        print("  [FAIL] 当前 checkpoint 在禁用名单里：%s"
              % cfg["model"]["checkpoint"])
        rc = 2
    else:
        print("  [ok] 模型锁定 %s + %s"
              % (cfg["model"]["checkpoint"], cfg["model"]["lora"]))

    print("== 铁律 ==")
    for k in ("swlvoice", "8 bar instrumental intro hook", "no opening vocals"):
        good = k in ironlaw.build_tags("test")
        print("  [%s] %s" % ("ok" if good else "FAIL", k))
        if not good:
            rc = 2

    print("== 采样 ==")
    bad = (cfg["sampler"]["shift"] < 5.0
           or cfg["sampler"]["steps"] < 24
           or cfg["sampler"]["cfg"] < 1.5
           or not (0.60 <= cfg["encode"]["temperature"] <= 0.70))
    print("  [%s] shift=%s steps=%s cfg=%s temperature=%s"
          % ("FAIL" if bad else "ok", cfg["sampler"]["shift"],
             cfg["sampler"]["steps"], cfg["sampler"]["cfg"],
             cfg["encode"]["temperature"]))
    if bad:
        print("       偏离已验证参数：shift 5.0~5.5 / steps 24~28 / cfg 2.0"
              " / temperature 0.62~0.68")
        rc = 2

    print("== 时长区间 ==")
    lo, hi = cfg["song"]["duration_range"]
    good = lo <= 150 and hi >= 200
    print("  [%s] 允许 %d~%d 秒（理想 150~200）"
          % ("ok" if good else "WARN", lo, hi))

    print("== 配置 ==")
    print("  服务器=%s" % cfg["engine"]["server"])
    for key in ("runs", "tmp", "tmp_today", "lyrics", "qc_cache",
                "style_cache", "index", "baseline"):
        pmod.ensure_dir(layout[key])
    log.info("doctor 结束", rc=rc, log_dir=run.dir)
    log.close()
    return rc


def cmd_styles(args):
    data = loader.styles()
    print("%-12s %5s %-11s %5s  %s" % ("风格", "BPM", "调性", "时长", "说明"))
    for k, v in sorted(data.items()):
        if k.startswith("_"):
            continue
        print("%-12s %5s %-11s %5s  %s"
              % (k, v["bpm"], v["key"], v["duration"], v["style"]))
    return 0


def cmd_target(args):
    cfg = build_cfg(args)
    if args.style:
        st = style_of(cfg, args.style, args)
    else:
        st = {"bpm": args.bpm or 94, "duration": args.dur or 165}
    if getattr(args, "bpm", None):
        st["bpm"] = args.bpm
    if getattr(args, "dur", None):
        st["duration"] = args.dur
    prof = resolve_profile(args)
    inst = bool(getattr(args, "instrumental", False))
    cfg_lyrics = meter.pick(cfg["lyrics"], prof)
    if args.style:
        print("风格 %s" % args.style)
    print("时长 %ds  BPM %d" % (st["duration"], st["bpm"]))
    if inst:
        stt = meter.analyse("", st["duration"], st["bpm"], cfg_lyrics)
        print("  [纯器乐] 无人声，整首 %.0f 小节全部留给器乐"
              % stt["total_bars"])
        print("  器乐结构段：intro / breakdown / build / drop / "
              "post-drop / final drop / outro")
        print("  可唱歌词目标：0 音节")
        return 0
    stt = meter.analyse("", st["duration"], st["bpm"], cfg_lyrics)
    print("  总小节 %.0f   前奏 %d 小节   可唱 %.0f 小节"
          % (stt["total_bars"], cfg_lyrics["intro_bars"], stt["sing_bars"]))
    if prof:
        print("  [密度档] %s（可唱小节占比 %.0f%%）"
              % (prof, cfg_lyrics["sing_bar_ratio"] * 100))
    mid, lo, hi = meter.target_words(st["duration"], st["bpm"], cfg_lyrics)
    print("  理论音节容量 %d ~ %d" % (stt["lo"], stt["hi"]))
    print("  建议正文字数：%d 字左右（放行区间 %d ~ %d）" % (mid, lo, hi))
    print("  平均每句：%d ~ %d 音节（硬上限 %d）"
          % (cfg_lyrics["ideal_line_syllable"][0],
             cfg_lyrics["ideal_line_syllable"][1],
             cfg_lyrics["line_syllable_max"]))
    return 0


def cmd_check(args):
    cfg = build_cfg(args)
    run, log = session_logger(args, cfg, "check")
    lexicon = loader.lexicon()
    raw = read_lyrics(args)
    st = style_of(cfg, args.style, args)
    tags, lyrics = assemble(args, cfg, st, raw, args.title or "check")
    inst = bool(getattr(args, "instrumental", False))
    prof = resolve_profile(args)
    errs, stats = do_validate(lyrics, tags, cfg, st, lexicon, strict=False,
                              instrumental=inst, profile=prof,
                              lean=bool(getattr(args, "lean_tags", False)))
    run.save_text("lyrics.txt", lyrics)
    run.save_text("tags.txt", tags)
    print("== 密度 ==")
    print("  时长 %ds  BPM %d  调性 %s" % (st["duration"], st["bpm"], st["key"]))
    if inst:
        print("  [纯器乐] 无可唱歌词，密度闸门不适用")
    elif prof:
        print("  [密度档] %s（可唱小节占比 %.0f%%）"
              % (prof, meter.pick(cfg["lyrics"], prof)["sing_bar_ratio"] * 100))
    print("  正文字节 %d 音节 / %d 句 / 平均每句 %.1f 音节"
          % (stats["syllables"], stats["lines"], stats["per_line"]))
    if inst:
        print("  密度闸门 N/A（纯器乐无歌词，放行区间不适用）")
    else:
        print("  理论容量 %d ~ %d   放行区间 %d ~ %d"
              % (stats["lo"], stats["hi"], stats["win_lo"], stats["win_hi"]))
    print("== 结果 ==")
    if errs:
        for e in errs:
            print("  [FAIL] %s" % e)
        log.warn("check 未通过", errors=len(errs), syllables=stats["syllables"])
        log.close()
        return 1
    print("  [OK] 通过，可提交生成")
    log.info("check 通过", syllables=stats["syllables"],
             win="%d-%d" % (stats.get("win_lo", 0), stats.get("win_hi", 0)),
             log_dir=run.dir)
    log.close()
    return 0


def cmd_gen(args):
    cfg = build_cfg(args)
    lexicon = loader.lexicon()
    title = args.title or "untitled"
    st = style_of(cfg, args.style, args)
    raw = read_lyrics(args)

    lo, hi = cfg["song"]["duration_range"]
    if not duration_allowed(cfg, st["duration"]):
        raise ConfigError("时长 %ds 超出允许区间 %d~%ds%s"
                          % (st["duration"], lo, hi,
                             _extra_hint(cfg, st["duration"])))

    tags, lyrics = assemble(args, cfg, st, raw, title)
    inst = bool(getattr(args, "instrumental", False))
    prof = resolve_profile(args)
    ibars = chill_intro_bars(
        cfg, bool(getattr(args, "chill_vocal", False)))
    errs, stats = do_validate(lyrics, tags, cfg, st, lexicon, strict=True,
                              instrumental=inst, profile=prof,
                              lean=bool(getattr(args, "lean_tags", False)))

    day_root = gen_day_root(cfg)
    pmod.ensure_dir(day_root)
    run_id = now_id("gen", day_root)
    run = Run(run_id, day_root)
    log = Logger(run, level=args.log_level, quiet=args.quiet)

    log.info("开始生成", title=title, style=args.style, bpm=st["bpm"],
             dur=st["duration"], key=st["key"], seeds=args.seeds)
    # 纯器乐与英文低声吟唱都强制走英文 language：
    #   1) chill-vocal 的歌词正文本来就是英文；
    #   2) instrumental 没有歌词，但 language 节点仍会影响音色/咬字倾向，
    #      不统一会让 instrumental vs chill 的 A/B 多出一个隐藏变量
    #      （早期 InstPure 就是因为漏传 --language en 走了默认中文，
    #        跟 chill 版对比时无法归因）。
    lang = "en" if (getattr(args, "chill_vocal", False)
                    or inst) else cfg["song"]["language"]
    if getattr(args, "language", None):
        lang = args.language
    log.info("密度校验通过", syllables=stats["syllables"],
             lines=stats["lines"], per_line=round(stats["per_line"], 2),
             win="N/A" if inst else "%d-%d" % (stats["win_lo"],
                                               stats["win_hi"]),
             cap_ratio=round(stats["fill"], 3),
             instrumental=inst, density_profile=prof or "default")

    template = wfmod.load_template(cfg["paths"]["workflow_template"])
    prefix = cfg["paths"]["output_prefix"]
    fmt = cfg["paths"]["output_format"]
    seed_base = args.seed if args.seed else random.randint(1, 2 ** 31 - 1)
    seeds = [seed_base + i for i in range(args.seeds)] if args.seeds > 1 \
        else [seed_base]
    # 永久命名规则：歌名-【seed号】-【特征】-日期戳
    # 特征从风格名中提取，日期戳格式 YYMMDD
    date_stamp = datetime.datetime.now().strftime("%y%m%d")
    style_tag = args.style if len(args.style) <= 12 else args.style[:12]
    safe_title = safe_name(title)

    run.save_text("lyrics.txt", lyrics)
    run.save_text("tags.txt", tags)

    built = []
    for i, sd in enumerate(seeds):
        # 永久命名规则：歌名-【sN】-【风格特征】-YYMMDD
        sub = "%s/%s-s%d-%s-%s" % (prefix, safe_title, i + 1, style_tag, date_stamp)
        wf = wfmod.build_workflow(
            template, title=title, tags=tags, lyrics=lyrics, seed=sd,
            bpm=st["bpm"], duration=st["duration"], keyscale=st["key"],
            sampler=cfg["sampler"], encode=cfg["encode"], model=cfg["model"],
            output_prefix=sub, output_format=fmt,
            language=lang,
            timesignature=cfg["song"]["time_signature"])
        wf_path = run.path("wf_%s_s%d.json" % (safe_title, i + 1))
        with open(wf_path, "w", encoding="utf-8") as f:
            json.dump(wf, f, ensure_ascii=False, indent=1)
        built.append((sd, wf, wf_path, sub))
        log.info("workflow 已写入", file=os.path.basename(wf_path), seed=sd)

    if args.dry_run:
        api = wfmod.render(built[0][1])
        run.save_json("payload_preview.json", api)
        print("\n[dry-run] 未提交 ComfyUI。已产出：")
        print("  歌词 %d 音节 / %d 句 / 平均每句 %.1f 音节"
              % (stats["syllables"], stats["lines"], stats["per_line"]))
        print("  时长 %ds  BPM %d  %s" % (st["duration"], st["bpm"], st["key"]))
        print("  workflow %s" % built[0][2])
        print("  API 预览 %s" % run.path("payload_preview.json"))
        print("  实际输出将写到 %s"
              % pmod.music_dir(cfg, pmod.comfy_root(cfg=cfg)))
        # dry-run 也写入索引，便于目录浏览
        man_dry = dict(
            run_id=run.run_id, title=title, style=args.style,
            bpm=st["bpm"], key=st["key"], duration=st["duration"],
            results=[dict(seed=seeds[0], files=[])],
            created=datetime.datetime.now().isoformat(timespec="seconds"),
            dry_run=True)
        run.save_json("manifest.json", man_dry)
        catmod.append_entry(cfg, catmod.entry_from_gen(run.dir, man_dry))
        log.info("dry-run 结束", seeds=seeds)
        log.close()
        return 0

    eng = Engine(cfg["engine"]["server"], timeout=cfg["engine"]["timeout_sec"],
                 poll_interval=cfg["engine"]["poll_interval_sec"],
                 submit_retries=cfg["engine"]["submit_retries"], logger=log)
    ok, info = eng.health()
    if not ok:
        raise ServerError("ComfyUI 不可达: %s" % info)

    results = []
    for sd, wf, wf_path, sub in built:
        pid = eng.submit_retry(wfmod.render(wf))
        log.info("已提交", seed=sd, prompt_id=pid)

        def prog(tick, secs, sd=sd):
            log.debug("等待中", seed=sd, tick=tick, elapsed=secs)

        outs, rec = eng.wait(pid, on_progress=prog)
        log.info("完成", seed=sd, files=outs)
        results.append(dict(seed=sd, prompt_id=pid, files=outs,
                            workflow=os.path.basename(wf_path)))

    cro = pmod.comfy_root(cfg=cfg)
    manifest = dict(
        run_id=run.run_id, title=title, style=args.style,
        bpm=st["bpm"], key=st["key"], duration=st["duration"],
        language=lang,
        chill_vocal=bool(getattr(args, "chill_vocal", False)),
        instrumental=inst,
        density_profile=prof or "default",
        intro_bars=ibars,
        sampler=cfg["sampler"], encode=cfg["encode"], model=cfg["model"],
        density=stats, results=results,
        output_dir=pmod.music_dir(cfg, cro),
        created=datetime.datetime.now().isoformat(timespec="seconds"),
    )
    mpath = run.save_json("manifest.json", manifest)
    catmod.append_entry(cfg, catmod.entry_from_gen(run.dir, manifest))
    log.info("全部完成", run=run.run_id, manifest=os.path.basename(mpath))

    print("\n生成完成 run_id=%s" % run.run_id)
    print("  时长 %ds  BPM %d  %s  种子 %s"
          % (st["duration"], st["bpm"], st["key"], seeds))
    if inst:
        print("  [纯器乐] 无可唱歌词，密度闸门不适用")
    else:
        print("  字数 %d 音节  放行区间 %d~%d  平均每句 %.1f 音节"
              % (stats["syllables"], stats["win_lo"], stats["win_hi"],
                 stats["per_line"]))
    for r in results:
        print("  seed=%s -> %s" % (r["seed"], ", ".join(r["files"] or ["无输出"])))
    print("  产物目录: %s" % run.dir)
    print("  音频输出: %s" % manifest["output_dir"])
    log.close()
    return 0


def cmd_runs(args):
    cfg = loader.load({})
    rows = catmod.list_entries(cfg, day=getattr(args, "day", None),
                               limit=args.limit)
    if not rows:
        # 无索引时回退扫盘
        path, n = catmod.rebuild(cfg)
        print("索引为空，已 rebuild -> %s (%d 条)" % (path, n))
        rows = catmod.list_entries(cfg, day=getattr(args, "day", None),
                                   limit=args.limit)
    if not rows:
        print("还没有任何运行记录")
        return 0
    print("%-26s %-12s %-10s %5s %5s %-6s  %s"
          % ("run_id", "日期", "标题", "BPM", "时长", "QC", "风格"))
    for d in rows:
        print("%-26s %-12s %-10s %5s %5s %-6s  %s"
              % (d.get("run_id", "?"), d.get("date") or "-",
                 (d.get("title") or "?")[:10], d.get("bpm"),
                 d.get("duration"), d.get("qc_verdict") or "-",
                 d.get("style") or ""))
    return 0


def cmd_show(args):
    cfg = loader.load({})
    rundir = catmod.find_run_dir(runs_root(cfg), args.run_id)
    if not rundir:
        print("找不到 run: %s" % args.run_id)
        return 2
    m = os.path.join(rundir, "manifest.json")
    with open(m, encoding="utf-8") as f:
        print(json.dumps(json.load(f), ensure_ascii=False, indent=1))
    return 0


def cmd_catalog(args):
    cfg = loader.load({})
    if args.rebuild:
        path, n = catmod.rebuild(cfg)
        print("rebuild %s  (%d 条)" % (path, n))
        return 0
    rows = catmod.list_entries(cfg, day=args.day, limit=args.limit)
    if not rows:
        print("索引为空。可先: songforge catalog --rebuild")
        return 0
    for d in rows:
        print("%s  %s  %s  bpm=%s  dur=%s  qc=%s  %s"
              % (d.get("date"), d.get("run_id"), d.get("title"),
                 d.get("bpm"), d.get("duration"),
                 d.get("qc_verdict") or "-", d.get("style")))
    return 0
