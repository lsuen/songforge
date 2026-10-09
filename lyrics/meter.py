# -*- coding: utf-8 -*-
"""歌词容量与时长对应关系。

这是"死心塌地"的部分：不看模型表现，只按音乐结构算出这段时间到底能唱多少字，
填字必须落进区间，否则 check 直接报错，不给侥幸。

推导：
    每小节秒数   = 60 / BPM * 每小节拍数
    总小节       = 时长 / 每小节秒数
    可唱小节     = (总小节 - 前奏小节) * 可唱比例
    音节容量     = 可唱小节 * 每小节音节区间
    填充率       = 实际音节 / 容量上限（仅诊断用，不作为放行闸门）

阈值来源（重要）：
    syllable_per_bar_* 与 ideal_line_syllable 不是理论推导，而是用 remake_v5.py 里
    8 首用户已实际生成并认可的歌反推出来的真实工作区间：
        每可唱小节音节  4.56 ~ 7.64   （均值 6.17）
        每句音节        5.9 ~ 7.4    （均值 6.7）
    改这两个数之前请先跑 selftest 的"实歌回归"，否则会误杀已验证可生成的歌词。
    历史坑：曾按理论值设成"每拍 2.6~4.4 音节、每句下限 7"，结果 8 首实歌
    全部被判歌词过少（实测每拍只有 1.14~1.91 音节）。理论值不能当阈值用。
"""
import math
import re

from ..core.errors import LyricDensityError

SEC_RE = re.compile(r"^\[(intro|instrumental|verse|pre-?chorus|chorus|hook|"
                    r"rap|bridge|break|breakdown|build|drop|final drop|"
                    r"post-?drop|outro|final hook|final chorus)\]",
                    re.I)


def lyric_len(text):
    cn = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    en = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", text))
    return cn, en




def kana_count(text):
    """假名音节数：平假名 + 片假名 + 半角浊音。

    为什么必须单独计数（中日混合歌词实测踩到的坑）：
    lyric_len 只认汉字和英文字母，日文假名一个都不数。
    "お誕生日おめでとう 一棠ちゃん" 这行真实可唱约 13 音节，
    原实现只算得到 5 个汉字 + 1 个英文字（2），少算一大截，
    于是 check 把一份写满的正常歌词误判成"歌词过少"，
    写词的人只能靠加无意义的字去凑数，越凑越空洞。

    排除两类字符，它们不独立占音节：
      1. 小书写假名 ぁぃぅぇぉゃゅょゎ ァィゥェォャュョヮ ヵヶ
         以及促音 っ —— 它们依附在前一个音节上。
      2. 长音符号 ー ゝゞヽヾ —— 它们延长前一个音，不新增音节。
    """
    small = (u"\u3041\u3043\u3045\u3047\u3049\u3063"
             u"\u3083\u3085\u3087\u308e"
             u"\u30a1\u30a3\u30a5\u30a7\u30a9\u30c3"
             u"\u30e3\u30e5\u30e7\u30ee\u30f5\u30f6"
             u"\u30fc\u309d\u309e\u30fd\u30fe")
    body = "".join(c for c in text if c not in small)
    return len(re.findall(r"[\u3041-\u309F\u30A0-\u30FF\uFF66-\uFF9F]", body))


def syllable(text):
    """可唱近似音节数：汉字*1 + 英文词*2 + 假名*1。"""
    cn, en = lyric_len(text)
    return cn + en * 2 + kana_count(text)


def only_lyrics(text):
    """剥掉结构标记与 [Instrumental: ...] 描述，只留真正被唱的正文。"""
    out = []
    for ln in text.split("\n"):
        s = ln.strip()
        if not s or s.startswith("["):
            continue
        out.append(s)
    return "\n".join(out)


def sections(text):
    """按结构标记切段，返回 [(tag, body_text)]。"""
    out = []
    tag = "unknown"
    buf = []
    for ln in text.split("\n"):
        s = ln.strip()
        if s.startswith("["):
            m = SEC_RE.match(s)
            if m:
                if buf:
                    out.append((tag, "\n".join(buf).strip()))
                tag = m.group(1).lower()
                buf = []
                continue
        buf.append(ln)
    if buf:
        out.append((tag, "\n".join(buf).strip()))
    return out


def bars(duration, bpm, beats_per_bar=4):
    return duration / (60.0 / bpm * beats_per_bar)


def pick(cfg_lyrics, profile=None):
    """按档取出密度阈值。

    default 档直接用 lyrics 顶层字段（保持向后兼容）；
    其它档从 lyrics.profiles.<name> 覆盖顶层同名字段后返回。
    这样加档不用改任何计算公式，只加配置。
    """
    if not profile or profile == "default":
        return cfg_lyrics
    prof = (cfg_lyrics.get("profiles") or {}).get(profile)
    if not prof:
        raise LyricDensityError(
            "未知密度档 '%s'，可选：default / %s"
            % (profile, ", ".join(sorted((cfg_lyrics.get("profiles") or {})))))
    out = dict(cfg_lyrics)
    out.update({k: v for k, v in prof.items() if not k.startswith("_")})
    out["_profile"] = profile
    return out


