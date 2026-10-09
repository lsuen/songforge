# -*- coding: utf-8 -*-
"""全链路自检（不连 ComfyUI 生成）。"""
import os
import re
import sys

from ..config import loader
from ..core import paths as pmod
from ..core import workflow as wfmod
from ..lyrics import meter
from ..rules import ironlaw
from .assemble import assemble, do_validate
from .common import ROOT, now_id

# 第三张专辑的低音吟唱 demo 歌词，稀疏档与 chill 集成自测都读它。
# 素材库迁到 _archive/lyrics_corpus 后，selftest 仍读包内 probe。
# 旧文件名 02_InstLowChill.txt 已不在树里，以现有 03 为准。
_CHILL_DEMO = os.path.join(
    ROOT, "songforge", "lyrics", "probe", "03_InstLowChill6.txt")


def _t_runid():
    """运行 ID 必须唯一。

    旧实现只到秒，连跑三首会落进同一个 runs 目录，lyrics.txt / tags.txt /
    manifest.json 互相覆盖，dry-run 记录直接失效。这里既验唯一性，
    也验真实目录里连续三次分配不冲突。
    """
    layout = pmod.path_layout(loader.load({}))
    scratch = os.path.join(layout["tmp"], "selftest_runid")
    if os.path.isdir(scratch):
        for f in os.listdir(scratch):
            p = os.path.join(scratch, f)
            if os.path.isdir(p):
                os.rmdir(p)
        os.rmdir(scratch)
    os.makedirs(scratch)
    try:
        ids = [now_id("t", scratch) for _ in range(3)]
        # 目录必须已被原子占位，防止后续写入互相覆盖
        assert all(os.path.isdir(os.path.join(scratch, i)) for i in ids), \
            "运行目录未占位: %s" % ids
    finally:
        for f in os.listdir(scratch):
            os.rmdir(os.path.join(scratch, f))
        os.rmdir(scratch)
    assert len(set(ids)) == 3, "运行 ID 冲突: %s" % ids

    # 模拟目录已存在时必须自增，而不是覆盖旧记录
    base = now_id("t", None)
    assert len(base) > len("t-20261004-041513"), \
        "运行 ID 缺少微秒段: %s" % base
    root2 = os.path.join(layout["tmp"], "selftest_runid2")
    if os.path.isdir(root2):
        for f in os.listdir(root2):
            os.rmdir(os.path.join(root2, f))
        os.rmdir(root2)
    os.makedirs(root2)
    try:
        # now_id 已原子占位，这里再取一次必须自动自增，而不是复用旧目录
        first = now_id("t", root2)
        second = now_id("t", root2)
    finally:
        for d in os.listdir(root2):
            os.rmdir(os.path.join(root2, d))
        os.rmdir(root2)
    assert first != second, "目录已存在时未自增: %s / %s" % (first, second)
    return [("运行 ID 唯一", "连跑三次 %s ~ %s 互不冲突" % (ids[0], ids[-1]))]


def _t_path_layout():
    """配置里的目录键必须能解析，且与 DEFAULTS 对齐。"""
    cfg = loader.load({})
    p = cfg["paths"]
    for key in ("runs_dir", "tmp_dir", "lyrics_dir", "qc_cache_dir",
                "style_cache_dir", "output_prefix", "baseline_dir",
                "catalog_file"):
        assert key in p and p[key], "paths.%s 缺失" % key
    layout = pmod.path_layout(cfg)
    runs_n = layout["runs"].replace("\\", "/")
    tmp_n = layout["tmp"].replace("\\", "/")
    assert runs_n.endswith("songforge/data/runs"), "runs 路径异常: %s" % runs_n
    assert tmp_n.endswith("songforge/data/tmp"), "tmp 路径异常: %s" % tmp_n
    assert os.path.isdir(layout["baseline"]), "baseline 目录缺失: %s" % layout["baseline"]
    for name in ("qc_baseline.json", "qc_baseline_old.json", "style_ref.json"):
        assert os.path.isfile(os.path.join(layout["baseline"], name)), \
            "缺少基线文件 %s" % name
    return [("目录布局", "data/runs|tmp + baseline + catalog 已从 config.paths 解析")]


def _t_paths():
    root = pmod.comfy_root()
    assert os.path.isdir(os.path.join(root, "models", "checkpoints")), \
        "ComfyUI 根目录推导错误: %s" % root
    return [("ComfyUI 根推导", root)]


def _t_config_priority():
    os.environ["SONGFORGE_SHIFT"] = "4.4"
    try:
        cfg = loader.load({})
        assert abs(cfg["sampler"]["shift"] - 4.4) < 1e-6, "环境变量未生效"
        cfg = loader.load({"sampler.shift": 6.0})
        assert abs(cfg["sampler"]["shift"] - 6.0) < 1e-6, "命令行未覆盖环境变量"
    finally:
        os.environ.pop("SONGFORGE_SHIFT", None)
    base = loader.load({})["sampler"]["shift"]
    assert abs(base - 5.5) < 1e-6, "默认 shift 被污染: %s" % base
    return [("配置优先级 命令行>环境>配置", "shift 5.5 -> 4.4 -> 6.0")]


