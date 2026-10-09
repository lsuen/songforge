# -*- coding: utf-8 -*-
"""音频验收 worker —— 重活全在这个文件里，由 qc.py 用 ComfyUI venv 的
Python 拉起。本文件不参与 songforge 主链路，主 CLI 零重依赖。

为什么单独拆一个进程：
    songforge 主入口跑在系统 Python（3.10，无 librosa/torch）。
    demucs / whisper / CAMPPlus / librosa 只在 ComfyUI venv 里。
    硬要在主进程 import 会把整个 CLI 的可用性绑到音频栈上，
    等于把地基埋雷。所以：CLI 只负责组装 job、调度、渲染报告，
    真正的测量全部在 venv 侧的 worker 里完成。

判定思路（重要）：
    阈值不拍脑袋。凡是"多少算好"的指标，都先在 8 首用户已认可的老歌上
    跑一遍取基线，再按基线派生放行线。否则就会出现"新歌 0.47 算低吗"
    这种没法回答的问题——0.47 到底是差还是好，只有跟已认可作品比才知道。

指标踩过的坑（都记在这，别再走回头路）：
    1. demucs 的 vocals 轨有器乐泄漏。早期用"前 2s / 前 8s 人声能量占比"
       判开头，结果 8 首老歌这个值也是 0.82~1.36 —— 连用户已认可的歌都
       "纯人声开头"。指标本身失效，不是歌的问题。
       改用 Whisper 段级时间戳：ASR 在什么时间第一次唱出真汉字，
       那就是人声进入点。这直接对应"有没有真的在唱"，不受泄漏干扰。
    2. F0 漂移不能用"30s 块中位数的 max-min / mean"。老歌里 75% 的漂移
       全部来自无词段的垃圾音高（yin 在器乐残留上乱跳）。
       改：先按能量门限挑出有声帧再算 F0，漂移用半音表示，阈值从老歌派生。
    3. tempo 用人声轨 onset 包络也不稳（人声轨稀疏）。改从混音轨算，
       生成链路没有 time-stretch，混音 tempo 应当全歌恒定。
    4. tempo 必须做八度折叠。实测设定 86 BPM 的《晚风不加班》被报成
       170.5 BPM —— librosa 的 onset 包络在慢速律动上会锁到二倍拍。
       不折叠就直接和设定值比，这个风格会被判死。见 qc_style.fold_bpm。
    5. 声线不能只和"某一个参考音频"比余弦。参考音频是谁、录哪一版、
       干声还是混响，都会让这个数飘。改成与 8 首已认可老歌做最近邻：
       报告里直接说"最像 身体先听懂"，既有数值又人耳可解释。
    6. 光按"实义词数 >= 4"判真唱是不行的。Whisper 在纯器乐前奏上会幻觉出
       成片实义词（"L-E-D-O-N-O"、"Oh..."），已认可老歌也被压到 onset=0.0s。
       改用歌词正文重合判真唱：ASR 段的实义词有多少落在歌词正文里。
    7. 但"与歌词正文不重合"也不能直接判成生成指令泄漏——纯器乐幻觉同样
       与歌词零重合。必须再算一次与**真实生成链路** [Instrumental: ...]
       指令的重合率：指令命中高才是泄漏，两个都低才是幻觉。
       指令文本必须走 force_intro_bars -> filter_intro_bracket 复现，
       拿歌词文件里的手写括号当对照是错的（ironlaw 会自动追加配器短语）。
    8. 8 小节前奏是硬要求，闸门就不能只放到理论长度的 55%。
       早期 0.55 让人声在第 4~5 小节就进 sang 也算过，
       等于把铁律降级成建议。现在按完整 8 小节判，只留 ASR 时间戳
       自身的分辨率容差（onset_grace）。

依赖（全部在 C:\\comfyui-venv）：
    soundfile / librosa / torch / torchaudio / demucs / whisper
    CAMPPlus 取自 ComfyUI 的 custom_nodes/ComfyUI_Seed-VC
"""
import json
import os
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qc_style  # noqa: E402

# 8 首用户已认可的老歌，作为声线/ASR/音频指标基线。
# 歌词正文不在这里硬编码，由 tools/make_baseline.py 从 remake_v5.py 抽到
# qc_baseline_old.json —— 没有歌词，coverage() 恒为 0，基线就是假的。
BASELINE_OLD = [
    "亮灯就跳", "干饭魂", "快乐废物指南", "拜师记",
    "晒太阳", "晚风不加班", "跳起来", "身体先听懂",
]

# 物理上不成立的硬边界，与听感无关，可以写死。
HARD = dict(
    dur_tol=0.05,            # 时长与设定 ±5%
    sr_min=44100,            # 输出采样率下限
    peak_max=1.0,            # 峰值不得超过 0 dBFS
    lufs=(-25.0, -9.0),      # 近似响度区间
    silence_max=0.30,        # 静音占比上限
    f0=(60.0, 620.0),        # 音高范围（男女声都要覆盖到）
    bpm_rel_tol=0.18,        # 实测速度与设定 BPM 偏差上限
    # 8 小节前奏是铁律，闸门必须按完整 8 小节判。
    # 原来这里是 0.55，等于"第 4~5 小节进 sang 也算过"，把铁律降级成建议。
    # 只留 onset_grace 这一条容差，用来吸收 ASR 时间戳自身的分辨率误差
    # （Whisper 段落起点最多会早报 0.5s 左右），不是用来放水进水点的。
    intro_onset_rel=1.0,
    onset_grace=1.2,         # 秒，ASR 时间戳容差
    # ---- 歌词感知分类阈值（见文件头坑位 6/7）----
    # lyric_hit >= 0.34 -> 真唱；instr_hit >= 0.40 -> 生成指令泄漏。
    # 这两个数是在 8 首已认可老歌 + 本批 8 首上交叉验证出来的：
    # 真唱段落在 0.30~1.00，幻觉段与泄漏段在 0.00~0.10，中间没有重叠。
    lyric_hit=0.34,
    instr_hit=0.40,
)

_S = {}          # 懒加载单例，避免每首歌重复加载模型
_EMB_CACHE = {}  # 声线 embedding 进程内缓存，跨首复用


# ------------------------------------------------------------------ 基础

def _load_audio(path, sr=None):
    """读音频 -> (float32 mono ndarray, sr, channels)。sf 读不了才退回 librosa。"""
    import soundfile as sf
    try:
        x, s = sf.read(path, always_2d=True)
    except Exception:
        import librosa
        x, s = librosa.load(path, sr=None, mono=False)
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = x[:, None]
    y = x.mean(axis=1)
    return y, int(s), x.shape[1]


def _rms(y):
    if y.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(y)) + 1e-20))


def _blocks(y, sr, sec):
    """切成定长块，最后不足半块丢弃。"""
    n = int(sr * sec)
    if n <= 0 or y.size < n:
        return []
    k = y.size // n
    return [y[i * n:(i + 1) * n] for i in range(k)]


def _down(y, sr, target=16000):
    import librosa
    if sr == target:
        return y, sr
    return librosa.resample(y, orig_sr=sr, target_sr=target), target


# ------------------------------------------------------------------ 人声分离

def _demucs():
    if "demucs" not in _S:
        import torch
        from demucs.apply import apply_model
        from demucs.pretrained import get_model
        bag = get_model("htdemucs")
        # get_model 返回 BagOfModels，必须取子模型才有 samplerate/sources/segment
        sub = bag.models[0] if hasattr(bag, "models") else bag
        sub.to("cuda").eval()
        _S["demucs"] = (torch, apply_model, sub)
    return _S["demucs"]


