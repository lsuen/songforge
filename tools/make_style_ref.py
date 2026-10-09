# -*- coding: utf-8 -*-
"""用历史已认可作品建风格质心语料 -> config/baseline/style_ref.json

跑法（必须在 ComfyUI venv 的 Python 下）：
    C:\\comfyui-venv\\Scripts\\python.exe tools\\make_style_ref.py

音乐目录 / 输出路径读 config/default.json + local.json（勿写死盘符）。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 坑位：songforge.py（CLI 入口）和 songforge/（包）同名，
# tools/ 内部互相引用一律走文件路径加载。
sys.path.insert(0, HERE)
import qc_style  # noqa: E402

_PKG = os.path.dirname(HERE)


def _paths_from_config():
    paths = {}
    for name in ("default.json", "local.json"):
        p = os.path.join(_PKG, "config", name)
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8") as f:
            blob = json.load(f)
        paths.update(blob.get("paths") or {})
    music = paths.get("music_dir") or os.environ.get("SONGFORGE_MUSIC_DIR")
    if not music:
        cro = paths.get("comfy_root") or os.environ.get("SONGFORGE_COMFY_ROOT")
        if cro:
            music = os.path.join(
                cro, "output", paths.get("output_prefix") or "MyMusic")
    if not music:
        music = os.path.join(
            os.path.dirname(os.path.dirname(_PKG)), "output", "MyMusic")
    out = os.path.join(_PKG, "config", "baseline", "style_ref.json")
    cache = os.path.join(_PKG, "cache", "style")
    return os.path.abspath(music), out, cache


MUSIC, OUT, CACHE = _paths_from_config()

# 前缀白名单：确认过的直接给 family，避免子串规则误伤
EXACT = {
    "smile13": "pop",
    "hit10": "pop",
    "dy10": "pop",
    "col2": "pop",
    "punk4": "punk",
    "niaopunk": "punk",
    "urbanhiphop": "hiphop",
    "chillhiphop": "hiphop",
    "chillhiphopen": "hiphop",
    "chillhiphopja": "hiphop",
    "upbeathiphop": "hiphop",
    "funkyhiphop": "hiphop",
    "discofunk": "hiphop",
    "partytrap": "hiphop",
    "smoothhiphop": "hiphop",
    "喜剧hiphop": "hiphop",
    "欧式hiphop": "hiphop",
}

# 出现在前缀里就归某家族（前面已排除精确表未命中的）
SUBSTR = (
    ("punk", "punk"),
    ("hiphop", "hiphop"),
    ("trap", "hiphop"),
)

SEED = re.compile(r"_\d{5}\.flac$", re.I)
# 只收每个 seed 序列的第一首。同源的 _00002/_00003 全收进来，语料就被
# 少数几首歌按 seed 数投票，质心被拉偏（踩过：172 个文件其实只有 30 来首歌）。
FIRST_SEED = re.compile(r"_00001\.flac$", re.I)


def family_of(name):
    """从文件名解析风格家族，解析不出来返回 None（直接排除）。"""
    stem = SEED.sub("", name)
    head = stem.split("_")[0]
    if head in EXACT:
        return EXACT[head]
    low = head.lower()
    for key, fam in SUBSTR:
        if key in low:
            return fam
    return None


def main():
    if not os.path.isdir(MUSIC):
        print("[错误] 找不到音频目录 %s" % MUSIC)
        return 2

    files = sorted(f for f in os.listdir(MUSIC) if f.lower().endswith(".flac"))
    picked = []
    for f in files:
        fam = family_of(f)
        if not fam:
            continue
        if not FIRST_SEED.search(f):        # 只收每首歌的第一个 seed
            continue
        p = os.path.join(MUSIC, f)
        if not os.path.isfile(p):
            continue
        picked.append((f, fam, p))

    if not picked:
        print("[错误] 一首都没匹配上，检查 EXACT / SUBSTR 是否还匹配文件名")
        return 2

    print("语料候选 %d 首，开始算特征（每首几秒）..." % len(picked))
    os.makedirs(CACHE, exist_ok=True)
    items = []
    for i, (f, fam, p) in enumerate(picked, 1):
        cp = os.path.join(CACHE, f + ".json")
        # 缓存 key 带上音频 mtime + 特征列表，音频换了或特征改了都会自动失效
        stamp = "%s|%s" % (os.path.getmtime(p), ",".join(qc_style.FEATURES))
        vec = None
        if os.path.isfile(cp):
            try:
                with open(cp, encoding="utf-8") as fh:
                    blob = json.load(fh)
                if blob.get("key") == stamp:
                    vec = blob["vec"]
            except Exception:
                vec = None
        if vec is None:
            try:
                # expect_bpm=None：历史文件没有可靠的 BPM 记录，靠自身中位数定折叠锚点，
                # 保证语料里的 tempo 尺度自洽。新歌的 BPM 对齐由 qc_worker 单独判。
                vec, _raw = qc_style.features(p, expect_bpm=None)
            except Exception as exc:
                print("  [%d/%d] 跳过 %s -> %s: %s"
                      % (i, len(picked), f, type(exc).__name__, exc))
                continue
            with open(cp, "w", encoding="utf-8") as fh:
                json.dump(dict(key=stamp, vec=vec), fh)
        items.append(dict(file=f, family=fam, vec=vec))
        print("  [%d/%d] %-44s %-6s tempo=%5.1f flat=%.4f"
              % (i, len(picked), f[:44], fam, vec["tempo"], vec["flatness"]))

    counts = {}
    for it in items:
        counts[it["family"]] = counts.get(it["family"], 0) + 1
    print("按家族统计：%s" % counts)
    for fam in qc_style.FAMILIES:
        if counts.get(fam, 0) < 3:
            print("[错误] %s 只有 %d 首，建不出可信质心（至少 3）"
                  % (fam, counts.get(fam, 0)))
            return 2

    ref = qc_style.build(items)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(ref, f, ensure_ascii=False, indent=1)

    loo = ref["loo"]
    print("")
    print("已写出 %s" % OUT)
    print("样本 %d（%d 首不同的歌），分组留一准确率 %.1f%%，trusted=%s"
          % (len(items), loo["n_songs"], loo["acc"] * 100, ref["trusted"]))
    print("分家族：%s" % json.dumps(loo["per_family"], ensure_ascii=False))
    print("混淆矩阵（行=真实，列=判成）：")
    for fam, row in sorted(loo["confusion"].items()):
        print("  %-7s %s" % (fam, json.dumps(row, ensure_ascii=False)))
    if not ref["trusted"]:
        print("")
        print("[结论] 风格判定不可信，只写进报告当参考，不当放行闸门。")
        print("       风格的第一物理保证是实测 BPM 与设定 BPM 对齐（QC 的 bpm_rel_tol）。")
        print("       注意：语料里 pop 的 chill/disco/funk 变体和 hiphop 制作手法本就连续，")
        print("       分组 LOO 总体约 60~67%、单家族最低约 43%，"
              "这是语料重叠，不是分类器没调好。")
    return 0


if __name__ == "__main__":
    sys.exit(main())