def _t_model_locked():
    cfg = loader.load({})
    ck = cfg["model"]["checkpoint"]
    assert ck == "ace_step_1.5_turbo_swl_fused.safetensors", "checkpoint 被换: %s" % ck
    assert cfg["model"]["lora"] == "swl_voice_best_ep257.safetensors", "LoRA 被换"
    assert ck not in cfg["model"]["denied_checkpoints"], "当前模型在禁用名单"
    return [("模型锁定", ck + " + " + cfg["model"]["lora"])]


def _t_ironlaw():
    tags = ironlaw.build_tags("Chinese pop")
    for kw in ("swlvoice", "8 bar instrumental intro hook", "no opening vocals",
               "no pure vocal intro", "no humming"):
        assert kw in tags, "tags 缺少 %s" % kw
    lyrics = ironlaw.build_lyrics(
        [("[verse]", "第一句歌词\n第二句歌词")], "kick and snare", bars=8)
    assert lyrics.lstrip().startswith("[intro]"), "未强制器乐前奏"
    assert "[Instrumental: 8 bar" in lyrics, "前奏未写明 8 小节"
    assert not ironlaw.check_rules(lyrics, tags), ironlaw.check_rules(lyrics, tags)
    # 缺前奏必须被拦
    no_intro = "[verse]\n没有前奏直接唱"
    assert ironlaw.check_rules(no_intro, tags), "缺器乐前奏未被拦截"
    # 中文结构标记必须被拦
    marked = lyrics.replace("[verse]", "[主歌]")
    assert ironlaw.check_rules(marked, tags), "中文结构标记未被拦截"
    return [("铁律注入", "8 小节器乐前奏 + 反人声条款齐备，缺前奏/中文标记均被拦")]


def _t_sparse_profile():
    """稀疏档必须真的放行"只有几句英文语气词"的写法，且仍然拦得住异常值。

    背景：第三张专辑人声压过音乐，根因之一是 default 密度档按"人声填满全曲"
    反推（175s/124BPM 下限 254 音节），把 84 音节的低音吟唱判成"歌词过少"，
    于是每次都被迫写满整首，人声自然盖过器乐。sparse 档就是为了给出
    "可唱小节只占 25%" 的合法区间。
    """
    cfg = loader.load({})["lyrics"]
    sparse = meter.pick(cfg, "sparse")
    assert sparse["sing_bar_ratio"] < cfg["sing_bar_ratio"], \
        "sparse 档可唱小节占比没有低于 default，音乐主导无从体现"
    assert sparse["ideal_line_syllable"][0] < cfg["ideal_line_syllable"][0], \
        "sparse 档句子下限没有放宽，mmm / ooh 这类短句仍会被拦"

    dur, bpm = 175, 124
    _, lo, hi = meter.target_words(dur, bpm, sparse)
    # 直接用真实 demo 歌词做样本，而不是抄一份内联的：
    # 抄的内联文本一漂移，测试就测不到真正要生成的那份文件了。
    probe = _CHILL_DEMO
    assert os.path.exists(probe), "找不到低音 demo 歌词 %s" % probe
    with open(probe, encoding="utf-8") as f:
        body = f.read()
    syl = meter.syllable(meter.only_lyrics(body))
    assert meter.check(body, dur, bpm, cfg)[0], \
        "default 档居然放行了稀疏歌词，档间没有区分度"
    errs, st = meter.check(body, dur, bpm, sparse)
    assert not errs, "sparse 档误杀了低音语气词写法: %s" % errs
    assert lo <= syl <= hi, \
        "sparse 放行区间 %d~%d 与实测 %d 音节不一致" % (lo, hi, syl)

    # 过短仍要拦：sparse 不是"无限制放宽"
    thin = ("[intro]\n[Instrumental: 8 bar]\n[verse]\nmmm\nooh\n")
    assert any("过少" in e for e in meter.check(thin, dur, bpm, sparse)[0]), \
        "sparse 档放过短歌词"

    # 过多仍要拦：写成满首人声时 sparse 也必须报错，否则会退回旧病
    fat = ("[intro]\n[Instrumental: 8 bar]\n[verse]\n"
           + "\n".join(["mmm mmm low and slow"] * 90))
    assert any("过多" in e for e in meter.check(fat, dur, bpm, sparse)[0]), \
        "sparse 档放过多歌词，音乐主导会被稀释"
    return [("稀疏密度档",
             "sparse 放行 %d~%d 音节（default 拒绝同一份），过少过多仍被拦"
             % (lo, hi))]