def vocals_of(path, cache_dir=None):
    """分离人声轨。返回 (vocals_mono_44k, sr)。大文件结果缓存到 npy。

    注意：这条轨只用于"声线 embedding"和"有声帧挑选"，
    绝不用于判断开头有没有人声——它有器乐泄漏，会把器乐前奏误判成演唱。
    """
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cp = os.path.join(cache_dir, os.path.basename(path) + ".voc44.npy")
        if os.path.isfile(cp):
            return np.load(cp), 44100

    torch, apply_model, sub = _demucs()
    import torchaudio
    x, sr, _ch = _load_audio(path)
    wav = torch.from_numpy(x.copy()).float()[None]        # (1, T)
    if sub.samplerate != sr:
        # 必须用 torchaudio.functional.resample。torch.nn.functional 没有 resample，
        # 用 interpolate 变调不留情，会把 F0 和时长一起改掉，后面所有指标都失真。
        wav = torchaudio.functional.resample(
            wav, float(sr), float(sub.samplerate))
    # demucs 要 (batch, channels, time)，单声道补成双声道再取回单声道
    st = wav.repeat(1, 2, 1)
    with torch.no_grad():
        out = apply_model(sub, st, device="cuda", split=True, overlap=0.25,
                          segment=7.8, shifts=0, progress=False)[0]
    vi = list(sub.sources).index("vocals")
    v = out[vi].mean(dim=0).cpu().numpy().astype(np.float32)
    v = np.nan_to_num(v)
    if cache_dir:
        np.save(cp, v)
    return v, 44100


# ------------------------------------------------------------------ 声线

def _campplus():
    """CAMPPlus 声线 embedding 器。取自 ComfyUI 自带的 Seed-VC。"""
    if "campplus" in _S:
        return _S["campplus"]
    import torch
    root = os.environ.get("SONGFORGE_COMFY_ROOT") or os.environ.get(
        "COMFY_ROOT")
    if not root:
        raise RuntimeError(
            "缺少 SONGFORGE_COMFY_ROOT（由 songforge qc 注入，或写在 "
            "config/local.toml 的 paths.comfy_root）")
    for p in (root, os.path.dirname(os.path.abspath(__file__))):
        sys.path.insert(0, p)
        sys.path.insert(0, os.path.join(p, "custom_nodes",
                                        "ComfyUI_Seed-VC"))
        break
    import folder_paths
    from seed_vc.modules.campplus.DTDNN import CAMPPlus
    bin_path = os.path.join(folder_paths.models_dir, "TTS", "Seed-VC",
                            "campplus_cn_common.bin")
    cm = CAMPPlus(feat_dim=80, embedding_size=192)
    cm.load_state_dict(torch.load(bin_path, map_location="cpu"), strict=False)
    cm.eval().to("cuda")
    _S["campplus"] = (torch, cm)
    return _S["campplus"]


def embed(sig, rate):
    """任意采样率的单声道信号 -> 归一化 192 维声线 embedding。"""
    import torch
    import torchaudio
    torch, cm = _campplus()
    t = torch.tensor(np.asarray(sig, dtype=np.float32)).reshape(-1)
    if rate != 16000:
        t = torchaudio.functional.resample(t[None], float(rate), 16000)[0]
    if t.numel() < 400:
        return torch.zeros(192)
    f = torchaudio.compliance.kaldi.fbank(t[None], num_mel_bins=80,
                                          dither=0, sample_frequency=16000)
    f = f - f.mean(0, keepdim=True)
    with torch.no_grad():
        e = cm(f[None].to("cuda"))
    return torch.nn.functional.normalize(e, dim=-1)[0].detach().cpu()


def cos(a, b):
    import torch
    na = float(torch.linalg.norm(a))
    nb = float(torch.linalg.norm(b))
    if na < 1e-8 or nb < 1e-8:
        return 0.0
    return float(torch.dot(a.reshape(-1), b.reshape(-1)) / (na * nb))


# ------------------------------------------------------------------ ASR

_CJK = None

# Whisper 会自动检测语种并把英文转写出来，但下面几个指标历史上全是按
# 汉字设计的，英文歌会整批归零。实测症状：
#     language 固定 "zh"          -> 英文歌被强行按中文识别，召回恒为 0
#     cjk() 只留 U+4E00~U+9FFF   -> 英文实义词一个都留不下
#     sung_segs() 固定 4 个汉字   -> "Bounce with me" 永远凑不够，进声点 None
# 所以抽词要按语种走：中文取汉字，英文取单词。
#
# 注意两个口径必须分开，不能共用一张停用词表：
#     real_words()    歌词召回用，数字词 one/four 必须留下
#                     （《HitItOnOne》整首 hook 就是 "Hit it on one"）
#     content_words() 判人声进入点用，再滤掉 mm/yeah/okay 这类哼唱衬词


# 简繁归一表。
#
# 不引 opencc / zhconv：ComfyUI venv 里没有，装一个几十 MB 的包只为了
# 折两三百个常用字不划算。而且两侧都过同一张表，所以不需要完整映射——
# 只有"歌词里可能写、Whisper 也可能吐"的那批繁体字需要登记。
# 没登记的繁体字原样保留，两侧仍然各自一致，不影响比对。
#
# 为什么需要：Whisper 的 zh 输出不保证全简体。同一句可能一侧写"节奏"、
# 另一侧写"節奏"，不归一就永远对不上，歌词召回被凭空压低几个点。
_ZH_T2S = {
    "們": "们", "個": "个", "來": "来", "時": "时", "對": "对", "過": "过",
    "說": "说", "這": "这", "裡": "里", "後": "后", "會": "会", "學": "学",
    "動": "动", "樂": "乐", "響": "响", "點": "点", "頭": "头", "問": "问",
    "沒": "没", "還": "还", "讓": "让", "從": "从", "樣": "样", "開": "开",
    "關": "关", "當": "当", "兩": "两", "臺": "台", "誰": "谁", "麼": "么",
    "為": "为", "無": "无", "愛": "爱", "親": "亲", "覺": "觉", "聽": "听",
    "見": "见", "邊": "边", "遠": "远", "風": "风", "飛": "飞", "飯": "饭",
    "燈": "灯", "舊": "旧", "膠": "胶", "體": "体", "長": "长", "節": "节",
    "聲": "声", "調": "调", "輕": "轻", "彈": "弹", "雙": "双", "腳": "脚",
    "擺": "摆", "靜": "静", "間": "间", "線": "线", "順": "顺", "鬆": "松",
    "緊": "紧", "穩": "稳", "陽": "阳", "夢": "梦", "記": "记", "憶": "忆",
    "橋": "桥", "樓": "楼", "鏡": "镜", "髮": "发", "臉": "脸", "睛": "睛",
    "進": "进", "詞": "词", "話": "话", "給": "给", "帶": "带", "牽": "牵",
    "掛": "挂", "齊": "齐", "備": "备", "認": "认", "結": "结", "剛": "刚",
    "際": "际", "衝": "冲", "擊": "击", "隨": "随", "盪": "荡", "搖": "摇",
    "數": "数", "兒": "儿", "勁": "劲", "帥": "帅", "斃": "毙", "錯": "错",
    "係": "系", "領": "领", "寶": "宝", "貝": "贝", "歲": "岁", "嗎": "吗",
    "喲": "哟", "唷": "哟", "噠": "哒", "嘩": "哗", "噓": "嘘", "噢": "哦",
    "嘍": "喽", "唄": "呗", "嘸": "呒", "誰": "谁", "壞": "坏", "傷": "伤",
    "熱": "热", "涼": "凉", "涼": "凉", "願": "愿", "驚": "惊", "險": "险",
    "離": "离", "電": "电", "腦": "脑", "機": "机", "車": "车", "轉": "转",
    "運": "运", "遊": "游", "過": "过", "還": "还", "邊": "边", "層": "层",
    "屬": "属", "歲": "岁", "組": "组", "網": "网", "總": "总", "係": "系",
    "積": "积", "極": "极", "業": "业", "參": "参", "雙": "双", "發": "发",
    "變": "变", "葉": "叶", "號": "号", "嘆": "叹", "嚴": "严", "華": "华",
    "處": "处", "備": "备", "復": "复", "夠": "够", "夠": "够", "捨": "舍",
    "捨": "舍", "鬧": "闹", "齊": "齐", "讀": "读", "變": "变", "讓": "让",
    "讚": "赞", "贊": "赞", "贈": "赠", "贏": "赢", "賽": "赛", "踐": "践",
}


