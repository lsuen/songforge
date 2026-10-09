# -*- coding: utf-8 -*-
"""风格特征与风格分类 —— 只吃 librosa，纯数值，不碰 demucs/whisper。

为什么单独一个文件：
    songforge 主链路是零重依赖的，风格判定属于"生成后验收"的一部分，
    必须在 venv 侧跑。把它独立出来，`qc_worker.py` 和
    `tools/make_style_ref.py` 共用同一套取特征逻辑——否则"建语料"和
    "验收"各写一份，特征对不上，分类结果就是自欺欺人。

风格怎么判（重要，不要改成拍脑袋的阈值）：
    这里不做"频谱质心 > X 就是朋克"这种单指标硬判。做法是拿历史里
    已经按风格命名、并且用户听过没否掉的歌当语料，逐首算同一套特征，
    按稳健标准化后建三个风格质心（pop / punk / hiphop），用最近质心
    分类，并做留一验证（LOO）把准确率一起存下来。
    准确率不够高时，报告里如实写"风格判定不可信"，只当参考不当闸门。

坑位记录：
    1. 速度必须做八度折叠。实测 `晚风不加班`（设定 86 BPM）会被判成
       170.5 BPM，正好是 2 倍——librosa 的 onset 包络在半速拍子上会
       锁到二倍拍。直接比设定值会把这个风格判死。
    2. 频谱类特征跨度极大（质心 1400~6000 Hz），必须取 log 再标准化，
       否则质心一项就主导了整段欧氏距离。
    3. perc_ratio 用 HPSS 的打击成分占比。朋克的军鼓+失真吉他、
       hiphop 的 kick+hi-hat 都会偏高，流行的人声+弦乐偏低。
       单看这个不如 BPM 有区分度，但作为辅助项能压掉误判。
    4. 特征里绝对不能有"人声轨 / 混音轨 能量比"。带标签的历史语料有 30~50 首，
       每一首都跑 demucs 要花几十分钟，而且 demucs 对不同混音的分离程度
       本身就不一致，这个比值混进去是纯噪声。风格只看混音轨。
"""
import os
import re

import numpy as np

FAMILY = {
    "pop": "pop",
    "punk": "punk",
    "emopunk": "punk",
    "urbanhiphop": "hiphop",
    "chillhiphop": "hiphop",
    "partyrap": "hiphop",
    "funkhiphop": "hiphop",
    "discofunk": "hiphop",
    "jazzhiphop": "hiphop",
    "guofeng": "hiphop",
}

FAMILIES = ("pop", "punk", "hiphop")

# 特征顺序即向量顺序，改这里必须重建 style_ref.json，否则老语料错位。
FEATURES = (
    "tempo",          # 八度折叠后的实测速度
    "tempo_drift",    # 速度稳定度（越小越稳）
    "centroid",       # log 频谱质心 -> 亮/暗
    "rolloff",        # log 85% rolloff -> 高频能量
    "hf_ratio",       # >6kHz 能量占比 -> 亮泽/嘶叫
    "lf_ratio",       # <150Hz 能量占比 -> 低频厚度/sub
    "dynamic",        # 动态范围 dB
    "onset_density",  # 每秒击打数
    "perc_ratio",     # HPSS 打击成分占比
    "flatness",       # 谱平坦度 -> 噪声/失真成分（朋克嘶叫会抬高）
    "crest",          # 峰值因子，越小越被压得平
)

_LOG_IDX = (FEATURES.index("centroid"), FEATURES.index("rolloff"))


# ------------------------------------------------------------------ 工具