def _t_instrumental():
    """纯器乐模式的标签必须自洽，且不与风格字典的唱法描述对撞。"""
    tags = ironlaw.build_tags(
        style="English melodic future bass", instruments="huge supersaw lead",
        mood="vast uplifting EDM",
        extra="male low whisper vocal, hushed and intimate",
        instrumental=True)
    # 反人声条款必须在
    for kw in ("no vocals anywhere", "instrumental only", "no voice at all",
               "no humming", "no choir", "no vocal sample", "swlvoice",
               "8 bar instrumental intro hook"):
        assert kw in tags, "纯器乐 tags 缺少 %s" % kw
    # 关键回归点：风格字典的唱法描述绝不能混进来跟 "no vocals" 对撞。
    for bad in ("whisper vocal", "sparse English words", "no belting"):
        assert bad not in tags, "纯器乐 tags 混入了唱法描述: %s" % bad
    # 器乐正向要求必须在（这里只校验铁律常量层，风格字典自带的配器词不算）
    for kw in ("long instrumental passages", "extended instrumental breakdown",
               "huge powerful drop", "instrumental sections are the main focus",
               "drums and bass carry the track"):
        assert kw in tags, "纯器乐 tags 缺少器乐主导描述 %s" % kw
    # 绝不能出现把器乐段填满的条款（这会让模型加人声）
    assert "no long empty instrumental" not in tags, \
        "纯器乐 tags 含 no long empty instrumental，会逼模型填人声"
    assert "no long empty instrumental" not in ironlaw.RULE_CHILL_MEMO, \
        "吟唱 tags 含 no long empty instrumental，会逼模型填人声"
    # 前奏不能是"先器乐后人声"那句：纯器乐里它等于命令模型把人声加回来
    assert "vocals enter only after" not in tags, \
        "纯器乐 tags 仍含 'vocals enter only after the instrumental hook'"
    assert "no breakdown" not in tags, \
        "纯器乐 tags 含 no breakdown，与 extended instrumental breakdown 冲突"
    # 歌词体必须是纯结构标记，没有可唱正文
    lyrics = ironlaw.build_inst_lyrics("huge supersaw", bars=8)
    assert lyrics.lstrip().startswith("[intro]"), "纯器乐歌词未以 [intro] 开头"
    body = meter.only_lyrics(lyrics)
    assert body.strip() == "", "纯器乐歌词体出现可唱正文: %r" % body[:80]
    assert meter.syllable(body) == 0, "纯器乐歌词体音节数非 0"
    # 结构段名不能是 [verse] / [chorus]：ACE-Step 见到这两个标记就会按
    # "有人唱的段落"来铺，即使下面只挂 [inst] 也容易把人声请回来。
    for bad_tag in ("[verse]", "[chorus]"):
        assert bad_tag not in lyrics.lower(), \
            "纯器乐歌词体含 %s，会重新诱导人声" % bad_tag
    for good_tag in ("[drop]", "[breakdown]", "[build]"):
        assert good_tag in lyrics.lower(), "纯器乐歌词体缺少 %s 段" % good_tag
    # 结构铁律照旧生效
    assert not ironlaw.check_rules(lyrics, tags), ironlaw.check_rules(lyrics, tags)
    # 密度闸门必须让行纯器乐
    cfg = loader.load({})
    errs, _ = do_validate(lyrics, tags, cfg,
                          {"duration": 175, "bpm": 124}, {}, strict=False,
                          instrumental=True)
    assert not errs, "纯器乐被密度闸门拦下: %s" % errs
    # chill 模式的稳定条款也不能再要求 no breakdown
    chill_tags = ironlaw.build_tags(style="future bass",
                                    extra="male low whisper vocal",
                                    chill_vocal=True)
    assert "no breakdown" not in chill_tags, \
        "chill tags 含 no breakdown，与 quiet breakdown 冲突"
    # 声线稳定性约束已被实测证明是反效果：标签里每多一句谈人声，
    # 人声越从 0 秒就进。chill 现在只保留乐器/调性的稳定性要求。
    assert "consistent vocal timbre" not in chill_tags, \
        "chill tags 又开始强调人声稳定性，实测会退回 0 秒开唱"
    assert "consistent instrumentation in every section" in chill_tags, \
        "chill tags 丢了配器稳定性约束"
    return [("纯器乐模式", "反人声标签自洽、无唱法对撞、无 no breakdown、"
                          "结构体不诱发人声、密度闸门让行")]


