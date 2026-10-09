# -*- coding: utf-8 -*-
"""结构化日志：控制台可读 + JSONL 落盘。run_id 贯穿一次运行的所有记录。

每次运行的日志写到 runs/<run_id>/run.log(JSONL)，便于出问题时回溯。
"""
import datetime
import json
import os
import sys

LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


class Run(object):
    """一次运行的上下文：run_id、目录、产物清单。"""

    def __init__(self, run_id, root):
        self.run_id = run_id
        self.dir = os.path.join(root, run_id)
        os.makedirs(self.dir, exist_ok=True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def save_json(self, name, obj):
        p = self.path(name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        return p

    def save_text(self, name, text):
        p = self.path(name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p


class Logger(object):
    def __init__(self, run=None, level="info", quiet=False, stream=None,
                 log_path=None):
        self.run = run
        self.level = LEVELS.get(level, 20)
        self.quiet = quiet
        self.stream = stream or sys.stdout
        self._fh = None
        path = log_path
        if path is None and run:
            path = run.path("run.log")
        if path:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._fh = open(path, "w", encoding="utf-8")

    def log(self, level, msg, **fields):
        if LEVELS.get(level, 20) < self.level:
            return
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        rid = self.run.run_id if self.run else "-"
        extra = " ".join("%s=%s" % (k, v) for k, v in fields.items())
        line = "%s [%s] run=%s %s%s" % (
            ts, level.upper(), rid, msg,
            ("  " + extra) if extra else "")
        if level in ("warn", "error"):
            print(line, file=self.stream, flush=True)
        elif not self.quiet:
            print(line, file=self.stream, flush=True)
        if self._fh:
            rec = dict(ts=datetime.datetime.now().isoformat(timespec="seconds"),
                       run_id=rid, level=level, msg=msg, **fields)
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._fh.flush()

    def debug(self, msg, **f):
        self.log("debug", msg, **f)

    def info(self, msg, **f):
        self.log("info", msg, **f)

    def warn(self, msg, **f):
        self.log("warn", msg, **f)

    def error(self, msg, **f):
        self.log("error", msg, **f)

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None
