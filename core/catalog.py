# -*- coding: utf-8 -*-
"""全局索引 catalog.jsonl：只存元数据，可从 runs 重建。"""
import json
import os
import re

from . import paths as pmod


def catalog_path(cfg):
    p = cfg["paths"].get("catalog_file") or "songforge/index/catalog.jsonl"
    return pmod.under_workflows(p)


def day_dir(runs_root, day=None):
    """runs_root/YYYY-MM-DD/"""
    import datetime
    if not day:
        day = datetime.date.today().isoformat()
    return os.path.join(runs_root, day)


def allocate_run_dir(runs_root, run_id, day=None):
    """占位 data/runs/YYYY-MM-DD/<run_id>/，返回绝对路径。"""
    d = day_dir(runs_root, day)
    pmod.ensure_dir(d)
    path = os.path.join(d, run_id)
    os.makedirs(path, exist_ok=False)
    return path


def find_run_dir(runs_root, run_id):
    """在按日目录、_migrated、扁平根下查找 run 目录。"""
    rid = (run_id or "").strip().rstrip("/\\")
    if not rid:
        return None
    if os.path.isdir(rid) and os.path.isfile(os.path.join(rid, "manifest.json")):
        return os.path.abspath(rid)
    base = os.path.basename(rid.replace("/", os.sep))
    if not os.path.isdir(runs_root):
        return None
    # 直接子目录（兼容旧扁平）
    cand = os.path.join(runs_root, base)
    if os.path.isfile(os.path.join(cand, "manifest.json")):
        return cand
    # 按日
    for day in os.listdir(runs_root):
        day_path = os.path.join(runs_root, day)
        if not os.path.isdir(day_path):
            continue
        cand = os.path.join(day_path, base)
        if os.path.isfile(os.path.join(cand, "manifest.json")):
            return cand
        # _migrated 下也可能套一层
        if day in ("_migrated",):
            for sub in os.listdir(day_path):
                cand = os.path.join(day_path, sub)
                if os.path.basename(cand) == base and \
                   os.path.isfile(os.path.join(cand, "manifest.json")):
                    return cand
    # 全树兜底
    for root, dirs, files in os.walk(runs_root):
        if os.path.basename(root) == base and "manifest.json" in files:
            return root
    return None


def _entry_from_manifest(rundir, man):
    results = man.get("results") or []
    seeds = [r.get("seed") for r in results if r.get("seed") is not None]
    files = []
    for r in results:
        for fn in r.get("files") or []:
            files.append(fn)
    day = None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", rundir.replace("\\", "/"))
    if m:
        day = m.group(1)
    elif man.get("created"):
        day = str(man["created"])[:10]
    return {
        "run_id": man.get("run_id") or os.path.basename(rundir),
        "date": day,
        "title": man.get("title"),
        "style": man.get("style"),
        "bpm": man.get("bpm"),
        "key": man.get("key"),
        "duration": man.get("duration"),
        "seeds": seeds,
        "qc_verdict": man.get("qc_verdict"),
        "audio_files": files,
        "path": rundir.replace("\\", "/"),
    }


def append_entry(cfg, entry):
    path = catalog_path(cfg)
    pmod.ensure_dir(os.path.dirname(path))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def update_qc_verdict(cfg, run_id, verdict):
    """重写 catalog：更新匹配 run_id 的 qc_verdict。"""
    path = catalog_path(cfg)
    if not os.path.isfile(path):
        return
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("run_id") == run_id:
                row["qc_verdict"] = verdict
            rows.append(row)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def rebuild(cfg):
    """扫描 runs_dir，重写 catalog.jsonl。"""
    runs_root = pmod.under_workflows(cfg["paths"]["runs_dir"])
    entries = []
    if os.path.isdir(runs_root):
        for root, dirs, files in os.walk(runs_root):
            if "manifest.json" not in files:
                continue
            mp = os.path.join(root, "manifest.json")
            try:
                with open(mp, encoding="utf-8") as f:
                    man = json.load(f)
            except (ValueError, OSError):
                continue
            entries.append(_entry_from_manifest(root, man))
    entries.sort(key=lambda e: (e.get("date") or "", e.get("run_id") or ""))
    path = catalog_path(cfg)
    pmod.ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path, len(entries)


def list_entries(cfg, day=None, limit=50):
    path = catalog_path(cfg)
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if day and row.get("date") != day:
                continue
            out.append(row)
    out.reverse()
    return out[:limit]


def entry_from_gen(rundir, manifest):
    return _entry_from_manifest(rundir, manifest)