def _t_chill_delay():
    """chill 模式：验证标签与结构两侧自洽（不退化成纯器乐，也不退回 0 秒开唱）。

    五轮实测踩出来的结论：
        chill 1  8 小节前奏 + "vocals enter only after…"
                 -> vocals stem 从 0.0 秒就有能量
        chill 2  标签加长到 16 小节 + "first 20 seconds are entirely instrumental"
                 + "the human voice does not enter until the second drop"
                 -> 第一句仍然落 0.0 秒，标签侧时间约束全部无效
        chill 3  结构重排 + 强人声描述
                 -> 稀疏低音语气词，能量 +45～50 dB，歌词重合 0.98
                 （当前唯一确认真唱的一版）
        chill 4  标签彻底不讨论人声（复用纯器乐反人声条款）
                 -> 人声归零，等同纯器乐
        chill 5  只保留音色质感，删掉密度/时机描述
                 -> 人声同样归零。此前把 ASR 的 4 段当成"少量真唱"是误判：
                    文本 "oh new healing / lömpe Minor" 歌词里根本没有，
                    能量只 +11 dB、ASR 覆盖率 100%，是持续 pad 幻听。

    关键结论：标签侧写时长/时机对人声起点零作用；唯一旋钮是
    "密度与时机类人声描述的多少"，而它降下去不是"延后"而是"消失"。
    所以自测只能守住"结构占位存在 + 标签不退回纯器乐"，
    人声起点必须靠音频 QC（ab_vocal_curve.py / ab_is_real_voice.py）判定。
    """
    tags = ironlaw.build_tags(style="English melodic future bass",
                              instruments="huge supersaw lead",
                              mood="vast uplifting emotional EDM",
                              extra="male low whisper vocal, "
                                    "hushed and intimate, sparse English words",
                              chill_vocal=True)
    # 人声仍然允许存在，不能把 chill 误改成纯器乐
    for kw in ("male low whisper vocal", "hushed and intimate",
               "low register vocal texture", "soft breathy phrases",
               "instrumental sections are the main focus",
               "long instrumental passages",
               "instrumental breakdown and instrumental drop sections"):
        assert kw in tags, "chill tags 缺少器乐主导描述 %s" % kw
    # 人声禁落器乐段
    for kw in ("no singing in any breakdown", "no singing over the first drop",
               "no long vocal phrases", "no choir"):
        assert kw in tags, "chill tags 缺少段落归属约束 %s" % kw
    # chill 不能退回普通前奏规则：那句只说"稍后进人声"，实测拦不住
    assert "vocals enter only after the instrumental hook has played" not in tags, \
        "chill tags 退回 RULE_INTRO，实测 0 秒开唱"

    # ---- 五轮实测收敛出来的核心断言 ----
    # InstLowChill / 2 / 3 / 4 / 5 五版量出一条干净的梯度，证明 prompt 对
    # 人声的控制力是真实存在的，旋钮 = "标签里有多少句在描述人声"：
    #   chill 2  强人声描述 + 16 小节前奏
    #           -> ASR 23 段，voc/acc 0.483，人声压过音乐
    #   chill 3  结构重排 + 强人声描述
    #           -> 稀疏低音语气词，voc/acc 0.376，唯一确认真唱的一版
    #   chill 4  标签彻底不讨论人声（复用纯器乐反人声条款）
    #           -> 人声归零，等同纯器乐
    #   chill 5  只留音色质感，删掉密度/时机描述
    #           -> 人声同样归零（ASR 全是持续 pad 幻听）
    #
    # 时机类措辞必须继续禁掉：chill 2/3 都写了
    # "second half / enters late / first 20 seconds"，第一句全部落 0.0 秒。
    for bad in ("voice enters", "the human voice", "no voice in the first",
                "until the second drop", "in the second half of the track",
                "first 20 seconds"):
        assert bad not in tags.lower(), \
            "chill tags 又开始讨论人声进场时机（%s），实测会退回 0 秒开唱" % bad

    # 密度类措辞必须保留：这是 chill 3 唯一产生真唱的东西，
    # 删掉（chill 5）人声直接归零，不是延后而是消失。
    for kw in ("voice is sparse and low volume", "used only as sparse texture",
               "sparse english words", "mostly instrumental between phrases"):
        assert kw in tags.lower(), \
            "chill tags 丢了密度触发词 %s，实测会退回纯器乐" % kw

    # 风格字典的 extra 绝不能混进来（六句里五句在谈人声）。
    # 这里不再靠关键词黑名单（因为 chill 自己现在也用同几个密度词，
    # 黑名单会误伤），改用哨兵值：故意传一个只在 extra 里的短语，
    # 结果里出现就说明 extra 泄漏了。
    probe = "zzz-extra-leak-probe"
    tags_probe = ironlaw.build_tags(style="s",
                                    instruments="i", mood="m",
                                    extra=probe,
                                    chill_vocal=True)
    assert probe not in tags_probe.lower(), "chill 分支把风格字典 extra 混进来了"

    # 但也不能把人声彻底禁掉，否则就是纯器乐、不是吟唱。
    # 注意只匹配纯器乐专属的反人声条款：
    #   - 不能用裸 "no singing"："no singing in any breakdown" 含这个子串，
    #     那是吟唱要的人声约束。
    #   - 不能用 "instrumental only"：它来自 "intro is instrumental only"，
    #     那是前奏必须保留的约束。
    for kw in ("no vocals anywhere", "no voice at all", "no lead vocal",
               "no backing vocal", "this is a backing track",
               "drums bass synths chords and lead melodies only"):
        assert kw not in tags.lower(), \
            "chill tags 含纯器乐条款 %s，吟唱被人声禁令吞掉" % kw

    return [("吟唱标签梯度",
             "时机类措辞全部禁掉，密度触发词保留，"
             "风格字典 extra 未泄漏")]


def _t_groove_first_beat():
    """器乐前奏必须从第 0 秒就有底鼓和贝斯，不能是空场。

    这里有一个被实测推翻过的旧结论，必须留在注释里免得又走回去：
    上一轮认定"前 15 秒是空场"，依据是 ab_lowfreq.py 报的
    -68~-75 dBFS。那份数据是错的——它把滤波器的瞬态响应和单帧跳变
    当成了整段电平。ab_bands.py / ab_head_rhythm.py 复测（dBFS，
    head = 前 16 秒，mid = 16~32 秒）：

        GroovePure  head sub -66.0 / mid sub -55.8
                    head full -32.7 / mid full -25.0

    也就是说开头有音乐（full band 只低 7.7 dB，hat/snare 瞬态齐备），
    缺的只是 sub/kick。所以真正要修的是"前奏要有节奏组"，
    不是"开头别是空场"，而"空场"这个说法本身就是误判。

    修法（见 ironlaw.intro_rhythm_only）：前奏括号只保留节奏组，
    剔除 pad / riser / reverb / lead。这一条比堆禁令有效——
    GroovePure 已经证明四条禁令（no ambient intro / no pad only intro /
    no beatless intro / no white noise riser in the opening）全部无效。
    """
    for flag, kw in ((False, "instrumental"), (True, "chill")):
        tags = ironlaw.build_tags(style="English melodic future bass",
                                  instruments="huge supersaw lead",
                                  mood="vast uplifting emotional EDM",
                                  chill_vocal=flag,
                                  instrumental=not flag)
        for need in ("full four on the floor groove from the very first beat",
                     "kick drum, sub bass and walking bass on bar one",
                     "no pad only intro", "no beatless intro",
                     "no riser in the opening"):
            assert need in tags.lower(), \
                "%s tags 缺第一拍 groove 约束: %s" % (kw, need)

    # 普通有声歌曲也一并受益：RULE_INTRO 同样要保证鼓从第一拍进。
    assert "rhythmic drums from the very first beat" in \
        ironlaw.build_tags("test").lower(), "普通歌曲前奏丢了第一拍底鼓"

    return [("第一拍 groove",
             "chill/纯器乐都要求底鼓+贝斯从第 0 拍进，"
             "并点名禁掉 pad only / beatless / riser 空场信号")]