def _norm_zh(text):
    """繁体 -> 简体。只走常用字表，覆盖歌词和 Whisper 实际会出现的范围。"""
    return "".join(_ZH_T2S.get(c, c) for c in text)


def cjk(text):
    """只保留汉字。Whisper 会在汉字之间塞标点/空格，比对前必须洗掉。

    顺带做简繁归一，见 _ZH_T2S 的说明。
    """
    return _norm_zh("".join(c for c in text if "\u4e00" <= c <= "\u9fff"))


def _norm_en(text):
    """英文归一化：数字统一成单词。

    Whisper 常把 "one two three" 写成 "1 2 3"，歌词原文是英文单词。
    不归一化的话这两边永远对不上，召回会被凭空压低。

    坑位：词表里虽然写了 ten，但下面用 enumerate(digits) 生成替换表，
    只覆盖下标 0~9，ten 根本没有被处理。"10" 在英文歌里不多见，
    但漏掉就是漏掉——这里显式多处理一位。
    """
    import re
    digits = ("zero one two three four five six seven eight nine ten").split()
    low = text.lower()
    for i, w in enumerate(digits):
        low = re.sub(r"\b%d\b" % i, " " + w + " ", low)
    low = re.sub(r"\b10\b", " ten ", low)
    return low



def _en_words(text):
    """英文取小写单词，丢掉标点，数字先归一化成单词。"""
    import re
    return re.findall(r"[a-z']+", _norm_en(text))


def tokens(text, lang="zh"):
    """按语种抽出可比对的实义单元。

    zh -> 汉字（历史口径，保持不变）
    en -> 小写单词

    分语种的原因不是"照顾英文"，而是中文那套汉字口径对英文恒为 0：
    cjk() 过滤后英文串必然为空，coverage 分母变 0、分子变 0。
    """
    if _is_en(lang):
        return _en_words(text)
    return list(cjk(text))


def _is_en(lang):
    return str(lang or "zh").lower().startswith("en")


# 英文里 Whisper 常见的功能词，不承载歌词内容。
#
# 关键：数字词 one~ten 刻意不放进来。它们在这批英文歌里是动作词本身——
# 《HitItOnOne》整首 hook 就是 "Hit it on one / drop on four"，
# 把 one/four 当停用词等于把主打句从召回分母里删掉。
EN_STOP = frozenset("""
a an the and or but if then than that this these those is are was were be been
being am do does did doing done have has had having i you he she it we they me
him her us them my your his its our their of in on at to for with from by as
into over under again very so no yes
""".split())

# 只有"判定人声进入点"时才额外剔掉的哼唱/附和词。
# 口径必须和歌词召回分开：mm / yeah / okay 这类在歌里是 legit 的
# 律动衬词，但绝不能算成"真的唱出内容了"。
EN_FILLER = frozenset("""
oh ah uh um mm hmm mhm okay ok yeah yep
""".split())


def _whisper():
    if "whisper" not in _S:
        import whisper
        _S["whisper"] = whisper.load_model("small", device="cuda")
    return _S["whisper"]


def asr(wav, sr, lang="zh"):
    """对整首歌做 ASR，返回 whisper 的完整 result（含分段时间戳）。

    坑位记录：不要把音频写成临时 wav 再让 whisper 读。whisper 内部走 ffmpeg
    子进程，遇到它不认识的封装/采样格式就直接
        Error opening input: Invalid data found when processing input
    然后返回空结果——不报错，静默变成"歌词召回 0%"，比没有 QC 更可怕。
    直接传 numpy 数组，whisper 会走 soundfile/内部解码，绕开 ffmpeg。

    lang 不能硬编码 "zh"：那样 Whisper 会把英文歌强行按中文解码，
    输出整片音译汉字或空串。英文歌必须传 "en"。
    """
    wm = _whisper()
    w = np.asarray(wav, dtype=np.float32)
    if w.ndim > 1:
        w = w.mean(axis=1)
    if sr != 16000:
        w, _ = _down(w, sr, 16000)
    if w.size < 16000:
        return dict(text="", segments=[])
    r = wm.transcribe(w, language=lang, fp16=True, beam_size=1,
                      temperature=0.0, condition_on_previous_text=False)
    segs = []
    for s in r.get("segments", []) or []:
        t = (s.get("text") or "").strip()
        n = len(tokens(t, lang))
        if n:
            segs.append(dict(t0=float(s.get("start", 0.0)),
                             t1=float(s.get("end", 0.0)),
                             text=t, chars=n,
                             # 置信度字段保留在结果里供排查，但**不再作为
                             # 分类主判据**：实测泄漏段与真唱段的
                             # no_speech_prob 相同或更高，分不开。
                             # 现在的判据是 classify_segs() 里的歌词正文
                             # 重合 + 实证泄漏短语。
                             nsp=float(s.get("no_speech_prob", -1.0)),
                             logp=float(s.get("avg_logprob", 0.0))))
    return dict(text=(r.get("text") or "").strip(), segments=segs)


# 语气词/无意义哼唱。Whisper 对纯伴奏段落会吐出成百上千个"嗯""啊"，
# 实测 pop 前奏被吐 112 个"嗯"、hiphop 前奏 223 个"啊"，去重后只有 1 个
# 字符。这些既不能算歌词，也不能算人声进入点。
FILLER = set("嗯啊哦噢喔呃哎唉嘿咦哟唔唷哼嘻嘿")


def real_cjk(text):
    """去掉语气词后的实义汉字。"""
    return "".join(c for c in cjk(text) if c not in FILLER)


def _strip_tags(text):
    """去掉结构标签方括号里的内容（[chorus] / [Instrumental: ...] / [intro]）。

    中文口径不需要这一步：cjk() 只保留 U+4E00~U+9FFF，ASCII 标签
    天然被扔掉。英文口径是"取单词"，标签里的英文会整个活下来——
    实测 [chorus] 把 chorus、intro 这些根本不会唱出来的词塞进分母，
    召回率被凭空压低一截。
    与 lyrics/meter.py 的 only_lyrics() 同一口径：结构标签不计入正文。
    """
    import re
    return re.sub(r"\[[^\]]*\]", " ", text)