def fold_bpm(value, ref):
    """把 value 折叠到离 ref 最近的八度。

    坑位：librosa.beat 在慢速律动上会锁到 2 倍拍，不折叠会把 86 BPM 的歌
    判成 170 BPM，然后"实测速度与设定偏差 98%"，一个完全正常的歌被判死。
    """
    if value is None or ref is None:
        return value
    try:
        v = float(value)
        r = float(ref)
    except (TypeError, ValueError):
        return value
    # 坑位：非有限值和非正数会一路穿到 log2 里，log2(inf) 再喂给 round()
    # 直接抛 OverflowError，把整首 QC 打断。下游只想要"折不动就原样返回"。
    if not (np.isfinite(v) and np.isfinite(r)) or v <= 0 or r <= 0:
        return v
    ratio = r / v
    if not np.isfinite(ratio) or ratio <= 0:
        return v
    return v * (2.0 ** round(np.log2(ratio)))


def fold_track(track, ref):
    return np.array([fold_bpm(x, ref) for x in track], dtype=np.float32)


def _band_ratio(y, sr, lo, hi):
    """指定频带能量占比。用 rfft 直接算，比 mel 滤波器组省事也够稳。"""
    if y.size < 256:
        return 0.0
    n = 1 << int(np.ceil(np.log2(y.size)))
    sp = np.abs(np.fft.rfft(y, n)) ** 2
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    band = sp[(freqs >= lo) & (freqs < hi)].sum()
    tot = sp.sum()
    return float(band / tot) if tot > 0 else 0.0


# ------------------------------------------------------------------ 取特征

def tempo_of(y, sr, expect_bpm=None):
    """返回 (折叠后中位速度, 折叠后速度漂移)。expect_bpm 用来定折叠方向。"""
    import librosa
    y16 = y if sr == 16000 else librosa.resample(
        np.asarray(y, dtype=np.float32), orig_sr=sr, target_sr=16000)
    sr16 = 16000 if sr != 16000 else sr
    oe = librosa.onset.onset_strength(y=y16, sr=sr16)
    # librosa 0.11 起 beat.tempo 全是 keyword-only，位置传参直接 TypeError
    tr = librosa.beat.tempo(onset_envelope=oe, sr=sr16, hop_length=512,
                            aggregate=None)
    tr = np.asarray(tr, dtype=np.float32)
    tr = tr[np.isfinite(tr) & (tr > 0)]
    if not tr.size:
        return 0.0, 0.0
    raw_med = float(np.median(tr))
    anchor = fold_bpm(raw_med, expect_bpm) if expect_bpm else raw_med
    ft = fold_track(tr, anchor)
    ft = ft[(ft > 0) & (ft < 400)]
    if not ft.size:
        return 0.0, 0.0
    med = float(np.median(ft))
    # 坑位：原来用 (p90 - p10) / median 算漂移，但 librosa 逐帧 tempo 的首尾
    # 各有一拍宽的边界伪影，180s 的歌能攒出十几秒伪影区，被 p90/p10 一夹
    # 就放大成 25%+ 的"漂移"，而同一首歌按 10s 分块的中位全是设定值
    # （实测 104.2/104.2，IQR=0）。改用中位绝对偏差，只对整体性的加速/
    # 减速敏感，对边界伪影和偶发跳拍免疫。
    drift = _mad_rel(ft, med)
    return med, drift


def _mad_rel(track, med):
    """相对中位绝对偏差。返回 0.0 表示测不出整体漂移。"""
    if track.size == 0 or med <= 0:
        return 0.0
    mad = float(np.median(np.abs(track - med)))
    return (mad / med) if med > 0 else 0.0


