# -*- coding: utf-8 -*-
"""路径解析。

PKG_ROOT = 本仓根（clone 到哪就是哪）。
ComfyUI 根 / QC Python / 音乐目录来自 local.toml 或环境变量，与仓位置解耦。
"""
import datetime
import os

# songforge/core/paths.py -> 仓根
PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 兼容旧名：曾表示 workflows 父目录；现一律指仓根
ROOT = PKG_ROOT

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

    # 嵌套在 ComfyUI/user/default/workflows/songforge 时向上探测
    cur = PKG_ROOT
    for _ in range(8):
        hits = sum(1 for m in _MARKERS if os.path.isdir(os.path.join(cur, m)))
        if hits >= 2:
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    return PKG_ROOT


def comfy_workflows_dir(cfg=None):
    """ComfyUI user/default/workflows（模板搜索备用）。"""
    return os.path.join(comfy_root(cfg=cfg), "user", "default", "workflows")


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


def _strip_legacy_prefix(rel):
    """旧配置相对 workflows 父目录写 songforge/...，现相对仓根。"""
    norm = rel.replace("\\", "/")
    if norm.startswith("songforge/"):
        return norm[len("songforge/"):]
    return rel


def under_pkg(rel, root=None):
    """相对仓根解析；绝对路径原样返回。兼容旧 songforge/ 前缀。"""
    if not rel:
        raise ValueError("路径为空")
    if os.path.isabs(rel):
        return os.path.normpath(rel)
    rel = _strip_legacy_prefix(rel)
    base = root if root is not None else PKG_ROOT
    return os.path.normpath(os.path.join(base, rel))


# 旧名
under_workflows = under_pkg


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
    base = under_pkg(cfg["paths"]["tmp_dir"])
    return ensure_dir(day_subdir(base, day))


def resolve_template(name_or_path, cfg=None):
    """解析 workflow 模板绝对路径。

    顺序：绝对路径 → 仓内相对路径 → ComfyUI workflows 目录下的同名文件。
    """
    if not name_or_path:
        raise ValueError("workflow_template 为空")
    if os.path.isabs(name_or_path) and os.path.isfile(name_or_path):
        return name_or_path
    cand = under_pkg(name_or_path)
    if os.path.isfile(cand):
        return cand
    # 包内默认模板
    bundled = os.path.join(PKG_ROOT, "workflows", "template.json")
    base = os.path.basename(name_or_path)
    if base in ("template.json",) and os.path.isfile(bundled):
        return bundled
    if name_or_path in ("workflows/template.json", "template.json") and \
       os.path.isfile(bundled):
        return bundled
    # ComfyUI 工作流目录（嵌套部署或本机另放的模板）
    cw = comfy_workflows_dir(cfg)
    for p in (os.path.join(cw, name_or_path), os.path.join(cw, base),
              os.path.join(cw, "songforge", "workflows", base)):
        if os.path.isfile(p):
            return p
    if os.path.isfile(bundled):
        return bundled
    return cand


def path_layout(cfg):
    """从配置展开本包目录布局（绝对路径）。"""
    p = cfg["paths"]
    cro = comfy_root(cfg=cfg)
    return {
        "pkg": PKG_ROOT,
        "workflows_host": comfy_workflows_dir(cfg),
        "comfy_root": cro,
        "template": resolve_template(
            p.get("workflow_template") or "workflows/template.json", cfg),
        "runs": under_pkg(p["runs_dir"]),
        "tmp": under_pkg(p["tmp_dir"]),
        "tmp_today": day_subdir(under_pkg(p["tmp_dir"])),
        "lyrics": under_pkg(p["lyrics_dir"]),
        "qc_cache": under_pkg(p["qc_cache_dir"]),
        "style_cache": under_pkg(p["style_cache_dir"]),
        "index": under_pkg(p.get("index_dir") or "index"),
        "catalog": under_pkg(
            p.get("catalog_file") or "index/catalog.jsonl"),
        "baseline": under_pkg(
            p.get("baseline_dir") or "config/baseline"),
        "audio_out": music_dir(cfg, cro),
        # 兼容旧键
        "workflows": PKG_ROOT,
    }


# 仓根不应出现的过程文件（doctor WARN）
ROOT_JUNK_NAMES = ("qc.json", "run.log", "manifest.json")
