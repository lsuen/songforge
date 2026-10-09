# -*- coding: utf-8 -*-
"""songforge 核心包 - 工作流构建器"""

import copy
import json
import os
import re

from .errors import ConfigError
from . import paths as pmod

ROOT = pmod.PKG_ROOT

# UI widgets_values 顺序 -> API inputs 键名。按节点类型分别声明，避免混用。
WIDGET_MAP = {
    "CheckpointLoaderSimple": ["ckpt_name"],
    "LoraLoader": ["lora_name", "strength_model", "strength_clip"],
    "ModelSamplingAuraFlow": ["shift", "sampling"],
    "EmptyAceStep1.5LatentAudio": ["seconds", "batch_size"],
    "TextEncodeAceStepAudio1.5": [
        "tags", "lyrics", "seed", "bpm", "duration", "timesignature",
        "language", "keyscale", "generate_audio_codes", "cfg_scale",
        "temperature", "top_p", "top_k", "min_p",
    ],
    "KSampler": ["seed", "control_after_generate", "steps", "cfg",
                 "sampler_name", "scheduler", "denoise"],
    "SaveAudio": ["filename_prefix"],
    "SaveAudioAdvanced": ["filename_prefix", "format"],
    "VAEDecodeAudio": [],
}


def load_template(path_or_name, cfg=None):
    """path_or_name：仓内相对路径 / Comfy workflows 文件名 / 绝对路径。"""
    p = pmod.resolve_template(path_or_name, cfg=cfg)
    if not os.path.isfile(p):
        raise ConfigError(
            "workflow 模板不存在: %s（已试仓内 workflows/ 与 ComfyUI workflows）"
            % path_or_name)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def convert(ui_wf):
    """UI 格式 workflow dict -> API prompt dict。"""
    links = ui_wf["links"]
    # 兼容两种格式：ComfyUI 对象数组 (dict) 或 元组列表 [id, origin, target, ...]
    if links and isinstance(links[0], dict):
        link_src = {l["id"]: [l["origin_id"], l["origin_slot"]] for l in links}
    else:
        link_src = {l[0]: [l[1], l[2]] for l in links}
    api = {}
    for n in ui_wf["nodes"]:
        cls = n["type"]
        keys = WIDGET_MAP.get(cls)
        inputs = {}
        if keys:
            wv = n.get("widgets_values", [])
            if len(wv) < len(keys):
                raise ConfigError(
                    "节点 %s (%s) 的 widgets_values 只有 %d 项，期望 %d 项；"
                    "模板可能被手改过"
                    % (n.get("id"), cls, len(wv), len(keys)))
            for k, v in zip(keys, wv):
                inputs[k] = v
        for inp in n.get("inputs", []):
            lid = inp.get("link")
            if lid is not None and lid in link_src:
                src = link_src[lid]
                inputs[inp["name"]] = [str(src[0]), src[1]]
        api[str(n["id"])] = {"class_type": cls, "inputs": inputs}
    return api


def find_node(ui_wf, class_type):
    for n in ui_wf["nodes"]:
        if n["type"] == class_type:
            return n
    return None


def render(wf_result):
    """build_workflow 返回的 dict -> API prompt。"""
    if isinstance(wf_result, dict) and "workflow" in wf_result and "class_type" not in wf_result:
        return convert(wf_result["workflow"])
    return convert(wf_result)


