# -*- coding: utf-8 -*-
"""ComfyUI 执行引擎：提交、轮询、重试、结果回收。只用标准库。"""
import json
import time
import urllib.error
import urllib.request

from .errors import JobFailed, ServerError


class Engine(object):
    def __init__(self, server, timeout=3600, poll_interval=10,
                 submit_retries=3, logger=None):
        self.server = server.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.submit_retries = submit_retries
        self.log = logger

    def _log(self, level, msg, **kw):
        if self.log:
            self.log.log(level, msg, **kw)

    def _get(self, path, timeout=15):
        url = self.server + path
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ServerError("GET %s 返回 %s" % (path, e.code), e.read().decode()[:400])
        except Exception as e:
            raise ServerError("GET %s 失败: %s" % (path, e))

    def _post(self, path, payload, timeout=60):
        url = self.server + path
        req = urllib.request.Request(
            url, data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ServerError("POST %s 返回 %s" % (path, e.code),
                              e.read().decode()[:600])
        except Exception as e:
            raise ServerError("POST %s 失败: %s" % (path, e))

    def health(self):
        """返回 (ok, info)。不抛异常，供 doctor 使用。"""
        try:
            stats = self._get("/system_stats")
        except ServerError as e:
            return False, str(e)
        dev = (stats.get("devices") or [{}])[0]
        info = {
            "device": dev.get("name", "?"),
            "vram_total_gb": round(dev.get("vram_total", 0) / 1024 ** 3, 1),
            "comfyui_version": stats.get("system", {}).get("comfyui_version", "?"),
        }
        q = self._get("/queue")
        info["queue_running"] = len(q.get("queue_running") or [])
        info["queue_pending"] = len(q.get("queue_pending") or [])
        return True, info

    def queue(self):
        return self._get("/queue")

    def submit(self, prompt_json):
        """prompt_json 可以是 dict（API prompt）或 JSON 字符串。
        最终 POST /prompt 的 body 是 {"prompt": <prompt对象>}。"""
        if isinstance(prompt_json, str):
            prompt_json = json.loads(prompt_json)
        payload = self._post("/prompt", json.dumps({"prompt": prompt_json}, ensure_ascii=False))
        pid = payload.get("prompt_id")
        if not pid:
            raise ServerError("提交成功但未返回 prompt_id",
                              json.dumps(payload, ensure_ascii=False)[:400])
        return pid

    def submit_retry(self, prompt_json):
        last = None
        for attempt in range(1, self.submit_retries + 1):
            try:
                return self.submit(prompt_json)
            except ServerError as e:
                last = e
                self._log("warn", "提交失败(%d/%d): %s"
                          % (attempt, self.submit_retries, e))
                if attempt < self.submit_retries:
                    time.sleep(3 * attempt)
        raise last

    def wait(self, pid, on_progress=None):
        """轮询直到任务结束。返回 (outputs, errors)。"""
        t0 = time.time()
        tick = 0
        while time.time() - t0 < self.timeout:
            time.sleep(self.poll_interval)
            tick += 1
            h = self._get("/history/" + pid)
            if pid not in h:
                if on_progress:
                    on_progress(tick, int(time.time() - t0))
                continue
            rec = h[pid]
            errs = [m for m in rec.get("status", {}).get("messages", [])
                    if m[0] == "execution_error"]
            outs = [a.get("filename") for o in rec.get("outputs", {}).values()
                    for a in o.get("audio", [])]
            if errs:
                raise JobFailed("任务执行失败: %s" % pid,
                                json.dumps(errs, ensure_ascii=False)[:600])
            return outs, rec
        raise JobFailed("等待超时(%ds): %s" % (self.timeout, pid))

    def object_info(self, class_type):
        try:
            return self._get("/object_info/" + class_type)
        except ServerError:
            return {}
