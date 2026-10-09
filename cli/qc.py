# -*- coding: utf-8 -*-
"""songforge qc —— 生成后验收。

    songforge qc --run <run_id>              验收某次运行产出的所有 FLAC
    songforge qc --run <run_id> --baseline   额外跑 8 首已认可老歌作对照
    songforge qc --file <flac> --dur 165     直接验收单个文件
    songforge qc --baseline-only --save-baseline
                                             校准一次并把软阈值固化到
                                             songforge/qc_baseline.json

设计约束：主 CLI 是零重依赖的（只吃标准库），librosa/torch/demucs/whisper
全在 ComfyUI venv 里。所以本命令只做三件事：
    1. 组 job（纯 dict/json）
    2. 用 venv python 起一个子进程跑 qc_worker.py
    3. 渲染报告 + 落盘 qc.json
测量逻辑一行都不在这里，避免主链路被音频栈拖死。

坑位记录：
    - 不要试图在主进程 import librosa。主入口跑在系统 Python（3.10，无 librosa），
      硬 import 会让整个 songforge 直接不可用。
    - worker 输出的 JSON 前面有进度打印，用 ###JSON### 哨兵切分，
      不要直接 json.loads 整个 stdout。
    - venv python 路径要能自动定位：先看 SONGFORGE_QC_PYTHON，再找
      ComfyUI 根同级/上级常见的 comfyui-venv，找不到就明确报错而不是静默跳过。
    - 老歌基线必须带歌词正文（qc_baseline_old.json），否则 coverage 恒为 0，
      派生出的"召回 >= 0%"是假放行，比没有基线更危险。
"""
import argparse
import glob
import json
import os
import subprocess

from ..config import loader
from ..core import catalog as catmod
from ..core import paths as pmod

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKER = os.path.join(ROOT, "songforge", "tools", "qc_worker.py")


def _cfg():
    return loader.load({})


def _baseline_paths(cfg=None):
    cfg = cfg or _cfg()
    base = pmod.under_workflows(
        cfg["paths"].get("baseline_dir") or "songforge/config/baseline")
    return (os.path.join(base, "qc_baseline_old.json"),
            os.path.join(base, "qc_baseline.json"),
            os.path.join(base, "style_ref.json"))


def _comfy_root(cfg=None):
    return pmod.comfy_root(cfg=cfg or _cfg())


def find_qc_python(cfg=None):
    """定位带 librosa/torch/demucs 的那个 python。"""
    cfg = cfg or _cfg()
    configured = (cfg.get("paths") or {}).get("qc_python")
    if configured and os.path.isfile(configured):
        return os.path.abspath(configured)

    env = os.environ.get("SONGFORGE_QC_PYTHON")
    if env and os.path.isfile(env):
        return os.path.abspath(env)

    cands = []
    drive = os.environ.get("SystemDrive", "C:")
    cands += [
        r"C:\comfyui-venv\Scripts\python.exe",
        os.path.join(drive, r"\comfyui-venv\Scripts\python.exe"),
    ]
    root = _comfy_root(cfg)
    for base in (os.path.dirname(root), os.path.dirname(os.path.dirname(root)),
                 os.path.join(root, "venv"), root):
        cands.append(os.path.join(base, "Scripts", "python.exe"))
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if p.lower().endswith("scripts") and os.path.isdir(p):
            cands.append(os.path.join(p, "python.exe"))

    for c in cands:
        if os.path.isfile(c):
            try:
                r = subprocess.run(
                    [c, "-c", "import librosa,torch,demucs,soundfile"],
                    capture_output=True, timeout=180)
                if r.returncode == 0:
                    return os.path.abspath(c)
            except (OSError, subprocess.SubprocessError):
                continue
    return None


def _normalize_run_id(run):
    run = (run or "").strip().rstrip("/\\")
    if not run:
        return run
    cand = run
    if cand.endswith("manifest.json"):
        cand = os.path.dirname(cand)
    if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "manifest.json")):
        return os.path.basename(os.path.abspath(cand))
    return os.path.basename(run.replace("/", os.sep))


def _read_manifest(run_id):
    cfg = _cfg()
    rid = _normalize_run_id(run_id)
    runs = pmod.under_workflows(cfg["paths"]["runs_dir"])
    rundir = catmod.find_run_dir(runs, rid)
    if not rundir:
        # 兼容旧路径 songforge/runs/<id>
        legacy = os.path.join(ROOT, "songforge", "runs", rid, "manifest.json")
        if os.path.isfile(legacy):
            with open(legacy, encoding="utf-8") as f:
                return json.load(f), os.path.dirname(legacy)
        return None, None
    with open(os.path.join(rundir, "manifest.json"), encoding="utf-8") as f:
        return json.load(f), rundir