def real_words(text, lang="zh"):
    """按语种抽实义单元：中文去语气词，英文去功能词和结构标签。

    这是"歌词召回"口径：one/four 这类动作词要留下，因为它们是歌词内容。
    判人声进入点请用 content_words()，那份会再滤掉哼唱衬词。
    """
    if _is_en(lang):
        return [w for w in _en_words(_strip_tags(text))
                if w not in EN_STOP]
    return list(real_cjk(text))


def content_words(text, lang="zh"):
    """判定"真的唱出内容了"的严格口径：在 real_words 基础上再滤哼唱词。"""
    if _is_en(lang):
        return [w for w in real_words(text, lang) if w not in EN_FILLER]
    return list(real_cjk(text))


# 一段要算"真的唱出字"，至少要几个实义单元。
#
# 中文 4 个汉字 ≈ 4 音节。英文一个词约 1.8~2 音节（见 lyrics/meter.py
# 里 en * 2 的算法），所以英文门槛必须比 4 低，否则
#     "Bounce, bounce, bounce with me"  去功能词后只剩 3 个
# 会被判成"没在唱"，人声进入点直接变 None。
# 用 3 个实词 ≈ 5~6 音节，和中文 4 字口径相当。
MIN_UNITS = {"zh": 4, "en": 3}


def min_units(lang):
    return MIN_UNITS.get("en" if _is_en(lang) else "zh", 4)


# ------------------------------------------------------------------ 歌词感知分类
#
# 这一段是本轮最关键的修正。原来的 sung_segs() 只看"实义单元数 >= 阈值"，
# 在纯器乐前奏上 Whisper 会幻觉出成片实义词，于是已认可老歌也被判成
# onset=0.0s，看起来像全部 8 首都违规，实际上是指标失效。
#
# 正确的判据要把一段 ASR 同时放到两个集合里比：
#
#     lyric_hit = 该段实义词落在**歌词正文**里的比例   -> 是不是真的在唱
#     instr_hit = 该段实义词落在**真实生成链路 [Instrumental: ...] 指令**
#                 里的比例                          -> 是不是把指令唱出来了
#
# 两种错误必须分开：
#     lyric_hit 低 + instr_hit 低  -> 纯器乐幻觉，无害，不参与任何判定
#     lyric_hit 低 + instr_hit 高  -> 生成指令泄漏，必须重生成
#     lyric_hit 高                 -> 真唱，拿它算 onset / F0 / head count
#
# 指令集合必须走 force_intro_bars -> filter_intro_bracket 复现真实链路，
# 拿歌词文件里的手写括号当对照是错的：ironlaw 会自动补 kick/bass 兜底和
# every-beat 层，这些词根本不在作者手写文件里。Bounce 的泄漏正是如此。


def _lyric_pool(lyrics, lang):
    """歌词正文集合（去掉结构标签）。"""
    return set(content_words(_strip_tags(lyrics or ""), lang))


def _ironlaw():
    """拿到 songforge.rules.ironlaw 模块。worker 是被当脚本直接跑的，
    没有包上下文，所以只能按文件位置把 workflows 目录塞进 sys.path。

    找不到就返回 None —— 判定逻辑会退回到"只看歌词重合"，
    不会因为拿不到 ironlaw 就整首歌崩掉。
    """
    global _IRONLAW
    if _IRONLAW is None:
        try:
            wf = os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))
            if wf not in sys.path:
                sys.path.insert(0, wf)
            from songforge.rules import ironlaw as _il
            _IRONLAW = _il
        except Exception:
            _IRONLAW = False
    return _IRONLAW or None


_IRONLAW = None


def _instr_pool(lyrics, bars=8):
    """真实生成链路最终送进模型的前奏指令词集合。

    走真实链路而不是读原始括号，原因见上面的说明。
    指令是英文自然语言，所以固定用英文词表比对，中文歌也一样。
    """
    il = _ironlaw()
    if il is None or not lyrics:
        return set()
    try:
        fixed = il.force_intro_bars(lyrics, bars)
        out = il.filter_intro_bracket(fixed)
    except Exception:
        return set()
    import re as _re
    m = _re.search(r"^\[Instrumental:(.*)\]$", out, _re.I | _re.M)
    if not m:
        return set()
    return set(_en_words(m.group(1)))


def _hit_ratio(text, pool, lang):
    """该段实义词落在 pool 里的比例。段太短或 pool 空时返回 0。

    下限 3 个实义单元：Whisper 常切出 1~2 词的碎片段，
    这种段的命中比例噪声极大，"the" 落在歌词里就能算出 100%，
    拿它判真唱或泄漏都是自欺。
    """
    a = content_words(text, lang)
    if len(a) < 3 or not pool:
        return 0.0
    return sum(1 for w in a if w in pool) / float(len(a))


def _phrase_leak(text):
    """ASR 段是否直接复述了实证泄漏过的生成指令措辞。

    为什么集合重合不够：ironlaw 修好之后，"no fading in, no swelling in"
    这类句子已经不会再进歌词括号，于是 ipool 里不再有 fading / swelling /
    quiet 这些词。拿修好后的链路当对照，去查修复前生成的旧候选就会
    全部漏判——ipool 越干净，历史泄漏越查不出来。

    所以这里复用 ironlaw 自己那份实证短语正则：只要 ASR 里出现
    "front and center"、"at full level"、"quiet or sparse" 这类
    从成品里逐词抄回来的措辞，就直接判泄漏，不依赖 ipool。
    """
    il = _ironlaw()
    rx = getattr(il, "_INTRO_LEAK_PHRASE_RE", None) if il else None
    if rx is None or not text:
        return ""
    try:
        hits = rx.findall(text)
    except Exception:
        return ""
    if not hits:
        return ""
    return ", ".join(sorted(set(str(h).lower() for h in hits)))


def classify_segs(segs, lyrics="", lang="zh", bars=8):
    """给每个 ASR 段打上类别，并返回可直接用的三组。

    返回 dict:
        segs     原段，每段多出 role / lyric_hit / instr_hit
        sung     真唱段（lyric_hit >= HARD["lyric_hit"]）
        leaks    指令泄漏段（instr_hit >= HARD["instr_hit"] 且不是真唱）
        halluc   纯幻觉段（两个都不高）
    """
    lpool = _lyric_pool(lyrics, lang) if lyrics else set()
    ipool = _instr_pool(lyrics, bars) if lyrics else set()
    sung, leaks, halluc = [], [], []
    for s in segs or []:
        t = s.get("text", "")
        # 中文歌的前奏指令是英文，但 Whisper 可能把英文唱成音译汉字；
        # 中文段仍按中文比对歌词，同时按英文比对指令，两个口径互不干扰。
        rl = _hit_ratio(t, lpool, lang)
        ri = _hit_ratio(t, ipool, "en")
        phrase = _phrase_leak(t)
        s["lyric_hit"] = round(rl, 3)
        s["instr_hit"] = round(ri, 3)
        s["leak_phrase"] = phrase
        if phrase:
            # 实证措辞优先：这段直接把生成指令唱出来了，
            # 哪怕它碰巧也命中了几个歌词词，也不能算真唱。
            role = "leak"
        elif not lpool and not ipool:
            # 没歌词可比（--file 模式常见）：退回到旧口径，
            # 只按实义单元数判。宁可宽松也不要凭空虚报幻觉。
            role = "sung" if len(content_words(t, lang)) >= min_units(lang) \
                else "halluc"
        elif rl >= HARD["lyric_hit"]:
            role = "sung"
        elif ri >= HARD["instr_hit"]:
            role = "leak"
        else:
            role = "halluc"
        s["role"] = role
        {"sung": sung, "leak": leaks, "halluc": halluc}.get(
            role, halluc).append(s)
    return dict(segs=list(segs or []), sung=sung, leaks=leaks,
                halluc=halluc, lpool=lpool, ipool=ipool)