def _t_intro_instrument_filter():
    """前奏括号必须过滤掉 pad/riser/reverb/lead，只留节奏组。

    这是本轮真正起作用的改动。上一轮只在标签里堆禁令，GroovePure 实测
    无效（开头 sub 仍 -66.0 dBFS，中段 -55.8 dBFS），因为真正的病灶是
    歌词体那一行：
        [Instrumental: 8 bar instrumental intro hook, huge supersaw lead,
         sidechain compressed pad, punchy electro kick, crisp clap,
         trap snare, white noise riser, big reverb, melodic drop, ...]
    空场信号（lead/pad/riser/reverb）4 个，节奏组（kick/clap/snare）3 个，
    3:4 输了，模型就把前奏铺成 ambient。

    所以这里守三件事：
      1) 过滤后必须还留着 kick（不能把节奏组也误删成空括号）；
      2) pad / riser / reverb / lead 一个都不能留在前奏括号里；
      3) 标签侧的 instruments 段保持原样——drop 段本来就要 pad 和 lead，
         只过滤前奏括号，不能把整首歌的配器改掉。
    """
    src = ("huge supersaw lead, sidechain compressed pad, "
           "punchy electro kick, crisp clap, trap snare, "
           "white noise riser, big reverb, melodic drop")
    got = ironlaw.intro_rhythm_only(src)
    low = got.lower()

    assert "kick" in low, "前奏括号把底鼓也过滤掉了: %r" % got
    for bad in ("pad", "riser", "reverb", "supersaw", "lead", "noise"):
        assert bad not in low, "前奏括号残留空场信号 %s: %r" % (bad, got)

    # 加法兜底：kick 和 bass 两条必须显式出现在前奏括号里。
    # 上一轮漏了这一条，风格字典里一条贝斯都没写，过滤后只剩三条中高频，
    # 结果两版新音频开头 sub 比全曲峰值低 53.5 / 31.2 dB。
    assert "bass" in low, "前奏括号没有 bass：%r" % got

    # 空 / 中文 / 全被过滤掉 -> 必须回落到默认节奏组，不能留空括号
    for probe in ("", None, "ambient pad, huge supersaw lead, big reverb"):
        fb = ironlaw.intro_rhythm_only(probe)
        assert "kick" in fb.lower(), "前奏括号回落失败 %r -> %r" % (probe, fb)
        assert "bass" in fb.lower(), "回落值缺 bass %r -> %r" % (probe, fb)
        for bad in ("pad", "riser", "reverb", "supersaw", "lead"):
            assert bad not in fb.lower(), "回落值带空场信号 %s: %r" % (bad, fb)

    # 回落值不能是 DEFAULT_INSTRUMENT：它含 "chord stabs"，而 "chord stab"
    # 就在 _RHYTHM_DROP 里，回落会把刚剔除掉的空场信号塞回前奏。
    assert "stab" not in ironlaw.DEFAULT_INTRO_RHYTHM.lower(), \
        "前奏回落值含 chord stab，与过滤规则自相矛盾"

    # 标签侧不受影响：instruments 段必须还是原样
    tags = ironlaw.build_tags(style="s", instruments=src, mood="m",
                              instrumental=True)
    assert "white noise riser" in tags, \
        "过滤逻辑泄漏到了标签侧，drop 段会丢掉 riser"
    assert "sidechain compressed pad" in tags, \
        "过滤逻辑泄漏到了标签侧，drop 段会丢掉 pad"

    # 歌词体里前奏那一行必须既没有 riser/pad，又必须有 bass
    lyr = ironlaw.build_inst_lyrics(src, bars=8)
    head = lyr.split("\n")[1].lower()
    assert "riser" not in head and "pad" not in head, \
        "纯器乐歌词体前奏行仍带空场信号: %s" % head
    assert "bass" in head, "纯器乐歌词体前奏行缺 bass: %s" % head
    lyr2 = ironlaw.build_chill_lyrics([("[verse]", "ooh")], src, bars=8,
                                       total_bars=90)
    head2 = lyr2.split("\n")[1].lower()
    assert "riser" not in head2 and "pad" not in head2, \
        "吟唱歌词体前奏行仍带空场信号: %s" % head2
    assert "bass" in head2, "吟唱歌词体前奏行缺 bass: %s" % head2

    return [("前奏配器过滤",
             "前奏括号剔除 pad/riser/reverb/lead，并强制补齐 kick+bass，"
             "标签侧配器保持原样")]