def build_workflow(template, *, title, tags, lyrics, seed, bpm, duration,
                   keyscale, sampler, encode, model, output_prefix,
                   output_format, language="zh", timesignature="4"):
    """按节点类型定位并写入参数。不依赖固定节点 ID。

    model["lora"] 为空 / None 时直接跳过 LoraLoader：
    我们用的 ace_step_1.5_turbo_swl_fused.safetensors 本身就是
"""
    from ..config.loader import load
    from ..rules.ironlaw import build_tags as _build_tags, build_lyrics, validate
    from ..rules.ironlaw import build_chill_lyrics

    # 1. 解析歌词章节
    sections = []
    lines = lyrics.split('\n')
    current_tag = None
    current_content = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        tag_match = re.match(r'^\[(\w+(?:[-]\w+)?)\]$', line)
        if tag_match:
            if current_tag is not None and current_content:
                sections.append((current_tag, '\n'.join(current_content)))
            current_tag = tag_match.group(1)
            current_content = []
        else:
            if current_tag is not None:
                current_content.append(line)
    if current_tag is not None and current_content:
        sections.append((current_tag, '\n'.join(current_content)))

    has_intro = lyrics.lstrip().lower().startswith('[intro]')

    # 5. 组合工作流
    workflow = copy.deepcopy(template)

    # 从配置文件加载默认参数（single source of truth）
    config = load()
    model_cfg = config.get("model", {})
    sampler_cfg = config.get("sampler", {})
    encode_cfg = config.get("encode", {})
    paths_cfg = config.get("paths", {})

    # 合并参数来源: CLI/函数传入 > config > 硬coded 安全兜底
    ckpt_name = model.get("checkpoint") or model_cfg.get("checkpoint", "ace_step_1.5_turbo_swl_fused.safetensors")
    lora_name = model.get("lora") or model_cfg.get("lora", "")
    lora_model_strength = model.get("lora_model_strength", model_cfg.get("lora_model_strength", 0.5))
    lora_clip_strength = model.get("lora_clip_strength", model_cfg.get("lora_clip_strength", 0.5))

    shift = sampler.get("shift", sampler_cfg.get("shift", 5.5))
    sampling = sampler.get("sampling", sampler_cfg.get("sampling", "flow"))
    steps = sampler.get("steps", sampler_cfg.get("steps", 28))
    cfg = sampler.get("cfg", sampler_cfg.get("cfg", 2.0))
    sampler_name = sampler.get("sampler_name", sampler_cfg.get("sampler_name", "euler"))
    scheduler = sampler.get("scheduler", sampler_cfg.get("scheduler", "simple"))
    denoise = sampler.get("denoise", sampler_cfg.get("denoise", 1.0))
    batch_size = sampler.get("batch_size", sampler_cfg.get("batch_size", 1))

    cfg_scale = encode.get("cfg_scale", encode_cfg.get("cfg_scale", 3.0))
    temperature = encode.get("temperature", encode_cfg.get("temperature", 0.68))
    top_p = encode.get("top_p", encode_cfg.get("top_p", 0.88))
    top_k = encode.get("top_k", encode_cfg.get("top_k", 50))
    min_p = encode.get("min_p", encode_cfg.get("min_p", 0.02))
    generate_audio_codes = encode.get("generate_audio_codes", encode_cfg.get("generate_audio_codes", True))

    output_format = output_format or paths_cfg.get("output_format", "flac")

    # 遍历节点设置参数
    for n in workflow["nodes"]:
        if n["type"] == "CheckpointLoaderSimple":
            n["widgets_values"] = [ckpt_name]
        elif n["type"] == "LoraLoader":
            # 设置 LoRA 加载器参数
            if "widgets_values" in n:
                n["widgets_values"] = [lora_name, lora_model_strength, lora_clip_strength]
        elif n["type"] == "ModelSamplingAuraFlow":
            if "widgets_values" in n:
                n["widgets_values"] = [shift, sampling]
        elif n["type"] == "EmptyAceStep1.5LatentAudio":
            if "widgets_values" in n:
                n["widgets_values"] = [duration, batch_size]
        elif n["type"] == "TextEncodeAceStepAudio1.5":
            if "widgets_values" in n:
                # 构建 widgets_values: tags, lyrics, seed, bpm, duration, timesignature,
                # language, keyscale, generate_audio_codes, cfg_scale, temperature, top_p, top_k, min_p
                n["widgets_values"] = [
                    tags,
                    lyrics,
                    seed,
                    bpm,
                    duration,
                    timesignature,
                    language,
                    keyscale,
                    generate_audio_codes,
                    cfg_scale,
                    temperature,
                    top_p,
                    top_k,
                    min_p,
                ]
        elif n["type"] == "KSampler":
            if "widgets_values" in n:
                n["widgets_values"] = [seed, "fixed", steps, cfg,
                                        sampler_name, scheduler, denoise]
        elif n["type"] == "SaveAudioAdvanced":
            if "widgets_values" in n:
                n["widgets_values"] = [output_prefix, output_format]
        elif n["type"] == "SaveAudio":
            if "widgets_values" in n:
                n["widgets_values"] = [output_prefix]

    return {
        "success": True,
        "workflow": workflow,
        "lyrics": lyrics,
        "tags": tags,
        "has_intro": has_intro,
    }