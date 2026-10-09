# -*- coding: utf-8 -*-
"""从历史脚本 remake_v5.py 抽出 8 首已认可老歌的基线资料。

用途：QC 需要"歌词正文"才能算召回率。没有歌词，coverage() 恒等于 0，
老歌基线就退化成"召回 >= 0%"，等于没有基线——这种假放行比不放行更危险。

同时抽出 bpm / duration / key，让 QC 能按真实 8 小节算理论前奏秒数。
不重新生成音频，只读源码里的 BODY / META。

输出：config/baseline/qc_baseline_old.json
源脚本路径可用环境变量 SONGFORGE_REMAKE_V5 覆盖。
"""
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(HERE)
OUT = os.path.join(_PKG, "config", "baseline", "qc_baseline_old.json")

_DEFAULT_SRC = os.path.join(
    os.environ.get("USERPROFILE", os.environ.get("HOME", "")),
    r".trae-cn\work\6ab523de391489c168050edc\remake_v5.py")
SRC = os.environ.get("SONGFORGE_REMAKE_V5") or _DEFAULT_SRC

BRACKET = re.compile(r"^\[[^\]]*\]")
CJK = re.compile(r"[\u4e00-\u9fff]")


def _load_remake():
    if not os.path.isfile(SRC):
        print("[错误] 找不到 remake_v5.py: %s" % SRC)
        print("       设置 SONGFORGE_REMAKE_V5=完整路径 后重试")
        return None
    spec = importlib.util.spec_from_file_location("remake_v5", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # __main__ 有守卫，不会提交任何任务
    return m


def body_text(m, title):
    """把 BODY[title] 折成纯歌词正文。"""
    seen, keep = set(), []
    for _tag, text in m.BODY[title]:
        if "Instrumental" in text:
            continue
        for line in text.split("\n"):
            line = BRACKET.sub("", line).strip()
            if not line or not CJK.search(line):
                continue
            if line in seen:
                continue
            seen.add(line)
            keep.append(line)
    return "\n".join(keep)


def main():
    m = _load_remake()
    if m is None:
        return 2
    items = []
    for title, seed, bpm, key, dur, style in m.META:
        items.append(dict(
            title=title,
            file="v5_%s_swl_00001.flac" % title,
            bpm=bpm, key=key, duration=dur,
            group="old",
            lyrics=body_text(m, title),
        ))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dict(note="8 首用户已认可老歌的基线资料，"
                             "由 tools/make_baseline.py 从 remake_v5.py 抽取",
                       items=items), f,
                  ensure_ascii=False, indent=1)
    for it in items:
        n = len(CJK.findall(it["lyrics"]))
        print("%-8s bpm=%-4s dur=%-4s 汉字=%d" % (it["title"], it["bpm"],
                                                  it["duration"], n))
    print("\nwrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