def _t_chill_no_conflict():
    """chill 标签里绝不能出现和"低音英文语气词"直接冲突的禁令。

    这三条（no humming / no mm mm syllables / no oh oh syllables）第一次改
    chill 铁律就是为了解开这个冲突，加回来等于自己把要求唱的内容禁掉。
    """
    tags = ironlaw.build_tags(style="s", instruments="i", mood="m",
                              chill_vocal=True)
    for bad in ("no humming", "no mm mm syllables", "no oh oh syllables",
                "no droning sustained vowels"):
        assert bad not in tags.lower(), \
            "chill tags 含 %s，与要求的低音语气词直接冲突" % bad
    # 反过来，"稀疏低音语气词"必须真的在标签里，否则就退化成纯器乐了。
    for need in ("male low whisper vocal", "low register vocal texture",
                 "soft breathy phrases"):
        assert need in tags.lower(), "chill tags 丢了低音唱腔描述: %s" % need

    return [("吟唱无冲突",
             "no humming / no mm mm / no oh oh 三条禁令未回归")]


def _t_chill_integration():
    """chill 集成路径：assemble + check_rules 必须同时通过。

    上一轮 14/14 全绿却照样生成失败，原因是这个自测只调 build_tags()，
    没走 assemble() + check_rules()，于是标签与歌词头小节数不一致这种
    集成冲突根本测不出来。集成层的坑必须在这里测。
    """
    cfg = loader.load({})
    st = {"bpm": 124, "duration": 175, "key": "A minor",
          "style": "English melodic future bass",
          "instruments": "huge supersaw lead",
          "mood": "vast uplifting emotional EDM",
          "extra": "male low whisper vocal, sparse English words"}

    class _A(object):
        chill_vocal = True
        instrumental = False

    raw = open(_CHILL_DEMO, encoding="utf-8").read()
    tags2, lyrics2 = assemble(_A, cfg, st, raw, "selftest")
    errs = ironlaw.check_rules(lyrics2, tags2)
    assert not errs, "chill 集成校验不通过: %s" % errs

    # 结构核心断言：第一个有人唱的段之前，纯器乐段必须占满前半段。
    # 注意这只保证"器乐占位存在"，不保证人声起点被推后——实测两者无关。
    # 音频层是否真的晚进，必须用 ab_vocal_curve.py / ab_is_real_voice.py 验。
    lines = [l.strip() for l in lyrics2.split("\n") if l.strip()]
    vocal_tags = ("[verse]", "[chorus]", "[hook]", "[bridge]", "[pre-chorus]")
    first_v = next((i for i, l in enumerate(lines)
                    if l.lower() in vocal_tags), None)
    assert first_v is not None, "chill 结构体里找不到任何人声段"
    head = [l.lower() for l in lines[:first_v]]
    # 只数"纯器乐段"，不能把 [inst] 占位和段名一起算进去。
    # 旧写法把两者相加，一个 breakdown 组就被算成 2，报出"8 个器乐段"，
    # 实际上只有 4 组——测试描述在骗人，验收语义必须按组算。
    inst_secs = [h for h in head
                 if h in ("[breakdown]", "[build]", "[drop]", "[post-drop]")]
    n_inst = len(inst_secs)
    assert n_inst >= 4, \
        "chill 第一个器乐段之前只有 %d 组纯器乐段，人声会紧跟 intro 进来" % n_inst
    # 每个纯器乐段名后面必须真的挂 [inst]，否则模型会当成有人唱的段
    for i, h in enumerate(head):
        if h in ("[breakdown]", "[build]", "[drop]", "[post-drop]"):
            assert i + 1 < len(head) and head[i + 1] == "[inst]", \
                "chill 器乐段 %s 后面没挂 [inst]，会被当成可唱段" % h

    # 器乐占位不能重复：原始歌词里的 [inst] 段已经重排过，
    # 再留一遍会让人声提前回来
    assert head.count("[inst]") <= n_inst, "chill 结构体器乐占位异常重复"

    # 前奏小节数：标签与歌词头必须一致
    mh = re.search(r"\[instrumental:[^\]]*?(\d+)\s*bar", lyrics2, re.I)
    mt = re.search(r"(\d+)\s*bar instrumental intro", tags2.lower())
    assert mh and mt and mh.group(1) == mt.group(1), \
        "chill 标签 %s 小节与歌词头 %s 小节不一致" % (
            mt.group(1) if mt else "?", mh.group(1) if mh else "?")

    # 反向闸门：两者不一致必须被拦下
    short_tags = tags2.replace("%s bar instrumental intro" % mt.group(1),
                               "16 bar instrumental intro", 1)
    errs = ironlaw.check_rules(lyrics2, short_tags)
    assert any("必须一致" in e for e in errs), \
        "tags 与歌词头前奏小节数不一致未被拦截"

    # 前奏小节数缺失也必须被拦
    errs = ironlaw.check_rules(lyrics2, re.sub(r"\d+\s*bar instrumental",
                                               "instrumental", tags2))
    assert any("小节数" in e for e in errs), "tags 未写前奏小节数未被拦截"

    return [("吟唱结构占位",
             "人声段之前留了 %d 组纯器乐段（只管密度，不管起点），"
             "标签侧完全不讨论人声进场时机" % n_inst)]


def _t_lexicon():
    lex = loader.lexicon()
    assert "长板" in (lex["banned"].get("longboard") or []), "禁用词表缺 长板"
    good = "[intro]\n[Instrumental: 8 bar]\n[verse]\n我在街头双翘上起跳"
    assert not ironlaw.check_lexicon(good, lex), ironlaw.check_lexicon(good, lex)
    bad = "[intro]\n[verse]\n我戴着头盔冲下坡"
    errs = ironlaw.check_lexicon(bad, lex)
    assert errs, "禁用词未被拦截"
    return [("身份词典", "街头双翘通过 / 长板类禁用词 %d 条被拦" % len(errs))]


