# -*- coding: utf-8 -*-
"""带时间戳的备份工具。任何文档/配置/脚本更新前必须先跑它。

    python songforge/tools/backup.py 生歌规则.md LLM_Wiki.md
    python songforge/tools/backup.py --note "加 songforge CLI 章节" AGENTS.md
    python songforge/tools/backup.py --from-root D:\\CodeFiles\\Thinking\\ComfyUI-MiniMaxH3
    python songforge/tools/backup.py --restore bak\\20261004-120000
    python songforge/tools/backup.py --list

产物：workflows/bak/<YYYYMMDD-HHMMSS-毫秒>/<原相对路径>
恢复：python songforge/tools/backup.py --restore <备份目录>

坑位记录：
- 早期版本把路径压平成 "目录__文件.bak"，文件一多就分不清谁是谁，
  且 AGENTS.md 在仓库根、不在 workflows 下，会退化成 ..__..__..__AGENTS.md.bak。
  现在保留完整目录树：workflows 内的文件按相对路径存放，
  仓库根的文件统一放在 <ROOT>/ 下，一眼能看出来源。
- 路径分隔符统一转成 "/"，Windows 与 Linux 读 manifest 结果一致。
"""
import datetime
import json
import os
import shutil
import sys

WF_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))          # .../user/default/workflows
BAK = os.path.join(WF_ROOT, "bak")


def stamp():
    """毫秒精度：同一秒内连续备份两次不会互相覆盖。"""
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


def comfy_root():
    """workflows 的上三级 = ComfyUI 根。"""
    return os.path.dirname(
        os.path.dirname(
            os.path.dirname(WF_ROOT)
        )
    )


def rel_under(path):
    """返回可还原、正斜杠的相对路径。

    - workflows 内：songforge/cli/main.py
    - ComfyUI 根：  ROOT__/AGENTS.md
    - 根之外：      ABS__/D/somewhere/file.md
    """
    path = os.path.abspath(path)

    for base, prefix in ((WF_ROOT, ""), (comfy_root(), "ROOT__/")):
        rel = os.path.relpath(path, base)
        if not rel.startswith(".."):
            return prefix + rel.replace(os.sep, "/")

    rel = path.replace(":", "").replace(os.sep, "/").lstrip("/")
    return "ABS__/" + rel


def backup(paths, note=""):
    """复制文件到 bak/<时间戳>/，保留原目录树，返回备份目录。"""
    # 坑位：原来用 exist_ok=True，同一毫秒内连续两次备份会落进同一目录，
    # 上一次的 _manifest.json 直接被覆盖，恢复时就少了一份。改成原子创建。
    base = stamp()
    for n in range(0, 100):
        cand = os.path.join(BAK, base if n == 0 else "%s-%d" % (base, n + 1))
        try:
            os.makedirs(cand, exist_ok=False)
            dst_dir = os.path.abspath(cand)
            break
        except FileExistsError:
            continue
    else:
        raise RuntimeError("无法在 %s 下分配备份目录" % BAK)

    items = []
    for p in paths:
        p = os.path.abspath(p)
        if not os.path.exists(p):
            print("  [skip] 不存在: %s" % p)
            continue

        rel = rel_under(p)
        dst = os.path.join(dst_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(p, dst)

        items.append(dict(src=p, bak=dst, rel=rel,
                          size=os.path.getsize(dst)))
        print("  [ok] %s" % rel)

    with open(os.path.join(dst_dir, "_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(dict(stamp=os.path.basename(dst_dir), note=note,
                       items=items), f, ensure_ascii=False, indent=1)

    print("备份完成 -> %s （%d 个文件）" % (dst_dir, len(items)))
    return dst_dir


def restore(bak_dir):
    """按 manifest 把文件拷回原位。"""
    bak_dir = os.path.abspath(bak_dir)
    mpath = os.path.join(bak_dir, "_manifest.json")
    if not os.path.exists(mpath):
        print("找不到 manifest: %s" % mpath)
        return 1

    with open(mpath, encoding="utf-8") as f:
        m = json.load(f)

    for it in m["items"]:
        if not os.path.exists(it["bak"]):
            print("  [miss] 备份件丢失: %s" % it["bak"])
            continue
        os.makedirs(os.path.dirname(it["src"]), exist_ok=True)
        shutil.copy2(it["bak"], it["src"])
        print("  [restore] %s" % it["rel"])

    print("恢复完成 <- %s" % bak_dir)
    return 0


def list_bak():
    if not os.path.isdir(BAK):
        print("还没有任何备份: %s" % BAK)
        return 1

    rows = []
    for name in sorted(os.listdir(BAK), reverse=True):
        mp = os.path.join(BAK, name, "_manifest.json")
        if not os.path.isfile(mp):
            continue
        with open(mp, encoding="utf-8") as f:
            m = json.load(f)
        rows.append((name, len(m.get("items", [])), m.get("note", "")))

    if not rows:
        print("备份目录为空: %s" % BAK)
        return 1

    for name, n, note in rows:
        print("  %s  %2d 个  %s" % (name, n, note))
    return 0


def _expand(root, patterns):
    out = []
    for pat in patterns:
        full = os.path.join(root, pat.replace("/", os.sep))
        if os.path.isdir(full):
            for dirpath, _, files in os.walk(full):
                for fn in files:
                    if fn.endswith(".pyc"):
                        continue
                    out.append(os.path.join(dirpath, fn))
        else:
            out.append(full)
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 1

    if argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    if argv[0] == "--restore":
        if len(argv) < 2:
            print(__doc__)
            return 1
        return restore(argv[1])

    if argv[0] == "--list":
        return list_bak()

    note = ""
    root = None

    while argv and argv[0].startswith("--"):
        opt = argv[0]
        if opt == "--note" and len(argv) >= 2:
            note = argv[1]
            argv = argv[2:]
        elif opt == "--from-root" and len(argv) >= 2:
            root = os.path.abspath(argv[1])
            argv = argv[2:]
        else:
            print("未知参数: %s" % opt)
            print(__doc__)
            return 1

    if not argv:
        print(__doc__)
        return 1

    backup(_expand(root, argv) if root else argv, note)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
