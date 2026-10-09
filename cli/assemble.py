# -*- coding: utf-8 -*-
"""铁律组装与校验：tags / 前奏注入 / 密度闸门。"""
import re

from ..core.errors import RuleViolation
from ..lyrics import meter
from ..rules import ironlaw
from .common import chill_intro_bars, resolve_profile


def split_sections(lyrics_raw):
    """把裸歌词切成 [(tag, text)]，识别不出标记时整段当 [verse]。"""
    secs = []
    cur_tag, buf = None, []
    for ln in lyrics_raw.split("\n"):
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            if cur_tag and buf:
                secs.append((cur_tag, "\n".join(buf).strip()))
            cur_tag, buf = s, []
        elif s:
            buf.append(s)
    if cur_tag and buf:
        secs.append((cur_tag, "\n".join(buf).strip()))
    if not secs:
        secs = [("[verse]", lyrics_raw.strip())]
    return secs


def vocal_sections(lyrics_raw):
    """只取真正有人唱的段：丢掉所有只挂 [inst] 的纯器乐占位。

    chill 重排时器乐占位要全部重新生成，先从原始歌词剥掉，
    否则同一段器乐会被写两遍，第二遍反而把人声提前拉回来。
    """
    out = []
    for tag, text in split_sections(lyrics_raw):
        t = text.strip()
        if not t or re.fullmatch(r"\[?inst\]?|\[Instrumental[^\]]*\]", t, re.I):
            continue
        out.append((tag, text))
    return out or [("[verse]", "")]


def duration_allowed(cfg, dur):
    """时长闸门：主区间 + 显式白名单区间。

    主区间 150~200s 来自 35 首已验证实歌，不能为短时长题材放宽。
    短视频/口水歌这类确实需要 100~130s，但没有实歌背书，
    所以走 config.song.duration_extra_ranges 显式列出。
    """
    lo, hi = cfg["song"]["duration_range"]
    if lo <= dur <= hi:
        return True
    for a, b in cfg["song"].get("duration_extra_ranges") or []:
        if a <= dur <= b:
            return True
    return False


def _extra_hint(cfg, dur):
    """报错时列出当前生效的额外区间，省一次查配置。"""
    ex = cfg["song"].get("duration_extra_ranges") or []
    if not ex:
        return ""
    return "（额外放行区间：%s）" % ", ".join("%d~%ds" % (a, b) for a, b in ex)


def assemble(args, cfg, st, lyrics_raw, title):
    """套用铁律：生成 tags、补器乐前奏、清洗中文标记。"""
    chill = bool(getattr(args, "chill_vocal", False))
    inst = bool(getattr(args, "instrumental", False))
    lean = bool(getattr(args, "lean_tags", False))
    tags = ironlaw.build_tags(
        style=st.get("style", ""), instruments=st.get("instruments", ""),
        mood=st.get("mood", ""), extra=st.get("extra", ""),
        chill_vocal=chill, instrumental=inst, lean=lean)
    ibars = chill_intro_bars(cfg, chill)
    # 纯器乐：没有可唱正文，歌词体直接由结构标记铺出来，
    # 密度闸门对纯器乐不适用（见 do_validate）。
    if inst and not lyrics_raw.lstrip().lower().startswith("[intro]"):
        lyrics = ironlaw.build_inst_lyrics(
            st.get("intro_detail") or st.get("instruments"),
            bars=ibars)
        return tags, lyrics
    if chill:
        # chill 走独立结构体：器乐段整体排在人声段之前。
        # 实测这只降低人声密度，并不推迟起点（chill 3 第一句仍在 0 秒），
        # 标签侧的时间/时机约束同样无效。详见 ironlaw 顶部实测结论。
        cfg_lyrics = meter.pick(cfg["lyrics"], resolve_profile(args))
        cap = meter.capacity(st["duration"], st["bpm"], cfg_lyrics)
        lyrics = ironlaw.build_chill_lyrics(
            vocal_sections(lyrics_raw),
            st.get("intro_detail") or st.get("instruments"),
            bars=ibars, total_bars=cap["total_bars"])
        return tags, lyrics
    lyrics = lyrics_raw
    if not lyrics.lstrip().lower().startswith("[intro]"):
        lyrics = ironlaw.build_lyrics(
            split_sections(lyrics_raw),
            st.get("intro_detail") or st.get("instruments"),
            bars=ibars)
    else:
        # 已有 [intro] 头的歌词文件：强制把前奏小节数对齐到标签写的值。
        # 标签写 16 小节而歌词头写 8 小节时，check_rules 会判两处不一致
        # ——这是故意留的闸门，不是 bug。
        lyrics = ironlaw.force_intro_bars(lyrics, ibars)
        # 前奏括号里的配器也要过 rhythm 过滤（2026-10-05 补）。
        # 之前这条分支只改小节数，作者手写的 [Instrumental: ...] 直接
        # 原样送进模型。ckfunk 批次的 8 首歌词前奏括号里就带着
        # "half time feel" 这类非节奏组描述，而作者也可能写 pad、
        # riser、sweep——intro_rhythm_only() 存在的全部理由就是把这些
        # 空场信号从前奏里剔掉，只留节奏组。手写歌词不等于免检。
        lyrics = ironlaw.filter_intro_bracket(lyrics)
    lyrics = "\n".join(ironlaw.strip_cn_instrumental(lyrics.splitlines())) + "\n"
    return tags, lyrics


def do_validate(lyrics, tags, cfg, st, lexicon, strict=True,
                instrumental=False, profile=None, lean=False):
    errs = []
    errs += ironlaw.check_rules(lyrics, tags, lean=lean)
    errs += ironlaw.check_lexicon(lyrics, lexicon)
    cfg_lyrics = meter.pick(cfg["lyrics"], profile)
    if instrumental:
        # 纯器乐没有可唱正文，"歌词过少/过多/每句音节"三条全部不适用。
        # 只保留结构与词典校验，密度统计仅作诊断记录。
        derr = []
        stats = meter.analyse(lyrics, st["duration"], st["bpm"], cfg_lyrics)
    else:
        derr, stats = meter.check(lyrics, st["duration"], st["bpm"], cfg_lyrics)
    errs += derr
    if strict and errs:
        raise RuleViolation("校验未通过：\n  - " + "\n  - ".join(errs))
    return errs, stats


# ---------------------------------------------------------------- commands