def _t_density():
    cfg = loader.load({})["lyrics"]
    st = loader.styles()["pop"]
    cap = meter.capacity(st["duration"], st["bpm"], cfg)
    assert cap["hi"] > cap["lo"] > 0, "容量区间异常"

    # 过短必须被拦
    too_thin = "[intro]\n[Instrumental: 8 bar]\n[verse]\n太短了"
    assert meter.check(too_thin, st["duration"], st["bpm"], cfg)[0], "过短未拦"

    # 过多必须被拦
    too_fat = "[intro]\n[Instrumental: 8 bar]\n[verse]\n" + \
        "\n".join(["这一句有十二个字刚刚好"] * 300)
    assert meter.check(too_fat, st["duration"], st["bpm"], cfg)[0], "过多未拦"

    # 按 target_words 反推合规歌词，必须放行（防止阈值过严误杀）
    mid, lo, hi = meter.target_words(st["duration"], st["bpm"], cfg)
    line = "这一句刚好十二个字的样子"      # 12 音节
    nlines = max(1, int(mid / 12))
    body = "\n".join([line] * nlines)
    ok = "[intro]\n[Instrumental: 8 bar]\n[verse]\n" + body
    errs, stt = meter.check(ok, st["duration"], st["bpm"], cfg)
    assert not errs, "按 target 写的合规歌词被误杀: %s" % errs
    return [("时长对应硬校验",
             "%ds/%dBPM 容量 %d~%d，目标 %d 字，过短/过多均被拦，合规放行"
             % (st["duration"], st["bpm"], cap["lo"], cap["hi"], mid))]


def _t_regression():
    """实歌回归：8 首用户已生成并认可的歌必须全部通过密度校验。

    这是阈值校准的锚点。原来按理论值（每拍2.6~4.4音节、每句下限7）设阈值，
    结果这 8 首全部被判"歌词过少"（填充率只有 26%~43%），说明阈值从未
    对齐过真实输出。任何阈值改动都必须先过这一关。
    """
    old_dir = r"c:\Users\孙文龙\.trae-cn\work\6ab523de391489c168050edc"
    if not os.path.isdir(old_dir):
        return [("实歌回归", "FAIL 找不到 remake_v5.py 目录")]
    sys.path.insert(0, old_dir)
    try:
        import remake_v5 as old
    except Exception as e:
        return [("实歌回归", "FAIL 无法导入 remake_v5 (%s)" % e)]

    cfg = loader.load({})["lyrics"]
    bad = []
    worst_pl = 99.0
    for title, seed, bpm, key, dur, inst in old.META:
        lyrics = old.build_song(title)[2]
        errs, st = meter.check(lyrics, dur, bpm, cfg)
        worst_pl = min(worst_pl, st["per_line"])
        if errs:
            bad.append("%s: %s" % (title, errs[0].split("：")[0]))
    if bad:
        return [("实歌回归", "FAIL %d/%d 首被误杀 -> %s"
                 % (len(bad), len(old.META), " | ".join(bad)))]
    assert worst_pl >= cfg["ideal_line_syllable"][0], \
        "实测最短平均句长 %.1f 已低于配置下限 %.1f，阈值与实歌脱节" % (
            worst_pl, cfg["ideal_line_syllable"][0])
    return [("实歌回归", "8 首已验证实歌全部通过（最短平均句长 %.1f 音节）"
             % worst_pl)]


def _t_parity():
    """与旧脚本 remake_v5.py 的 API prompt 逐字段对比。"""
    old_dir = r"c:\Users\孙文龙\.trae-cn\work\6ab523de391489c168050edc"
    sys.path.insert(0, old_dir)
    try:
        import remake_v5 as old
    except Exception as e:
        return [("旧脚本 parity", "跳过：无法导入 remake_v5 (%s)" % e)]

    title = "身体先听懂"
    owf, otags, olyrics = old.build_song(title)
    oapi = old.convert(owf)

    cfg = loader.load({})
    template = wfmod.load_template(cfg["paths"]["workflow_template"])
    m = [x for x in old.META if x[0] == title][0]
    _, oseed, obpm, okey, odur, oinst = m
    nwf = wfmod.build_workflow(
        template, title=title, tags=otags, lyrics=olyrics, seed=oseed,
        bpm=obpm, duration=odur, keyscale=okey, sampler=cfg["sampler"],
        encode=cfg["encode"], model=cfg["model"],
        output_prefix="MyMusic/parity_%s" % title, output_format="flac",
        language="zh", timesignature="4")
    napi = wfmod.render(nwf)

    if set(oapi) != set(napi):
        return [("旧脚本 parity", "FAIL 节点集不同 %s vs %s"
                 % (sorted(oapi), sorted(napi)))]

    diffs = []
    for nid in sorted(oapi, key=int):
        for k in sorted(set(oapi[nid]["inputs"]) | set(napi[nid]["inputs"])):
            a = oapi[nid]["inputs"].get(k)
            b = napi[nid]["inputs"].get(k)
            if k in ("tags", "lyrics"):
                # 新版 build_tags 措辞不同，只校验关键铁律都在
                for kw in ("swlvoice", "8 bar instrumental intro hook"):
                    if (kw in str(a)) != (kw in str(b)):
                        diffs.append("节点%s.%s 铁律不一致" % (nid, k))
                continue
            if k == "filename_prefix":
                continue          # 输出前缀本就故意不同
            if a != b:
                diffs.append("节点%s.%s: 旧=%r 新=%r" % (nid, k, a, b))
    if diffs:
        return [("旧脚本 parity", "FAIL %d 处不同：%s"
                 % (len(diffs), " | ".join(diffs[:6])))]
    return [("旧脚本 parity", "核心 API payload 与 remake_v5.py 等价（%d 节点；"
             "filename_prefix 与歌词措辞为有意差异）" % len(oapi))]