def sung_segs(segs, min_chars=None, lang="zh", lyrics=None, bars=8):
    """只保留"真的唱出字"的段。

    传了 lyrics 就走歌词感知口径（推荐）；没传则退回旧的实义单元数口径，
    保持对既有调用方的兼容。
    """
    if lyrics is not None:
        return classify_segs(segs, lyrics, lang, bars)["sung"]
    if min_chars is None:
        min_chars = min_units(lang)
    out = []
    for s in segs or []:
        if len(content_words(s.get("text", ""), lang)) >= min_chars:
            out.append(s)
    return out


def vocal_onset(segs, min_chars=None, lang="zh", lyrics=None, bars=8):
    """人声进入点：第一个"真的唱出字"的段起点。

    为什么不用能量：Whisper 会在纯伴奏段落输出空或单字噪声，
    要求该段实义单元数 >= 阈值才算真唱，配合时间戳就能定位演唱起点。

    坑位（已修）：光按实义单元数会中 Whisper 幻觉的招——器乐前奏上
    它能吐出 "L-E-D-O-N-O" 这种成片实义词，把 onset 压到 0.0s。
    已认可老歌也被压到 0.0s，说明是指标失效而不是歌的问题。
    所以 lyrics 存在时必须走 classify_segs() 的歌词正文重合判据。

    返回 None 表示整首歌没识别出真唱（歌词完整度会一并判 0）。
    """
    for s in sung_segs(segs, min_chars, lang, lyrics=lyrics, bars=bars):
        return round(s["t0"], 2)
    return None


def _collapse_runs(s, max_run=3):
    """把同一字符的连续重复压到最多 max_run 次。

    坑位：Whisper 在吟唱/哨音段会吐出上百个"呜"。SequenceMatcher 是
    按顺序找最长公共子串，一段上百字的噪声会把歌词切成两半对齐，
    召回率被凭空压低十几个点。这里先把复读折叠掉。
    """
    out = []
    run = 1
    for i, ch in enumerate(s):
        if i and ch == s[i - 1]:
            run += 1
            if run > max_run:
                continue
        else:
            run = 1
        out.append(ch)
    return "".join(out)


def coverage(asr_text, lyrics, lang="zh"):
    """ASR 召回率：ASR 里命中的歌词单元 / 歌词正文单元。

    为什么用召回而不是相似度：Whisper 对中文演唱常输出"嗯嗯嗯"这类无意义
    重复，也会把长句压短。相似度会被这些噪声压低，而召回只看"歌词到底唱出来
    没有"，对噪声不敏感。配合老歌基线一起看就稳。

    坑位：ASR 文本要先去掉语气词和复读再对齐，否则器乐前奏和吟唱段的
    幻觉字会切断歌词匹配，实测让召回率虚低。

    英文必须按词计（lang="en"）：中文那套 cjk()+SequenceMatcher 对英文
    恒为 0——cjk() 过滤后两边都是空串，分子分母全灭。词级召回同样直接，
    只是分母从"字数"变成"词数"，阈值含义不变。
    """
    import difflib
    if _is_en(lang):
        a = real_words(asr_text, lang)
        e = real_words(lyrics, lang)
        if not e or not a:
            return 0.0
        sm = difflib.SequenceMatcher(None, e, a, autojunk=False)
        hit = sum(b.size for b in sm.get_matching_blocks())
        return hit / float(len(e))

    a = _collapse_runs(real_cjk(asr_text))
    e = cjk(lyrics)
    if not e or not a:
        return 0.0
    sm = difflib.SequenceMatcher(None, e, a, autojunk=False)
    hit = sum(b.size for b in sm.get_matching_blocks())
    return hit / float(len(e))


# ------------------------------------------------------------------ 单曲测量

def _f0_voiced(v16, vsr16, floor_db=-34.0):
    """只在有声帧上算 F0。

    坑位一：直接对整轨 yin，无词段/器乐残留会给出乱七八糟的音高，
    30s 块中位数漂移能到 75%，全是噪声。这里先用短时能量门限把
    真正在发声的地方挑出来，再交给 yin。

    坑位二：门限不能写成 max(floor_db, percentile(db, 60))。percentile
    是按秩取值的，keep 恒等于 1-0.60 = 40% 的帧——它不是能量门，而
    是一个固定百分位选择器。实测三首新歌的有声帧占比精确等于 40.0%，
    41% 的无人声尾奏照样被标成"发声"，yin 在上面测出 368/414 Hz 的
    假音高，一首歌的 F0 漂移被撑到 9.6 半音。必须用绝对 dB 门限。
    """
    import librosa
    if v16.size <= 2048:
        return np.array([0.0]), np.array([0.0])
    hop = 512
    rms = librosa.feature.rms(y=v16, frame_length=2048, hop_length=hop)[0]
    if rms.size == 0:
        return np.array([0.0]), np.array([0.0])
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    # 绝对门限，固定 -34dBFS；再放宽到峰值下方 floor_db，两者取高。
    abs_gate = db > floor_db
    peak_gate = db > float(db.max()) + floor_db
    keep = abs_gate | peak_gate
    if keep.mean() > 0.92:
        # 整首几乎都在响（老歌混音偏响），门限失去区分力，退回软门限但
        # 仍按能量排序取绝对上限，绝不回到固定 40%。
        keep = db > np.percentile(db, 8)
    f0 = librosa.yin(v16, fmin=HARD["f0"][0], fmax=HARD["f0"][1],
                     sr=vsr16, frame_length=2048, hop_length=hop)
    n = min(f0.size, keep.size)
    return f0[:n], keep[:n]


