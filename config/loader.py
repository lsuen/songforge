# -*- coding: utf-8 -*-
"""配置加载与覆盖。

优先级：命令行 > 环境变量 > local.toml|json > default.toml|json > DEFAULTS。

算法配置用 TOML（与 ComfyUI workflow JSON 分流）。
styles.json / lexicon.json / baseline/*.json 仍是数据文件，保持 JSON。
本机绝对路径只写 local.toml（gitignore）。
"""
import copy
import json
import os
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(ROOT, "songforge", "config")

DEFAULTS = {
    "engine": {"server": "http://127.0.0.1:8188", "timeout_sec": 3600,
               "poll_interval_sec": 10, "submit_retries": 3},
    "model": {"checkpoint": "ace_step_1.5_turbo_swl_fused.safetensors",
              "lora": "swl_voice_best_ep257.safetensors",
              "lora_model_strength": 0.5, "lora_clip_strength": 0.5,
              "denied_checkpoints": ["yue2_3b_int8_convrot.safetensors",
                                    "ace_step_1.5_turbo_aio.safetensors"]},
    "sampler": {"shift": 5.5, "sampling": "flow", "steps": 28, "cfg": 2.0,
                "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0},
    "encode": {"cfg_scale": 3.0, "temperature": 0.68, "top_p": 0.88,
               "top_k": 50, "min_p": 0.02, "generate_audio_codes": True,
               "timesignature": "4"},
    "paths": {"workflow_template": "urbanhiphop-身体先听懂.json",
              "runs_dir": "songforge/data/runs",
              "tmp_dir": "songforge/data/tmp",
              "lyrics_dir": "songforge/input/lyrics",
              "qc_cache_dir": "songforge/cache/qc",
              "style_cache_dir": "songforge/cache/style",
              "index_dir": "songforge/index",
              "catalog_file": "songforge/index/catalog.jsonl",
              "baseline_dir": "songforge/config/baseline",
              "comfy_root": None,
              "qc_python": None,
              "music_dir": None,
              "output_prefix": "MyMusic",
              "output_format": "flac"},
    "song": {"language": "zh", "time_signature": "4",
             "duration_range": [150, 200],
             "duration_extra_ranges": [[100, 130]]},
    "lyrics": {"sing_bar_ratio": 0.70, "intro_bars": 8,
               "syllable_per_bar_min": 4.40, "syllable_per_bar_max": 7.80,
               "line_syllable_max": 12, "ideal_line_syllable": [5, 12],
               "profiles": {
                   "sparse": {
                       "sing_bar_ratio": 0.25,
                       "syllable_per_bar_min": 4.40,
                       "syllable_per_bar_max": 7.80,
                       "line_syllable_max": 12,
                       "ideal_line_syllable": [3, 12],
                   }
               }},
    "rules": {"trigger_word": "swlvoice", "intro_bars": 8},
}


def _deep_merge(base, patch):
    for k, v in patch.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _read_json(name):
    p = os.path.join(CONFIG_DIR, name)
    if not os.path.isfile(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _read_toml(name):
    p = os.path.join(CONFIG_DIR, name)
    if not os.path.isfile(p):
        return {}
    with open(p, "rb") as f:
        return tomllib.load(f)


def _read_layer(basename):
    """同名 .json 与 .toml：先 json 再 toml，toml 覆盖（迁移期兼容）。"""
    out = {}
    _deep_merge(out, _read_json(basename + ".json"))
    _deep_merge(out, _read_toml(basename + ".toml"))
    return out


def load(overrides=None):
    """返回最终配置 dict。overrides 是 {'sampler.shift': 5.5} 这种点号路径。"""
    cfg = copy.deepcopy(DEFAULTS)
    _deep_merge(cfg, _read_layer("default"))
    _deep_merge(cfg, _read_layer("local"))
    _deep_merge(cfg, _env_overrides())
    if overrides:
        for path, val in overrides.items():
            if val is None:
                continue
            _set(cfg, path, val)
    return cfg


def _set(cfg, path, val):
    keys = path.split(".")
    node = cfg
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = val


_ENV_MAP = {
    "SONGFORGE_SERVER": "engine.server",
    "SONGFORGE_TIMEOUT": "engine.timeout_sec",
    "SONGFORGE_CHECKPOINT": "model.checkpoint",
    "SONGFORGE_LORA": "model.lora",
    "SONGFORGE_LORA_STRENGTH": "model.lora_model_strength",
    "SONGFORGE_SHIFT": "sampler.shift",
    "SONGFORGE_STEPS": "sampler.steps",
    "SONGFORGE_CFG": "sampler.cfg",
    "SONGFORGE_ENC_CFG": "encode.cfg_scale",
    "SONGFORGE_TEMPERATURE": "encode.temperature",
    "SONGFORGE_TOP_P": "encode.top_p",
    "SONGFORGE_TOP_K": "encode.top_k",
    "SONGFORGE_MIN_P": "encode.min_p",
    "SONGFORGE_TEMPLATE": "paths.workflow_template",
    "SONGFORGE_RUNS": "paths.runs_dir",
    "SONGFORGE_TMP": "paths.tmp_dir",
    "SONGFORGE_LYRICS_DIR": "paths.lyrics_dir",
    "SONGFORGE_QC_CACHE": "paths.qc_cache_dir",
    "SONGFORGE_STYLE_CACHE": "paths.style_cache_dir",
    "SONGFORGE_OUTPUT_PREFIX": "paths.output_prefix",
    "SONGFORGE_LANGUAGE": "song.language",
    "SONGFORGE_COMFY_ROOT": "paths.comfy_root",
    "SONGFORGE_QC_PYTHON": "paths.qc_python",
    "SONGFORGE_MUSIC_DIR": "paths.music_dir",
}

_NUMERIC = {"engine.timeout_sec", "model.lora_model_strength",
            "model.lora_clip_strength", "sampler.shift", "sampler.steps",
            "sampler.cfg", "sampler.denoise", "encode.cfg_scale",
            "encode.temperature", "encode.top_p", "encode.top_k",
            "encode.min_p", "lyrics.sing_bar_ratio", "lyrics.intro_bars",
            "lyrics.syllable_per_bar_min", "lyrics.syllable_per_bar_max",
            "lyrics.line_syllable_max"}


def _env_overrides():
    out = {}
    for env_key, path in _ENV_MAP.items():
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        if path in _NUMERIC:
            try:
                raw = float(raw)
                if raw.is_integer() and path not in ("sampler.shift",
                                                     "sampler.cfg",
                                                     "encode.cfg_scale",
                                                     "encode.temperature",
                                                     "encode.top_p",
                                                     "encode.min_p",
                                                     "lyrics.sing_bar_ratio",
                                                     "model.lora_model_strength",
                                                     "model.lora_clip_strength"):
                    raw = int(raw)
            except ValueError:
                continue
        elif raw.lower() in ("true", "false"):
            raw = raw.lower() == "true"
        node = out
        keys = path.split(".")
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = raw
    return out


def styles():
    return _read_json("styles.json")


def lexicon():
    return _read_json("lexicon.json")


def baseline_path(name):
    """config/baseline/<name> 绝对路径。"""
    return os.path.join(CONFIG_DIR, "baseline", name)


def python_hint():
    """当前解释器信息（doctor 用）。"""
    return "%s (%s)" % (sys.executable, sys.version.split()[0])