def features(path, expect_bpm=None, cache_dir=None):
    """算一首歌的完整风格特征向量。

    坑位：这个函数只允许吃混音轨（path）。不要往里塞 demucs 人声轨——
    建历史语料时要对几十首老歌跑，塞进去就等于把风格判定绑死在分离质量上，
    而 demucs 的分离程度本身随混音变化。声线由 qc_worker 单独负责。
    """
    import librosa
    import soundfile as sf

    x, sr = sf.read(path, always_2d=True)
    x = np.asarray(x, dtype=np.float32)
    y = x.mean(axis=1)

    dur = y.size / float(sr)
    tempo, tempo_drift = tempo_of(y, sr, expect_bpm)

    y16 = librosa.resample(y, orig_sr=sr, target_sr=16000)
    sr16 = 16000
    S = np.abs(librosa.stft(y16, n_fft=2048, hop_length=512)) + 1e-12
    centroid = float(np.mean(librosa.feature.spectral_centroid(S=S, sr=sr16)))
    rolloff = float(np.mean(librosa.feature.spectral_rolloff(
        S=S, sr=sr16, roll_percent=0.85)))
    # 谱平坦度：几何均值/算术均值。噪声化（失真、嘶叫、lo-fi）会把它抬高，
    # 干净的钢琴/弦乐 pad 会压得很低。比 "高频能量占比" 更能区分失真音色。
    gm = np.exp(np.mean(np.log(S), axis=0))
    am = np.mean(S, axis=0)
    flatness = float(np.mean(gm / am))

    bs = 3 * sr
    n = max(1, y.size // bs)
    rmss = np.array([np.sqrt(np.mean(np.square(y[i * bs:(i + 1) * bs])) + 1e-20)
                     for i in range(n)], dtype=np.float32)
    dyn = float(20 * np.log10(max(rmss.max(), 1e-9) / max(rmss.min(), 1e-9)))

    oe = librosa.onset.onset_strength(y=y16, sr=sr16)
    hop = 512
    onset_density = float(len(librosa.onset.onset_detect(
        onset_envelope=oe, sr=sr16, hop_length=hop)) / max(dur, 1e-6))

    h, p = librosa.effects.hpss(y16)
    perc_ratio = float(np.mean(np.abs(p)) /
                       max(np.mean(np.abs(h)) + np.mean(np.abs(p)), 1e-9))

    peak = float(np.max(np.abs(y))) if y.size else 0.0
    rms_all = float(np.sqrt(np.mean(np.square(y)) + 1e-20))
    crest = float(20 * np.log10(max(peak, 1e-9) / max(rms_all, 1e-9)))

    vec = {
        "tempo": tempo,
        "tempo_drift": tempo_drift,
        "centroid": float(np.log10(max(centroid, 1.0))),
        "rolloff": float(np.log10(max(rolloff, 1.0))),
        "hf_ratio": _band_ratio(y16, sr16, 6000, sr16 / 2),
        "lf_ratio": _band_ratio(y16, sr16, 20, 150),
        "dynamic": dyn,
        "onset_density": onset_density,
        "perc_ratio": perc_ratio,
        "flatness": flatness,
        "crest": crest,
    }
    return vec, dict(centroid_hz=centroid, rolloff_hz=rolloff,
                     flatness=flatness,
                     onset_density=onset_density, perc_ratio=perc_ratio,
                     crest=crest, peak=peak)


def vector(vec):
    return np.array([float(vec[k]) for k in FEATURES], dtype=np.float64)


# ------------------------------------------------------------------ 分类器

def _robust_scale(X):
    """中位数 + IQR 标准化。IQR 比 std 抗离群值。"""
    med = np.median(X, axis=0)
    q1 = np.percentile(X, 25, axis=0)
    q3 = np.percentile(X, 75, axis=0)
    iqr = np.maximum(q3 - q1, 1e-6)
    return med, iqr


class StyleRef(object):
    """风格质心分类器。语料不足或 LOO 太低时 self.trusted=False。"""

    def __init__(self, data):
        self.note = data.get("note", "")
        self.loo = data.get("loo", {})
        self.items = data.get("items", [])
        self.med = np.array(data["med"], dtype=np.float64)
        self.iqr = np.array(data["iqr"], dtype=np.float64)
        self.centroids = {k: np.array(v, dtype=np.float64)
                          for k, v in data["centroids"].items()}
        self.counts = data.get("counts", {})
        self.trusted = bool(data.get("trusted", False))

    def _z(self, vec):
        return (vector(vec) - self.med) / self.iqr

    def classify(self, vec):
        """返回 (top_family, conf, dists)。conf 是 softmax 后的置信度。"""
        z = self._z(vec)
        dists = {}
        for fam, c in self.centroids.items():
            dists[fam] = float(np.sqrt(np.mean((z - c) ** 2)))
        order = sorted(dists, key=lambda k: dists[k])
        if not order:
            return "", 0.0, dists
        top = order[0]
        e = {k: np.exp(-dists[k]) for k in order}
        s = sum(e.values())
        return top, float(e[top] / s if s > 0 else 0.0), dists

    def nn(self, vec):
        """最近邻：返回 (最像的文件名, 与它的标准化距离, 该文件所属风格)。

        这是声线之外第二个'像不像已认可作品'的信号，且自带人耳可解释性：
        报告里能直接说出"最像 身体先听懂（urbanhiphop）"。
        """
        z = self._z(vec)
        best = ("", 1e9, "")
        for it in self.items:
            d = float(np.sqrt(np.mean((z - np.array(it["z"], dtype=np.float64)) ** 2)))
            if d < best[1]:
                best = (it["file"], d, it.get("family", ""))
        return best


def load(path):
    import json
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return StyleRef(json.load(f))


def _loo_groups(items):
    """按"歌"分组，而不是按文件。

    坑位（踩过）：最初按文件留一，得到 100% 准确率，一度以为分类器完美。
    实际上同一首歌的 _00001/_00002/_00003 是同一首曲子的不同 seed，
    音频高度同源。留一时只拿掉一个 seed，剩下 5 个"同款"还留在质心里，
    等于让样本给自己作证，准确率必然虚高。
    真正的留一必须整首拿掉——验证的是"没听过这首歌时能不能认出风格"。
    """
    g = {}
    for i, it in enumerate(items):
        key = it.get("group") or _group_key(it.get("file", ""))
        g.setdefault(key, []).append(i)
    return g


def _group_key(file):
    """从文件名还原"这是哪一首歌"。dy10_城市晚安_swl_00001 -> dy10_城市晚安。

    只用于分组，不是标签。

    坑位：这里曾经把第一个下划线之前的前缀当"批次号"砍掉，只留后面的歌名。
    结果 chillhiphop_慢慢来 和 hit10_慢慢来 被并成同一组，58 个文件只出
    57 个组。留一验证时同一首歌的另一版本还留在质心里，样本等于给自己作证。
    所以只剥 seed 号和模型后缀，风格前缀必须留着——它本来就是区分同名歌的
    唯一信息。命名不规范时退回整条文件名也不会错。
    """
    stem = file.rsplit(".flac", 1)[0]
    stem = re.sub(r"_\d{5}$", "", stem)          # 去掉 seed 号
    stem = re.sub(r"_swl$", "", stem, flags=re.I)  # 去掉模型后缀
    return stem


def build(items, loo_min=0.65, fam_min=0.50):
    """由带标签的语料建质心，并做按歌分组的留一验证。

    items: [{"file","family","bpm","vec"}]
    返回可直接 json.dump 的字典。

    关于阈值为什么这么严（实测数据，别拍脑袋改）：
        语料是 58 首歌（pop 35 / hiphop 16 / punk 7）。按歌分组留一后，
        最好的组合也只到 ~65%：pop 有 4 成被判成 hiphop，hiphop 也有近
        1 成被判成 pop。逐个特征看下来没有救——tempo 单项 49%、centroid
        单项 39%，加特征、加权、改标准化都只在 60~67% 之间晃。
        根因是语料本身重叠：pop 语料里的 chill/disco/funk 变体和 hiphop
        语料在制作手法上就是连续的，不是分类器没调好。

        所以这里刻意把门槛设高（loo_min=0.65 且每个家族都要 >=0.50），
        让 trusted 大概率为 False —— 风格判定只写进报告当参考，不当放行闸门。
        宁可报告里写"风格不可信"，也不要一个 60% 准确率的分类器
        拿去做"风格不符就判 FAIL"的闸门，那是在用假精度杀人。
        风格的第一物理保证是实测 BPM 与设定 BPM 对齐（见 HARD.bpm_rel_tol）。
    """
    import json
    X = np.array([vector(it["vec"]) for it in items], dtype=np.float64)
    med, iqr = _robust_scale(X)
    Z = (X - med) / iqr

    cent = {}
    counts = {}
    for fam in FAMILIES:
        idx = [i for i, it in enumerate(items) if it["family"] == fam]
        if not idx:
            continue
        counts[fam] = len(idx)
        cent[fam] = np.mean(Z[idx], axis=0)

    # ---- 按歌分组留一：整首拿掉，重算标准化和质心，再看它能不能被认对
    groups = _loo_groups(items)
    correct = 0
    total = 0
    per = {}
    confusion = {}
    for key, idxs in groups.items():
        fam = items[idxs[0]]["family"]
        keep = [i for i in range(len(items)) if i not in set(idxs)]
        Xk = np.array([vector(items[i]["vec"]) for i in keep],
                      dtype=np.float64)
        mk, ik = _robust_scale(Xk)
        Zk = (Xk - mk) / ik
        ck = {}
        for f in FAMILIES:
            j = [n for n, i in enumerate(keep) if items[i]["family"] == f]
            if j:
                ck[f] = np.mean(Zk[j], axis=0)
        if len(ck) < 2:
            continue
        z = np.mean([(vector(items[i]["vec"]) - mk) / ik for i in idxs], axis=0)
        d = {f: float(np.sqrt(np.mean((z - c) ** 2))) for f, c in ck.items()}
        top = min(d, key=lambda k: d[k])
        total += 1
        good = top == fam
        correct += 1 if good else 0
        a = per.setdefault(fam, [0, 0])
        a[1] += 1
        a[0] += 1 if good else 0
        cell = confusion.setdefault(fam, {})
        cell[top] = cell.get(top, 0) + 1

    acc = float(correct / total) if total else 0.0
    fam_acc = {k: (v[0] / float(v[1]) if v[1] else 0.0) for k, v in per.items()}
    # 总体准确率高也可能被某一个大类撑起来，所以每个家族都要单独过线。
    # 三个家族里只要有一个基本认不出来（比如 punk 全被判成 pop），
    # 这个分类器就不能拿去做风格闸门。
    fam_ok = bool(fam_acc) and min(fam_acc.values()) >= fam_min
    ref = dict(
        features=list(FEATURES),
        med=[float(x) for x in med],
        iqr=[float(x) for x in iqr],
        centroids={k: [float(x) for x in v] for k, v in cent.items()},
        counts=counts,
        items=[dict(file=it["file"], family=it["family"],
                    group=it.get("group") or _group_key(it["file"]),
                    bpm=it.get("bpm"), vec=it["vec"],
                    z=[float(x) for x in Z[i]])
               for i, it in enumerate(items)],
        loo=dict(acc=round(acc, 4), n=total, n_songs=len(groups),
                 mode="group-by-song",
                 per_family={k: dict(acc=round(v[0] / max(v[1], 1), 3), n=v[1])
                             for k, v in per.items()},
                 confusion=confusion,
                 fam_min=round(min(fam_acc.values()) if fam_acc else 0.0, 3)),
        trusted=bool(total >= 9 and acc >= loo_min and fam_ok),
        note=("由 tools/make_style_ref.py 在历史已认可歌上生成，不要手改。"
              "trusted 只有在按歌分组 LOO >= %.0f%% 且每个家族都 >= %.0f%% 时为真；"
              "实测语料 pop/hiphop 制作手法重叠，多轮尝试后总体约 60~67%%、"
              "单家族最低约 43%%，"
              "所以本文件通常 trusted=false —— 风格判定只当参考，"
              "风格的第一物理保证是实测 BPM 与设定 BPM 对齐。"
              % (loo_min * 100, fam_min * 100)),
    )
    return ref