def measure(path, expect=None, cache_dir=None, with_asr=True):
    """测一首。expect 里可带 duration/bpm/intro_bars/lyrics/title/group。

    lang 也从 expect 里取（"zh" / "en"），决定 ASR 语种和歌词召回口径。
    """
    import librosa
    expect = expect or {}
    lang = expect.get("lang") or expect.get("language") or "zh"
    y, sr, ch = _load_audio(path)
    dur = len(y) / float(sr)

    peak = float(np.max(np.abs(y))) if y.size else 0.0
    bs = 3 * sr
    n = max(1, y.size // bs)
    rmss = [_rms(y[i * bs:(i + 1) * bs]) for i in range(n)]
    lufs = 20 * np.log10(max(float(np.mean(rmss)), 1e-9)) - 0.691
    dyn = 20 * np.log10(max(max(rmss), 1e-9) / max(min(rmss), 1e-9))
    silence = float(np.mean(np.abs(y) < 0.01))

    y16, sr16 = _down(y, sr)
    centroid = float(np.mean(librosa.feature.spectral_centroid(y=y16, sr=sr16)))
    rolloff = float(np.mean(librosa.feature.spectral_rolloff(
        y=y16, sr=sr16, roll_percent=0.85)))

    # ---- 速度：从混音轨算，且做八度折叠
    # 生成链路没有 time-stretch，混音 tempo 应当全歌恒定。
    # 折叠是必须的：librosa 在慢速律动上会锁到二倍拍，
    # 不折叠的话设定 86 BPM 会被报成 170 BPM，直接判死。
    tempo_med, tempo_drift = qc_style.tempo_of(
        y, sr, expect_bpm=expect.get("bpm"))
    tempo_raw = 0.0
    if not expect.get("bpm"):
        tempo_raw = tempo_med

    # ---- 人声轨：只用来做声线 embedding 和挑有声帧
    v, vsr = vocals_of(path, cache_dir)
    v16, vsr16 = _down(v, vsr)

    # ---- 人声进入点：靠 ASR 时间戳，不靠能量（见文件头坑位 1）
    lyr = expect.get("lyrics", "")
    bars = int(expect.get("intro_bars") or 8)
    txt, segs = ("", [])
    if with_asr:
        a = asr(v, vsr, lang)
        txt, segs = a["text"], a["segments"]
    # 歌词感知分类：真唱 / 指令泄漏 / 纯幻觉，三者必须分开。
    # 后面所有依赖"人在哪、唱了什么"的指标都只用真唱段。
    cls = classify_segs(segs, lyr, lang, bars) if with_asr else \
        dict(segs=[], sung=[], leaks=[], halluc=[])
    trusted = cls["sung"]
    onset = vocal_onset(segs, lang=lang, lyrics=lyr if with_asr else None,
                        bars=bars)
    # 前 2s / 8s 内是否有真歌词（铁律：禁止纯人声/spoken 开头）
    # 坑位：只数真唱段里的实义单元。Whisper 在器乐前奏上会幻觉出上百个
    # "嗯"，甚至 "L-E-D-O-N-O" 这种成片实义词；不筛就会把干净前奏判成
    # "开头 112 字"，逼你重生成一首没问题的歌。
    head_chars_2 = sum(len(content_words(s["text"], lang))
                       for s in trusted if s["t0"] < 2.0)
    head_chars_8 = sum(len(content_words(s["text"], lang))
                       for s in trusted if s["t0"] < 8.0)
    # 泄漏段单列。它不是幻觉，也不是歌词，必须独立报出来：
    # 这是"铁律被破坏"的证据，只能靠重生成修，QC 口径改不动。
    leak_segs = [dict(t0=round(s["t0"], 2), t1=round(s["t1"], 2),
                      instr_hit=s.get("instr_hit"),
                      text=s["text"][:80].replace("\n", " "))
                 for s in cls["leaks"]]

    # ---- 音高稳定性：只在真唱段上测，且只用段内音高稳定的帧
    # 坑位一：不能按 30s 整块取中位。块里必然混进器乐间奏和吟唱段，
    # 实测 pop 的 115~145s 是 50 个"呜"的吟唱，yin 在上面给出 437Hz，
    # 单这一块就把全歌 F0 漂移从 1.4 半音撑到 9.6 半音。
    # 坑位二：漂移用 MAD 而不是 P90-P10。吟唱/换声是少数段，极差法
    # 对它们最敏感；MAD 只反映"整体音高是否偏移"，正好对上
    # "声线、速度、调性中途不变化"这条铁律。
    f0, keep = _f0_voiced(v16, vsr16)
    f0v = f0[keep] if f0.size == keep.size else f0
    f0v = f0v[np.isfinite(f0v) & (f0v > 0)]
    hop = 512
    seg_med = []
    seg_f0 = []
    for s in trusted:
        i0 = max(0, int(s["t0"] * vsr16 / hop))
        i1 = min(len(f0), int(s["t1"] * vsr16 / hop))
        if i1 - i0 < 8:
            continue
        sv = f0[i0:i1]
        sv = sv[np.isfinite(sv) & (sv > 0)]
        if sv.size < 8:
            continue
        # 段内音高跨度超过 6 个半音，说明这段是吟唱/滑音/残留，不是
        # 一个稳定的 singing 音高，跳过。
        inner = float(np.percentile(sv, 90) - np.percentile(sv, 10))
        if inner >= 60.0:
            continue
        seg_med.append(float(np.median(sv)))
        seg_f0.append(sv)
    if len(seg_med) >= 3:
        arr = np.array(seg_med)
        base = max(float(np.median(arr)), 1e-9)
        st = 12.0 * np.log2(arr / base)
        f0_drift_st = float(np.median(np.abs(st)))
    else:
        # 拿不到 3 个稳定段就退回 0 并标记不可信，由judge 决定要不要卡。
        f0_drift_st = 0.0

    # 音高范围必须和漂移用同一批帧（真唱稳定段），不能用全曲有声帧。
    # 坑位：全曲口径下 yin 会在 demucs 残留上撞自己的 fmax 上限。实测
    # 朋克有 4.49% 的帧报 640Hz（fmax 就是 620，报 640 是饱和不是音高），
    # 而老歌"跳起来"同样出现 640，只是 p2 恰好落在界内才没被卡——
    # 说明这是口径缺陷不是朋克的毛病。只用真唱段后朋克 p98 从 640 降到 343。
    if seg_f0:
        f0v = np.concatenate(seg_f0)

    # ---- 声线自相似：前半 vs 后半，直接抓"中途换声线"
    half = v.size // 2
    e_all = embed(v, vsr)
    e_self = cos(embed(v[:half], vsr), embed(v[half:], vsr)) \
        if half > vsr else 0.0

    cov = coverage(txt, lyr, lang) if with_asr else 0.0

    # ---- 风格分类：只看混音轨，不掺 demucs（见 qc_style 文件头坑位 4）
    svec, sraw = qc_style.features(path, expect_bpm=expect.get("bpm"))

    res = dict(
        file=os.path.basename(path), path=os.path.abspath(path),
        title=expect.get("title") or os.path.basename(path),
        group=expect.get("group", "new"),
        style=expect.get("style", ""),
        lang=lang,
        duration=round(dur, 2), sample_rate=sr, channels=ch,
        peak=round(peak, 4), lufs=round(lufs, 2), dynamic_db=round(dyn, 2),
        silence_ratio=round(silence, 4),
        centroid_hz=round(centroid, 1), rolloff_hz=round(rolloff, 1),
        tempo_med=round(tempo_med, 1), tempo_drift=round(tempo_drift, 4),
        vocal_onset=onset,
        head_chars_0_2=head_chars_2, head_chars_0_8=head_chars_8,
        sung_segs=len(trusted), leak_segs=len(leak_segs),
        halluc_segs=len(cls["halluc"]),
        leaks=leak_segs[:6],
        leak_first=leak_segs[0] if leak_segs else None,
        f0_p2=round(float(np.percentile(f0v, 2)), 1) if f0v.size else 0.0,
        f0_med=round(float(np.median(f0v)), 1) if f0v.size else 0.0,
        f0_p98=round(float(np.percentile(f0v, 98)), 1) if f0v.size else 0.0,
        f0_drift_st=round(f0_drift_st, 2),
        asr_chars=len(tokens(txt, lang)), asr_segs=len(segs),
        coverage=round(cov, 4), asr_head=txt[:120],
        voice_self=round(float(e_self), 4),
        flatness=round(sraw.get("flatness", 0.0), 4),
        expect=dict(expect),
        _emb=e_all, _svec=svec,
    )
    return res


# ------------------------------------------------------------------ 判定

def _pct(seq, q):
    return float(np.percentile(seq, q)) if len(seq) else 0.0


def _med(seq):
    return float(np.median(seq)) if len(seq) else 0.0


def derive_baseline(old):
    """从老歌派生软阈值。老歌是"用户已认可"的，所以它们必须整体通过。"""
    return dict(
        n_old=len(old),
        coverage=round(_med([r["coverage"] for r in old]), 4),
        voice_self=round(_med([r.get("voice_self", 0.0) for r in old]), 4),
        # 声线地板：老歌留一最近邻的 P10。新歌至少要等于这个数。
        voice_floor=round(_pct([r.get("voice_nn_sim", 0.0) for r in old], 10), 4)
        if any(r.get("voice_nn_sim") for r in old) else 0.0,
        dynamic_db=round(_med([r["dynamic_db"] for r in old]), 2),
        centroid=round(_med([r["centroid_hz"] for r in old]), 1),
        f0_drift_st=round(_pct([r["f0_drift_st"] for r in old], 90), 2),
        tempo_drift=round(_pct([r["tempo_drift"] for r in old], 90), 4),
    )


def derive_soft(old, base):
    """放行线怎么来的（不要拍脑袋，也不要直接套老歌最小值）：

        歌词召回   >= 老歌中位 * 0.85   老歌本身是"唱出来了"的
        声线最近邻 >= 老歌 P10 - 0.02    不比已认可的最差那首更远
        声线自相似 >= 老歌 P10 - 0.02    前后半段不能换人
        动态/质心  >= 老歌 P10 - 余量     偏离即风格跑偏
        F0 漂移    <= 老歌 P90 + 1 半音   中途变调的上限由已认可作品决定
    """
    nn = [r.get("voice_nn_sim", 0.0) for r in old]
    has_nn = any(nn)
    return dict(
        coverage=round(base["coverage"] * 0.85, 4),
        voice_nn=round(_pct(nn, 10) - 0.02, 4) if has_nn else 0.0,
        voice_self=round(_pct([r.get("voice_self", 0.0) for r in old], 10) - 0.02,
                         4),
        dyn_lo=round(_pct([r["dynamic_db"] for r in old], 10) - 2.0, 2)
        if old else 6.0,
        centroid_lo=round(_pct([r["centroid_hz"] for r in old], 10) * 0.85, 1)
        if old else 1800.0,
        f0_drift_st_max=round(base["f0_drift_st"] + 1.0, 2),
        tempo_drift_max=round(base["tempo_drift"] + 0.05, 4),
    )


def judge(rows, baseline):
    old = [r for r in rows if r.get("group") == "old" and "error" not in r]
    new = [r for r in rows if r.get("group") != "old" and "error" not in r]

    base = derive_baseline(old)
    soft = derive_soft(old, base)
    if baseline:
        base.update(baseline.get("base", {}))
        soft.update(baseline.get("soft", {}))

    verdicts = []
    for r in new:
        if "error" in r:
            verdicts.append(dict(file=r["file"], verdict="ERROR",
                                 why=[r["error"]]))
            continue
        why = []
        e = r.get("expect", {})
        if e.get("duration"):
            d = abs(r["duration"] - e["duration"]) / float(e["duration"])
            if d > HARD["dur_tol"]:
                why.append("时长 %.1fs 与设定 %ds 偏差 %.1f%%（>5%%）"
                           % (r["duration"], e["duration"], d * 100))
        if r["sample_rate"] < HARD["sr_min"]:
            why.append("采样率 %d < %d" % (r["sample_rate"], HARD["sr_min"]))
        if r["peak"] > HARD["peak_max"] + 1e-4:
            why.append("峰值 %.4f 削波" % r["peak"])
        if not (HARD["lufs"][0] <= r["lufs"] <= HARD["lufs"][1]):
            why.append("响度 %.1f dB 超出 %.0f~%.0f"
                       % (r["lufs"], HARD["lufs"][0], HARD["lufs"][1]))
        if r["silence_ratio"] > HARD["silence_max"]:
            why.append("静音占比 %.1f%% 过高（>30%%）" % (r["silence_ratio"] * 100))
        if not (HARD["f0"][0] <= r["f0_p2"] and r["f0_p98"] <= HARD["f0"][1]):
            why.append("音高范围 %.0f~%.0f Hz 越界"
                       % (r["f0_p2"], r["f0_p98"]))

        # ---- 铁律一：禁止纯人声 / spoken 开头（用 ASR 段，不可用能量）
        if r["head_chars_0_2"] > 0:
            unit = "词" if _is_en(r.get("lang", e.get("lang"))) else "汉字"
            why.append("前 2s 就已唱出 %d 个%s，疑似纯人声/spoken 开头"
                       % (r["head_chars_0_2"], unit))
        # ---- 铁律二：8 小节器乐前奏（完整 8 小节，不是 55%）
        # 原来判的是 vocal_onset < want * 0.55，等于"第 4~5 小节进 sang
        # 也算过"，把铁律降级成建议。现在按完整 8 小节判，只留
        # onset_grace 吸收 ASR 时间戳本身的分辨率误差。
        if e.get("bpm") and e.get("intro_bars"):
            want = e["intro_bars"] * 60.0 / float(e["bpm"]) * 4
            gate = want * HARD["intro_onset_rel"] - HARD["onset_grace"]
            if r["vocal_onset"] is None:
                why.append("整首未识别出真唱（人声进入点 None）")
            elif r["vocal_onset"] < gate:
                why.append("人声在 %.1fs 就进，%d 小节前奏需 %.1fs"
                           % (r["vocal_onset"], e["intro_bars"], want))
        # ---- 铁律三：生成指令不能被唱出来
        # 这是 --lean-tags 之外的独立一条：instruction 是模型的歌词条件通道，
        # 一旦自然语言要求句被当歌词唱出来，歌里就有几句没人写的词，
        # QC 口径改不动，只能重生成。
        if r.get("leak_segs"):
            lf = r.get("leak_first") or {}
            # 泄漏有两类证据：实证短语命中（不依赖指令集合）和指令词集合重合。
            # 修复 ironlaw 之后集合里已经没有 fading / swelling 这类词了，
            # 所以短语命中才是主判据，报告里必须把命中的原话打出来。
            hit = lf.get("leak_phrase") or ""
            if not hit:
                hit = "指令词命中 %.0f%%" % (
                    (lf.get("instr_hit") or 0) * 100)
            why.append("前奏唱出生成指令 %d 处（最早 %.1fs，%s）：%s"
                       % (r["leak_segs"], lf.get("t0", 0), hit,
                          (lf.get("text") or "")[:40]))
        # ---- 禁止中途变速 / 变调 / 换声线
        if r["tempo_drift"] > soft["tempo_drift_max"]:
            why.append("速度中途漂移 %.1f%%（>%.1f%%），疑似变速"
                       % (r["tempo_drift"] * 100,
                          soft["tempo_drift_max"] * 100))
        if r["f0_drift_st"] > soft["f0_drift_st_max"]:
            why.append("音高中途漂移 %.1f 半音（>%.1f），疑似变调"
                       % (r["f0_drift_st"], soft["f0_drift_st_max"]))
        if r.get("voice_self", 0.0) < soft["voice_self"]:
            why.append("前后半段声线自相似 %.3f 低于 %.3f，疑似中途换声线"
                       % (r.get("voice_self", 0.0), soft["voice_self"]))
        # ---- 速度与设定 BPM 对齐（风格可听性的第一道物理保证）
        if e.get("bpm") and r["tempo_med"] > 0:
            rel = abs(r["tempo_med"] - e["bpm"]) / float(e["bpm"])
            if rel > HARD["bpm_rel_tol"]:
                why.append("实测速度 %.1f BPM 与设定 %d 偏差 %.0f%%，风格不成立"
                           % (r["tempo_med"], e["bpm"], rel * 100))
        # ---- 软阈值（老歌基线派生）
        if r.get("coverage", 0) < soft["coverage"]:
            why.append("歌词召回 %.0f%% 低于老歌基线派生线 %.0f%%"
                       % (r["coverage"] * 100, soft["coverage"] * 100))
        if soft["voice_nn"] > 0 and r.get("voice_nn_sim", 0.0) < soft["voice_nn"]:
            why.append("声线最近邻相似度 %.3f（最像《%s》）低于老歌派生线 %.3f"
                       % (r.get("voice_nn_sim", 0.0), r.get("voice_nn", "?"),
                          soft["voice_nn"]))
        if r["dynamic_db"] < soft["dyn_lo"]:
            why.append("动态范围 %.1f dB 低于老歌 P10 派生线 %.1f dB（偏平）"
                       % (r["dynamic_db"], soft["dyn_lo"]))
        if r["centroid_hz"] < soft["centroid_lo"]:
            why.append("频谱质心 %.0f Hz 低于老歌 P10 派生线 %.0f Hz（偏闷）"
                       % (r["centroid_hz"], soft["centroid_lo"]))

        # ---- 风格判定：与历史带标签语料比。
        # 只在语料本身可信（LOO 验证通过）时才当闸门，否则只写进报告当参考。
        want_fam = e.get("family") or qc_style.FAMILY.get(e.get("style", ""))
        got_fam = r.get("style_fam", "")
        if r.get("style_trusted") and want_fam and got_fam and got_fam != want_fam:
            why.append("风格判为 %s（最近像《%s》，置信 %.0f%%），与设定 %s 不符"
                       % (got_fam, r.get("style_like", "?"),
                          r.get("style_conf", 0) * 100, want_fam))

        verdicts.append(dict(
            file=r["file"], title=r["title"], style=r.get("style", ""),
            style_fam=got_fam, style_conf=r.get("style_conf"),
            style_trusted=r.get("style_trusted"),
            style_like=r.get("style_like"),
            voice_nn=r.get("voice_nn"), voice_nn_sim=r.get("voice_nn_sim"),
            vocal_onset=r.get("vocal_onset"),
            leak_segs=r.get("leak_segs", 0), leaks=r.get("leaks", [])[:3],
            verdict="PASS" if not why else "FAIL", why=why))

    return dict(baseline=base, soft=soft, verdicts=verdicts)


# ------------------------------------------------------------------ main

def run_job(job):
    root = job["comfy_root"]
    music = job["music_dir"]
    ref = job.get("ref")
    cache = job.get("cache_dir") or os.path.join(root, "songforge", "cache", "qc")
    os.makedirs(cache, exist_ok=True)

    style_ref = qc_style.load(job.get("style_ref"))
    e_ref = None
    if ref and os.path.isfile(ref):
        ry, rsr, _ = _load_audio(ref)
        e_ref = embed(ry, rsr)

    rows = []
    for item in job["files"]:
        p = item["path"] if os.path.isabs(item["path"]) \
            else os.path.join(music, item["path"])
        try:
            r = measure(p, item, cache_dir=cache,
                        with_asr=job.get("asr", True))
            if e_ref is not None:
                r["voice_ref"] = round(cos(r["_emb"], e_ref), 4)

            # ---- 风格分类：与历史带标签语料的质心比（只看混音轨）
            if style_ref is not None and "_svec" in r:
                fam, conf, dists = style_ref.classify(r["_svec"])
                near, ndist, nfam = style_ref.nn(r["_svec"])
                r["style_fam"] = fam
                r["style_conf"] = round(conf, 4)
                r["style_trusted"] = style_ref.trusted
                r["style_like"] = near
                r["style_like_family"] = nfam
                r["style_like_dist"] = round(ndist, 4)
                r["style_dists"] = {k: round(v, 4) for k, v in dists.items()}

            r.pop("_emb", None)
            r.pop("_svec", None)
            rows.append(r)
        except Exception as exc:
            rows.append(dict(file=os.path.basename(p), group=item.get(
                "group", "new"), error="%s: %s" % (type(exc).__name__, exc),
                traceback=traceback.format_exc()[-600:]))
        print("  .. %s" % os.path.basename(p), flush=True)

    # ---- 声线最近邻必须在全部量完之后做：
    # 比的不是某一个参考音频，而是 8 首已认可老歌整体。
    # 老歌自己也要算，但必须留一（跟自己比永远是 1.0，没有参考价值）。
    # 坑位（踩过）：最初只给新歌算 NN，老歌行没有 voice_nn_sim，
    # 而 derive_soft 又是从老歌行派生闸门 -> 阈值算成 0，
    # `if soft["voice_nn"] > 0` 永远不成立，声线这一项等于形同虚设。
    old_nn = []
    for r in rows:
        if "error" in r:
            continue
        nn = _voice_nn(r, job["files"], music, cache)
        if nn:
            r.update(nn)
        if r.get("group") == "old" and r.get("voice_nn_sim"):
            old_nn.append(r["voice_nn_sim"])
    floor = float(np.percentile(old_nn, 10)) if old_nn else None
    if floor is not None:
        for r in rows:
            r["voice_nn_floor"] = round(floor, 4)
    return rows


def _voice_emb_of(path, cache):
    """带磁盘缓存的声线 embedding。整轮 QC 有 11 首，重复算纯浪费。"""
    key = os.path.abspath(path)
    if key in _EMB_CACHE:
        return _EMB_CACHE[key]
    cp = os.path.join(cache, os.path.basename(path) + ".emb.npy")
    if os.path.isfile(cp):
        e = np.load(cp).astype(np.float64)
    else:
        v, vsr = vocals_of(path, cache)
        t = embed(v, vsr)
        e = np.asarray(t.detach().cpu().numpy()
                       if hasattr(t, "detach") else t, dtype=np.float64)
        np.save(cp, e)
    _EMB_CACHE[key] = e
    return e


def _voice_nn(row, files, music, cache):
    """这首歌 vs 8 首已认可老歌的声线最近邻。

    坑位：老歌自己比老歌余弦接近 1，没有参考价值，所以要做留一——
    计算某首老歌时把它自己从候选里剔掉。这样 8 首老歌互相之间
    的相似度才是"已认可作品之间的自然差异"，新歌的闸门才有意义。
    """
    self_path = os.path.abspath(row["path"])
    e_new = _voice_emb_of(row["path"], cache)
    best = ("", -1.0)
    for it in files:
        if it.get("group") != "old":
            continue
        p = it["path"] if os.path.isabs(it["path"]) \
            else os.path.join(music, it["path"])
        if os.path.abspath(p) == self_path:
            continue                      # 留一：自己不能给自己打分
        try:
            eo = _voice_emb_of(p, cache)
        except Exception:
            continue
        na, nb = float(np.linalg.norm(e_new)), float(np.linalg.norm(eo))
        if na < 1e-8 or nb < 1e-8:
            continue
        s = float(np.dot(e_new, eo) / (na * nb))
        if s > best[1]:
            best = (it.get("title") or os.path.basename(p), s)
    if not best[0]:
        return None
    return dict(voice_nn=best[0], voice_nn_sim=round(best[1], 4))


def main(argv):
    spec = json.loads(argv[1])
    rows = run_job(spec)
    base_cache = spec.get("baseline_file")
    baseline = None
    if base_cache and os.path.isfile(base_cache):
        with open(base_cache, encoding="utf-8") as f:
            baseline = json.load(f)
    rep = judge(rows, baseline)
    rep["rows"] = rows
    print("###JSON###" + json.dumps(rep, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
