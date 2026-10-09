# -*- coding: utf-8 -*-
"""路径解析。ComfyUI 根优先配置/环境变量，再自动探测。"""
import datetime
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_MARKERS = (
    os.path.join("models", "checkpoints"),
    os.path.join("custom_nodes"),
    os.path.join("user", "default"),
)


def comfy_root(explicit=None, cfg=None):
    """返回 ComfyUI 根目录绝对路径。"""
    if explicit:
        return os.path.abspath(explicit)

    if cfg:
        cr = (cfg.get("paths") or {}).get("comfy_root")
        if cr:
            return os.path.abspath(cr)

    env = os.environ.get("SONGFORGE_COMFY_ROOT")
    if env:
        return os.path.abspath(env)

    cur = ROOT
    for _ in range(6):
        hits = sum(1 for m in _MARKERS if os.path.isdir(os.path.join(cur, m)))
        if hits >= 2:
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    return os.path.dirname(os.path.dirname(os.path.dirname(ROOT)))


def model_path(root, *parts):
    return os.path.join(root, "models", *parts)


def user_default(root):
    return os.path.join(root, "user", "default")


def output_dir(root, prefix="MyMusic"):
    return os.path.join(root, "output", *prefix.split("/"))


def music_dir(cfg=None, root=None):
    """音频成品目录：paths.music_dir 或 ComfyUI/output/<output_prefix>。"""
    if cfg:
        md = (cfg.get("paths") or {}).get("music_dir")
        if md:
            return os.path.abspath(md)
        root = root or comfy_root(cfg=cfg)
        return output_dir(root, cfg["paths"].get("output_prefix", "MyMusic"))
    root = root or comfy_root()
    return output_dir(root, "MyMusic")


def under_workflows(rel, root=None):
    if not rel:
        raise ValueError("路径为空")
    if os.path.isabs(rel):
        return os.path.normpath(rel)
    base = root if root is not None else ROOT
    return os.path.normpath(os.path.join(base, rel))


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def today_iso():
    return datetime.date.today().isoformat()


def day_subdir(base, day=None):
    """base/YYYY-MM-DD/"""
    return os.path.join(base, day or today_iso())


def tmp_today(cfg, day=None):
    """paths.tmp_dir/YYYY-MM-DD/，确保存在。"""
    base = under_workflows(cfg["paths"]["tmp_dir"])
    return ensure_dir(day_subdir(base, day))


def path_layout(cfg):
    """从配置展开本包目录布局（绝对路径）。"""
    p = cfg["paths"]
    cro = comfy_root(cfg=cfg)
    return {
        "workflows": ROOT,
        "comfy_root": cro,
        "runs": under_workflows(p["runs_dir"]),
        "tmp": under_workflows(p["tmp_dir"]),
        "tmp_today": day_subdir(under_workflows(p["tmp_dir"])),
        "lyrics": under_workflows(p["lyrics_dir"]),
        "qc_cache": under_workflows(p["qc_cache_dir"]),
        "style_cache": under_workflows(p["style_cache_dir"]),
        "index": under_workflows(p.get("index_dir") or "songforge/index"),
        "catalog": under_workflows(
            p.get("catalog_file") or "songforge/index/catalog.jsonl"),
        "baseline": under_workflows(
            p.get("baseline_dir") or "songforge/config/baseline"),
        "audio_out": music_dir(cfg, cro),
        "pkg": under_workflows("songforge"),
    }


# 根目录不应出现的过程文件（doctor WARN）
ROOT_JUNK_NAMES = ("qc.json", "run.log", "manifest.json")