def capacity(duration, bpm, cfg_lyrics):
    """返回该时长/BPM 下的音节区间与结构量。

    区间由"每可唱小节音节数"决定，这是唯一闸门；填充率只做诊断。
    """
    beats = 4                                  # time_signature 固定 4 拍
    total = bars(duration, bpm, beats)
    sing = max(1.0, (total - cfg_lyrics["intro_bars"])
               * cfg_lyrics["sing_bar_ratio"])
    lo = sing * cfg_lyrics["syllable_per_bar_min"]
    hi = sing * cfg_lyrics["syllable_per_bar_max"]
    return dict(total_bars=total, sing_bars=sing, lo=lo, hi=hi,
                beats_per_bar=beats)


def window(duration, bpm, cfg_lyrics):
    """check 实际放行的音节窗口（整数化）。

    对外所有"该写多少字"的表述都必须来自这里，避免文档与代码不一致。
    """
    cap = capacity(duration, bpm, cfg_lyrics)
    lo, hi = cap["lo"], cap["hi"]
    if hi < lo:
        lo = hi = cap["lo"]
    return int(math.ceil(lo)), int(math.floor(hi))


def analyse(lyrics, duration, bpm, cfg_lyrics):
    body = only_lyrics(lyrics)
    syl = syllable(body)
    lines = [l for l in body.split("\n") if l.strip()]
    cap = capacity(duration, bpm, cfg_lyrics)
    wlo, whi = window(duration, bpm, cfg_lyrics)
    fill = syl / cap["hi"] if cap["hi"] else 0.0
    per_line = syl / len(lines) if lines else 0.0
    return dict(syllables=syl, lines=len(lines), per_line=round(per_line, 1),
                fill=round(fill, 3), total_bars=round(cap["total_bars"], 1),
                sing_bars=round(cap["sing_bars"], 1),
                lo=int(cap["lo"]), hi=int(cap["hi"]),
                win_lo=wlo, win_hi=whi)


def target_words(duration, bpm, cfg_lyrics):
    """给写词的人用的目标字数区间（死心塌地填字依据）。

    返回 (建议字数, 下限, 上限)。下限上限与 check 实际放行窗口完全一致。
    建议字数取实测均值 6.17 音节/小节对应的位置，不取理论中点，
    因为理论中点会把歌写得比已验证作品明显更稀或明显更密。
    """
    cap = capacity(duration, bpm, cfg_lyrics)
    wlo, whi = window(duration, bpm, cfg_lyrics)
    mid = cap["sing_bars"] * 6.17              # 实测工作区均值
    mid = max(wlo, min(mid, whi))
    return int(mid), wlo, whi


def check(lyrics, duration, bpm, cfg_lyrics):
    """死心塌地校验。返回问题列表，空列表代表通过。"""
    st = analyse(lyrics, duration, bpm, cfg_lyrics)
    errs = []
    if st["syllables"] == 0:
        errs.append("正文为空，没有任何可唱歌词")
        return errs, st
    if st["syllables"] < st["win_lo"]:
        errs.append(
            "歌词过少：%d 音节（放行下限 %d，还缺约 %d）。"
            "%ds / %dBPM 共 %.0f 小节，前奏占 %d 小节，实际可唱 %.0f 小节，"
            "按实测密度需要 %d~%d 音节。字太少模型会硬拉长音、抢拍、吞字。"
            % (st["syllables"], st["win_lo"], st["win_lo"] - st["syllables"],
               duration, bpm, st["total_bars"], cfg_lyrics["intro_bars"],
               st["sing_bars"], st["win_lo"], st["win_hi"]))
    elif st["syllables"] > st["win_hi"]:
        errs.append(
            "歌词过多：%d 音节（放行上限 %d，超出约 %d）。"
            "实际可唱 %.0f 小节，按实测密度上限只到 %d 音节。"
            "字太多会加速、丢字、句尾被吃掉。"
            % (st["syllables"], st["win_hi"],
               st["syllables"] - st["win_hi"],
               st["sing_bars"], st["win_hi"]))
    if st["per_line"] > cfg_lyrics["line_syllable_max"]:
        errs.append("平均每句 %.1f 音节，超过上限 %d，换气点不足，容易抢拍"
                    % (st["per_line"], cfg_lyrics["line_syllable_max"]))
    elif st["per_line"] < cfg_lyrics["ideal_line_syllable"][0]:
        errs.append("平均每句 %.1f 音节偏少（实测下限约 %.1f），句子太碎，"
                    "容易出现拖腔填充"
                    % (st["per_line"],
                       cfg_lyrics["ideal_line_syllable"][0]))
    return errs, st


def enforce(lyrics, duration, bpm, cfg_lyrics):
    errs, st = check(lyrics, duration, bpm, cfg_lyrics)
    if errs:
        raise LyricDensityError(
            "歌词与时长不匹配（死心塌地时长对应关系）：\n  - "
            + "\n  - ".join(errs))
    return st