def _t_fallback():
    """fallback 资产必须仍在原位。"""
    root = pmod.comfy_root()
    need = [
        os.path.join(ROOT, "song_rules.py"),
        os.path.join(ROOT, "urbanhiphop-身体先听懂.json"),
        pmod.output_dir(root, "MyMusic"),
    ]
    missing = [p for p in need if not os.path.exists(p)]
    bak_root = os.path.join(ROOT, "bak")
    stamps = sorted(os.listdir(bak_root)) if os.path.isdir(bak_root) else []
    if missing:
        return [("fallback 资产", "FAIL 缺失 %s" % missing)]
    if not stamps:
        return [("fallback 资产", "FAIL bak 目录为空")]
    return [("fallback 资产", "song_rules.py / 模板 / output/MyMusic 就位，bak %d 份"
             % len(stamps))]


def _t_window():
    """target_words 报的区间必须与 check 实际放行窗口一致。

    这是最容易出"文档说能过、实际过不了"的地方：容量 [lo,hi] 是理论值，
    但 check 还叠加了填充率红线，真正生效的窗口必须两者取交。
    """
    cfg = loader.load({})["lyrics"]
    for name in ("pop", "punk", "urbanhiphop"):
        st = loader.styles()[name]
        dur, bpm = st["duration"], st["bpm"]
        mid, lo, hi = meter.target_words(dur, bpm, cfg)
        assert lo <= mid <= hi, "%s 目标字数 %d 不在放行区间 %d~%d" % (
            name, mid, lo, hi)

        cap = meter.capacity(dur, bpm, cfg)
        assert cap["lo"] <= hi and lo <= cap["hi"], \
            "%s 放行区间 %d~%d 超出理论容量 %d~%d" % (
                name, lo, hi, int(cap["lo"]), int(cap["hi"]))

        # 窗口两端必须真的被 check 拦/放，边界不能只存在于文档里
        line = "这一句刚好十二个字的样子"

        def build(n):
            return ("[intro]\n[Instrumental: 8 bar]\n[verse]\n"
                    + "\n".join([line] * n))

        n_mid = max(1, int(mid / 12))
        ok_errs, _ = meter.check(build(n_mid), dur, bpm, cfg)
        assert not ok_errs, "%s 按目标字数写的歌词被误杀: %s" % (name, ok_errs)

        # 少一个字就该报"过少"，多一个字就该报"过多"，容差必须小
        few = meter.check(build(max(1, int((lo - 30) / 12))), dur, bpm, cfg)[0]
        assert any("过少" in e for e in few), \
            "%s 低于放行下限却未报过少: %s" % (name, few)
        many = meter.check(build(int((hi + 30) / 12) + 1), dur, bpm, cfg)[0]
        assert any("过多" in e for e in many), \
            "%s 高于放行上限却未报过多: %s" % (name, many)
    return [("字数窗口自洽",
             "pop/punk/urbanhiphop 的 target 区间 == check 放行窗口，边界确实生效")]


def cmd_selftest(args):
    from .common import session_logger
    cfg = loader.load({})
    run, log = session_logger(args, cfg, "selftest")
    cases = [_t_runid, _t_paths, _t_path_layout, _t_config_priority,
             _t_model_locked, _t_ironlaw,
             _t_instrumental, _t_chill_delay, _t_groove_first_beat,
             _t_intro_instrument_filter, _t_chill_no_conflict,
             _t_chill_integration,
             _t_sparse_profile, _t_lexicon,
             _t_density, _t_window, _t_regression, _t_parity, _t_fallback]
    npass = 0
    print("== songforge selftest ==")
    log.info("selftest 开始", cases=len(cases))
    for fn in cases:
        try:
            notes = fn()
            for name, detail in notes:
                # 测试函数用返回文本表达失败时，必须显式识别，不能算作通过
                bad = bool(re.search(r"\bFAIL\b|\bERR\b|失败", str(detail)))
                print("  [%s] %-22s %s"
                      % ("FAIL" if bad else "ok", name, detail))
                if bad:
                    raise AssertionError(detail)
            npass += 1
        except AssertionError as e:
            print("  [--] %-22s %s" % (fn.__name__[3:], e or "断言失败"))
            log.warn("case 失败", case=fn.__name__, err=str(e))
        except Exception as e:
            print("  [ERR] %-22s %s: %s"
                  % (fn.__name__[3:], type(e).__name__, e))
            log.error("case 异常", case=fn.__name__, err=str(e))
    print("== 结果 ==")
    print("  %d/%d 通过" % (npass, len(cases)))
    log.info("selftest 结束", passed=npass, total=len(cases), log_dir=run.dir)
    log.close()
    return 0 if npass == len(cases) else 1


# ---------------------------------------------------------------- parser