def _baseline_items():
    base_old, _, _ = _baseline_paths()
    if not os.path.isfile(base_old):
        print("[错误] 缺少 %s" % base_old)
        print("       先跑 tools/make_baseline.py 从 remake_v5.py 抽歌词基线。")
        return []
    with open(base_old, encoding="utf-8") as f:
        return json.load(f).get("items", [])


def _old_files(music):
    out = []
    for it in _baseline_items():
        p = os.path.join(music, it["file"])
        if os.path.isfile(p):
            out.append(dict(path=p, title=it["title"], group="old",
                            lyrics=it["lyrics"], bpm=it.get("bpm"),
                            duration=it.get("duration")))
        else:
            print("  [skip] 基线缺失 %s" % it["file"])
    return out


def cmd_qc(args):
    cfg = _cfg()
    base_old, base_json, style_ref = _baseline_paths(cfg)
    music = args.music_dir or pmod.music_dir(cfg)
    ref = args.ref or os.path.join(
        _comfy_root(cfg), "input", "voice_samples", "materials_D60927.wav")

    files = []
    rundir = None
    if args.run:
        man, rundir = _read_manifest(args.run)
        if not man:
            print("找不到 run: %s" % args.run)
            return 2
        names = []
        for r in man.get("results", []):
            for fn in r.get("files") or []:
                names.append(fn)
        if not names:
            names = glob.glob("%s_s*_00001.flac"
                               % (man.get("title") or args.run))
        lyr = ""
        lp = os.path.join(rundir, "lyrics.txt")
        if os.path.isfile(lp):
            with open(lp, encoding="utf-8") as f:
                lyr = f.read()
        for fn in names:
            base = os.path.basename(fn)
            hit = None
            for cand in (fn, os.path.join("MyMusic", fn),
                         os.path.join(music, base)):
                if os.path.isabs(cand) and os.path.isfile(cand):
                    hit = cand
                    break
                if os.path.isfile(cand):
                    hit = cand
                    break
            if hit is None:
                hit = os.path.join(music, base)
            files.append(dict(
                path=hit, title=man.get("title"), style=man.get("style", ""),
                group="new", duration=man.get("duration"),
                bpm=man.get("bpm"), intro_bars=man.get("intro_bars", 8),
                lang=man.get("language", "zh"),
                lyrics=lyr))
        if not files:
            print("run %s 没有可验收的音频" % args.run)
            return 2

    for fn in args.file or []:
        files.append(dict(path=fn, title=os.path.splitext(
            os.path.basename(fn))[0], group="new",
            duration=args.dur, bpm=args.bpm,
            intro_bars=args.intro_bars, lang=args.lang, lyrics=""))

    if args.baseline or args.baseline_only:
        files = _old_files(music) + files
    if not files:
        print("没有要验收的文件")
        return 2

    missing = [f["path"] for f in files if not os.path.isfile(f["path"])]
    if missing and not args.allow_missing:
        for m in missing:
            print("  [FAIL] 文件不存在 %s" % m)
        return 2

    py = find_qc_python(cfg)
    if not py:
        print("[错误] 找不到带 librosa/torch/demucs 的 Python。")
        print("       用 config/local.json 的 qc_python 或 SONGFORGE_QC_PYTHON 指定")
        return 2
    print("QC python  %s" % py)

    _cache = pmod.under_workflows(cfg["paths"]["qc_cache_dir"])
    pmod.ensure_dir(_cache)

    job = dict(comfy_root=_comfy_root(cfg), music_dir=music, ref=ref,
               files=files, asr=not args.no_asr,
               cache_dir=_cache,
               style_ref=style_ref if os.path.isfile(style_ref) else None,
               # 坑位：--save-baseline 必须不让 worker 读旧基线。
               baseline_file=None if args.save_baseline else
               (base_json if os.path.isfile(base_json) else None))

    env = dict(os.environ)
    env["SONGFORGE_COMFY_ROOT"] = _comfy_root(cfg)
    env["PYTHONIOENCODING"] = "utf-8"
    print("测量中（首次要加载 demucs + whisper + CAMPPlus，请耐心）...")
    if not job["style_ref"]:
        print("[提示] 没有 style_ref.json，风格判定不会运行"
              "（先跑 tools/make_style_ref.py）")
    cmd = [py, WORKER, json.dumps(job, ensure_ascii=False)]
    try:
        r = subprocess.run(cmd, capture_output=True, env=env, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print("[错误] QC 超时（%ds）" % args.timeout)
        return 2

    out = r.stdout.decode("utf-8", "replace")
    if "###JSON###" not in out:
        print(out[-2000:])
        print(r.stderr.decode("utf-8", "replace")[-2000:])
        print("[错误] worker 没有产出结果")
        return 2
    rep = json.loads(out.split("###JSON###", 1)[1].strip())

    if args.save_baseline:
        pmod.ensure_dir(os.path.dirname(base_json))
        with open(base_json, "w", encoding="utf-8") as f:
            json.dump(dict(base=rep["baseline"], soft=rep["soft"],
                           note="由 songforge qc --baseline-only --save-baseline "
                                "在 8 首已认可老歌上校准，不要手改"), f,
                      ensure_ascii=False, indent=1)
        print("\n基线已固化: %s" % base_json)

    return _render(rep, args, rundir=rundir, cfg=cfg)


def _render(rep, args, rundir=None, cfg=None):
    base, soft, verd = rep["baseline"], rep["soft"], rep["verdicts"]
    rows = rep["rows"]
    cfg = cfg or _cfg()

    print("\n== 基线（%d 首已认可老歌）==" % base.get("n_old", 0))
    if base.get("n_old"):
        print("  歌词召回 中位 %.0f%%   声线最近邻 中位 %.3f   自相似 %.3f"
              % (base.get("coverage", 0) * 100, base.get("voice_floor", 0),
                 base.get("voice_self", 0)))
        print("  动态 %.1f dB   质心 %.0f Hz   F0漂移 P90 %.1f 半音"
              % (base.get("dynamic_db", 0), base.get("centroid", 0),
                 base.get("f0_drift_st", 0)))
        print("  放行线：召回>=%.0f%%  声线NN>=%.3f  自相似>=%.3f  动态>=%.1fdB"
              % (soft.get("coverage", 0) * 100, soft.get("voice_nn", 0),
                 soft.get("voice_self", 0), soft.get("dyn_lo", 0)))
        print("        质心>=%.0fHz  F0漂移<=%.1f半音  速度漂移<=%.1f%%"
              % (soft.get("centroid_lo", 0), soft.get("f0_drift_st_max", 0),
                 soft.get("tempo_drift_max", 0) * 100))
        print("        实测速度 vs 设定 BPM 容差 ±%.0f%%"
              % (18.0,))
    else:
        print("  [WARN] 没有老歌基线，本次只跑硬阈值，软阈值不可信")

    print("\n== 风格分类 ==")
    sr = rows[0].get("style_trusted") if rows else None
    print("  语料可信度：%s" % ("可信（分组留一通过）" if sr
                             else ("不可信，只当参考" if sr is not None
                                   else "未运行")))
    print("%-22s %-7s %6s %-22s %-7s"
          % ("file", "判定", "置信", "最像历史作品", "其风格"))
    for r in rows:
        if "error" in r or not r.get("style_fam"):
            continue
        print("%-22s %-7s %5.0f%% %-22s %-7s"
              % (r["file"][:22], r["style_fam"],
                 r.get("style_conf", 0) * 100, r.get("style_like", "?")[:22],
                 r.get("style_like_family", "")))

    print("\n== 明细 ==")
    print("%-22s %6s %6s %6s %6s %6s %7s %7s %7s %6s"
          % ("file", "dur", "peak", "lufs", "dyn", "cent", "bpm设定", "bpm实测",
             "人声进", "泄漏"))
    for r in rows:
        if "error" in r:
            print("%-22s  ERROR %s" % (r["file"][:22], r["error"][:70]))
            continue
        want = (r.get("expect") or {}).get("bpm") or 0
        print("%-22s %6.1f %6.3f %6.1f %6.1f %6.0f %7s %7.1f %7s %6d"
              % (r["file"][:22], r["duration"], r["peak"], r["lufs"],
                 r["dynamic_db"], r["centroid_hz"],
                 want or "-", r.get("tempo_med", 0),
                 "-" if r.get("vocal_onset") is None
                 else "%.1f" % r["vocal_onset"],
                 r.get("leak_segs", 0)))
    for r in rows:
        if r.get("leaks"):
            print("\n  指令泄漏明细 %s：" % r["file"][:22])
            for lk in r["leaks"]:
                # 命中证据优先打实证短语；只有短语为空（纯集合重合）
                # 才退回打指令词命中率。
                hit = lk.get("leak_phrase") or "指令词命中 %3.0f%%" % (
                    (lk.get("instr_hit") or 0) * 100)
                print("    t0=%6.2fs  %s  %s"
                      % (lk["t0"], hit, lk.get("text", "")))

    print("\n== 声线 / 歌词 ==")
    print("%-22s %-14s %8s %7s %7s %6s %6s %7s"
          % ("file", "最近邻老歌", "NN相似", "自相似", "cov", "f0中位",
             "f0漂移", "t漂移"))
    for r in rows:
        if "error" in r:
            continue
        print("%-22s %-14s %8.3f %7.3f %6.1f%% %6.1f %5.1fst %6.1f%%"
              % (r["file"][:22], (r.get("voice_nn") or "-")[:14],
                 r.get("voice_nn_sim", 0), r.get("voice_self", 0),
                 r.get("coverage", 0) * 100, r.get("f0_med", 0),
                 r.get("f0_drift_st", 0), r.get("tempo_drift", 0) * 100))

    print("\n== 结论 ==")
    npass = 0
    for v in verd:
        print("  [%s] %s" % (v["verdict"], v["file"]))
        if v["verdict"] == "PASS":
            npass += 1
        for w in v.get("why", []):
            print("        - %s" % w)

    outp = args.out
    if not outp:
        if rundir:
            base_dir = rundir
        elif args.run:
            runs = pmod.under_workflows(cfg["paths"]["runs_dir"])
            found = catmod.find_run_dir(runs, _normalize_run_id(args.run))
            base_dir = found or pmod.tmp_today(cfg)
        else:
            base_dir = pmod.tmp_today(cfg)
        outp = os.path.join(base_dir, "qc.json")
    os.makedirs(os.path.dirname(outp) or ".", exist_ok=True)
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print("\n报告: %s" % outp)

    if args.run and verd:
        new_names = set()
        for row in rows:
            if row.get("group") == "new" or (
                    row.get("group") is None and "error" not in row):
                new_names.add(os.path.basename(str(row.get("file") or "")))
        new_v = [v for v in verd
                 if os.path.basename(str(v.get("file") or "")) in new_names] \
            or verd
        n_ok = sum(1 for v in new_v if v.get("verdict") == "PASS")
        if n_ok == len(new_v):
            verdict_str = "PASS"
        elif n_ok == 0:
            verdict_str = "FAIL"
        else:
            verdict_str = "PARTIAL"
        try:
            catmod.update_qc_verdict(cfg, _normalize_run_id(args.run),
                                     verdict_str)
        except Exception as e:
            print("[WARN] catalog 更新失败: %s" % e)

    if not verd:
        print("本次只有基线校准，没有待验收新歌 -> 退出码 0")
        return 0
    return 0 if npass == len(verd) else 1


def add_qc(sub):
    q = sub.add_parser("qc", help="生成后验收（时长/声线/歌词/开头器乐/音频质量）")
    q.add_argument("--run", help="验收某次 run 产出的 FLAC")
    q.add_argument("--file", action="append", help="直接验收文件，可多次")
    q.add_argument("--baseline", action="store_true",
                   help="额外测 8 首已认可老歌，一并派生软阈值")
    q.add_argument("--baseline-only", dest="baseline_only",
                   action="store_true", help="只跑老歌基线（校准用）")
    q.add_argument("--save-baseline", dest="save_baseline",
                   action="store_true", help="把本次派生阈值固化到 qc_baseline.json")
    q.add_argument("--music-dir", dest="music_dir")
    q.add_argument("--ref", help="声线参考音频")
    q.add_argument("--dur", type=int, help="期望时长（秒），用于时长校验")
    q.add_argument("--bpm", type=int)
    q.add_argument("--intro-bars", dest="intro_bars", type=int, default=8)
    q.add_argument("--lang", dest="lang", default="zh",
                   choices=["zh", "en"],
                   help="歌词语种，决定 ASR 语种与召回口径。"
                        "英文歌必须传 en，否则 ASR 按中文解码、召回恒为 0。")
    q.add_argument("--no-asr", dest="no_asr", action="store_true")
    q.add_argument("--timeout", type=int, default=7200)
    q.add_argument("--allow-missing", dest="allow_missing", action="store_true")
    q.add_argument("--out")
    q.set_defaults(func=cmd_qc)
    return q