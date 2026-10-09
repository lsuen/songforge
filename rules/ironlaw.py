# -*- coding: utf-8 -*-
"""生歌铁律。全部自动注入，调用方无法遗漏。

从 song_rules.py 迁入并扩展。旧模块保留不动（fallback 用），但新流程一律走这里。
用户永久约束：
    "以后所有歌曲都不要开始就是纯人声的设定"
    "也不要没有拍子纯人声"
"""

import os
import sys
import re

# 确保正确添加工程根目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from songforge.core.errors import LexiconError, RuleViolation

TRIGGER = "swlvoice"

RULE_INTRO = (
    "8 bar instrumental intro hook before any vocals, "
    "instrumental opening, no singing in the first 8 bars, "
    "vocals enter only after the instrumental hook has played, "
    "the intro has a catchy instrumental melody hook, "
    "the intro bass line and synth melody are the most memorable catchy element, "
    "rhythmic drums from the very first beat, "
    "intro is instrumental only, no voice, no lyrics, no humming, "
    "no vocal ad-lib in the opening, no wu wu wu, no woo woo, "
    "no mm mm, no en en, no ng ng, no mouth sounds, no grunting, "
    "the opening features a strong instrumental melody line, "
    "synth lead melody plays from the very beginning, "
    "instrumental melody carries the intro hook"
)

# 注意：绝对不要加 "no long empty instrumental"。它与"必须有 8 小节器乐前奏"
# 直接矛盾，模型为消除"空"而把前奏填满人声。这个坑踩过，别加回来。
RULE_NEG = (
    "no opening vocals, no pure vocal intro, no a cappella opening, "
    "no vocal intro, no singing until after the instrumental hook, "
    "no spoken intro, no rap intro talk, no intro talking, "
    "no humming, no mm mm syllables, no oh oh syllables, "
    "no wu wu wu, no woo woo, no ooh ooh, no sustained wu vowel, "
    "no en en en, no ng ng ng, no mouth sounds, no grunting, no breathing sounds, "
    "no eh ah ah filler, no vocal ad-libs, no droning sustained vowels, "
    "no yodeling, no melisma, no crooning, no R&B ballad vocal style, "
    "no overly long intro, no excessive drum solo, "
    "no slow start, no fade in, "
    "no beatless intro, no unaccompanied vocal, no a cappella, "
    "no singing chinese instruction words, no vocalizing text labels, "
    "no wordless vocalization, no non-verbal vocal sounds, "
    "no vocal pad, no vocal texture, no ambient vocal, "
    "no background vocal in the opening, no choir in the intro, "
    "no vocal harmonies in the intro, no vocal layer in the opening"
)

RULE_CONSIST = (
    "consistent vocal timbre in every section, no vocal style change, "
    "identical vocal character in verse, chorus and rap, "
    "no style drift, no breakdown, no key change, no tempo change mid song"
)

# 整首歌的衬音/即兴人声禁令（不限于前奏）。
#
# RULE_NEG 已经覆盖了前奏的"no opening vocals / no humming"，但它绑在
# "opening" 语境上，模型在 verse 之间的间奏段仍会插入 ooh / ah / 嗯 / 啊。
# 这条 RULE_NO_FILLER 单独存在，且中英文双管齐下：
#   - 英文：覆盖 ACE-Step 最常见的 filler 词
#   - 中文：覆盖模型在中文歌词间插入的衬词
#   - 段落间：明确要求段落之间只允许器乐，不允许人声填充
RULE_NO_FILLER = (
    "vocals sing only the exact words written in the lyrics, "
    "no other vocal sounds anywhere in the song, "
    "all sections without lyrics are instrumental only"
)

RULE_HARMONY = (
    "no dissonance, no sour notes, no off key singing, "
    "no minor second clash, no unbalanced harmony"
)

RULE_MEMO = (
    "memorable repeating vocal hook, short four syllable hook, "
    "strong call and response, dynamic contrast between verse and hook, "
    "clear space between rap phrases, crisp clear pronunciation, "
    "no straining, no autotune warble, radio ready loudness, "
    "choreography friendly, dance floor ready"
)

RULE_ENERGY = (
    "clear dynamic contrast, full energy intro at the same level as the drop, "
    "medium verse, loud hook, "
    "build up into every hook, no flat lifeless mix, no monotone energy, "
    "no quiet intro, no beatless intro, no sparse opening"
)

DEFAULT_INSTRUMENT = ("four on the floor kick, crisp snare, tight hi-hats, "
                      "walking bass, chord stabs")

# 前奏括号专用的回落值。
#
# 刻意不直接复用 DEFAULT_INSTRUMENT：它含 "chord stabs"，而 "chord stab"
# 就在 _RHYTHM_DROP 里。旧代码回落时会把刚剔除掉的空场信号重新塞回前奏，
# 出现"过滤生效但一回落就前功尽弃"的 bug。
DEFAULT_INTRO_RHYTHM = ("punchy four on the floor kick, deep sub bass, "
                        "crisp snare, tight closed hi-hats")

# 前奏括号里必须出现的两条正向要求（缺一条就补一条）。
#
# 为什么单独兜底：intro_rhythm_only() 只做"减法"（从风格配器里挑出节奏组），
# 但风格字典本身可能一条贝斯都没写。实测 vocalchill 就是这样：
#     huge supersaw lead, sidechain compressed pad, punchy electro kick,
#     crisp clap, trap snare, white noise riser, big reverb, melodic drop
# 过滤后只剩 "punchy electro kick, crisp clap, trap snare"——三条全是
# 中高频，**没有一条能落在 30~120 Hz**。结果两版新音频（GroovePure2 /
# GrooveChill2）开头 sub 比全曲峰值低 53.5 / 31.2 dB，始终过不了验收。
#
# 所以改成"减法 + 兜底加法"：减法负责去掉 pad/riser/reverb/lead，
# 加法保证 kick 和 bass 两条一定写进前奏括号，不依赖风格字典写没写。
_INTRO_MUST = (
    ("kick", "punchy four on the floor kick, every beat"),
    ("bass", "deep sub bass, every beat"),
)

# 前奏括号里额外要有的"音量电平"要求（减法 + 加法之外的第三层）。
#
# ⚠ 2026-10-05 重大修正：这条曾经以散文句的形式追加进**歌词括号**，结果被
# 模型原样唱了出来。实测 Bounce_s8 的 vocals stem ASR：
#     t0=0.00  "The opening grooves in base front, ace front and center
#               at full level."
#     t0=27.00 "No quiet or sparse openin' No fading in, no swelling in"
# 逐词对得上下面这句 _INTRO_LEVEL（旧版），而它根本不在作者手写的
# Bounce.txt 里。
#
# 根因：[Instrumental: ...] 括号是**歌词条件通道**，不是 prompt 通道。
# 里面每一个英文词都可能被当成可唱的歌词。之前把"要求句"写进括号，
# 等于亲手把一段英文散文歌词塞给模型。
#
# 现在这些要求只留在 tags 通道（RULE_GROOVE_FIRST_BEAT），那里不会被唱。
# 括号里只允许"配器名词短语"。原始的低频实测记录保留在下面，
# 因为"和 drop 同电平"这个物理要求本身没变，只是换了通道。
#
# 前两轮把 kick / bass 写进括号后，intro_sub 变成双峰分布——
#     要么 -35 dB（有鼓），要么 -80 dB（数字静音），中间没有过渡。
#     27 个 seed 里 22 个落在 -80 dB 那侧。
# _INTRO_MUST 只回答"有哪些件"，不回答"多大声"，所以还需要电平要求。
# 但电平要求属于 tags 的活，不属于歌词括号。
_INTRO_LEVEL = ""   # 保留名字只为向后兼容；不再写进歌词括号

# 前奏括号里要出现的"每一拍都在打"量词要求。
#
# 为什么要单独一层：_INTRO_MUST 只保证 kick / bass 这两个**词**出现过。
# 风格字典写 "heavy four on the floor kick on beat one" 时词是齐的，
# 兜底不触发，但"on beat one"是弱表述——它只承诺第一拍，
# 其余三拍模型可以合法地填成空拍或 pad。
# Bounce 的 15 个 seed 全部栽在这：前 4 小节 sub 掉到 -83 dB 数字静音，
# 鼓到第 4.12 小节才进。给了它"空一拍"的合法借口，它就会真的空一拍。
#
# 判据必须是量词短语（every beat / every single beat）。
# 不能用 "on beat one"、"beat one" 之类——那正是要修的弱表述本身。
#
# ⚠ 措辞必须保持"配器名词短语"，不能写成人话句子。2026-10-05 实测：
# 旧版写成 "the kick drum and the bass hit on every single beat of every bar
# with no gaps, no rests and no missing beats"，被模型当成歌词唱了出来。
# 括号是歌词通道，句号和从句都会变成可唱的词。现在只保留节奏组名词，
# "每一拍都在打"这个语义交给 tags 侧的 RULE_GROOVE_FIRST_BEAT 表达。
#
# 必须是**单个逗号分项**且自身含保留词，否则第二次过滤会把它删掉，
# intro_rhythm_only() 就不幂等了。实测踩过：
#     "per beat kick and sub bass, four on the floor"
# 里的 "four on the floor" 不含任何 _RHYTHM_KEEP 词（kick/bass/beat…），
# 下一轮整项被剔，追加的量词句凭空消失。
_INTRO_EVERY_BEAT = "every beat kick and sub bass four on the floor"

# "每一拍都在打"的量词判据。必须是词边界正则而不是子串查找：
# "every sixteenth"（拍一下的配器描述）不是"每一拍"，但子串 "every beat"
# 匹配不上它、而 "every" + "beat" 拆开判断又太啰嗦，所以直接用一条正则
# 同时覆盖 every beat / every single beat / every sixteenth note 之外的所有
# "每拍"表述，并显式排除 "on beat one" 这类只承诺第一拍的弱表述。
_EVERY_BEAT_RE = re.compile(
    r"\b(?:every|each|per)\s+(?:single\s+)?(?:one\s+)?beat\b"
    r"|\ball\s+(?:four\s+)?beats\b"
    r"|\b(?:every|each|per)\s+(?:single\s+)?(?:1|one|quarter)\b",
    re.I,
)

# 前奏括号里允许原样保留的结构性前缀白名单。
#
# 只允许 "8 bar instrumental intro hook" / "8 bar groove" 这类纯描述长度和
# 段落的词。一旦前缀里混进配器词（pad / riser / bass / kick...），就必须
# 连它一起送进 intro_rhythm_only() 过滤，否则空场信号会绕过整条过滤链。
_INTRO_PREFIX_OK = re.compile(
    r"^\d+\s*bar(?:\s+(?:instrumental|intro|introduction|groove|break|hook|"
    r"opening|build|section|beat|main|rhythmic))*\.?\s*$",
    re.I,
)


# === 前奏括号专用的配器过滤（本轮真正起作用的改动）===
#
# 为什么要过滤：build_inst_lyrics() / build_chill_lyrics() 会把风格字典的
# 整条 instruments 塞进前奏的 [Instrumental: ...] 括号里。实测（vocalchill）
# 这一串是：
#     huge supersaw lead, sidechain compressed pad, punchy electro kick,
#     crisp clap, trap snare, white noise riser, big reverb, melodic drop
# 其中"把前奏铺成空场"的词有 4 个：supersaw lead / sidechain pad /
# white noise riser / big reverb；而"要求底鼓贝斯从第 0 拍进"的只有
# kick / clap / snare 三项。3 : 4 的正面竞争输了，模型就把前奏渲染成
# ambient pad + riser。
#
# 实测证据（ab_bands.py，head = 前 16 秒，mid = 16~32 秒，dBFS）：
#     GroovePure  head sub -66.0 / mid sub -55.8   缺底鼓贝斯
#     GrooveChill head sub -53.5 / mid sub -14.7   反而不缺
# 说明"前奏没鼓"不是标签里少写了鼓，而是前奏括号里 pad/riser 词太多。
#
# 所以前奏括号只保留节奏组（底鼓/贝斯/鼓组），其余全部剔除。
# 这些词只在**前奏括号**里剔除，标签侧的 instruments 段保持原样——
# drop 和 build 段本来就该有 pad、lead、reverb。
_RHYTHM_KEEP = (
    "kick", "bass", "snare", "clap", "hat", "cymbal", "tom", "percussion",
    "808", "sub", "drum", "beat", "claves", "shaker", "tambourine",
    "rim", "crash", "ride", "piano", "rhodes", "keys", "organ",
    "guitar", "bassline", "bass line", "synth bass",
)

# 明确剔除：这些词留在前奏括号里会把前奏推向 ambient / 空场。
#
# 匹配方式：子串匹配会误删正词（实测 2026-10-05）：
#     "layered crisp snare"     含 "layer"  -> 整项被删
#     "clavinet short stabs"    含 "stab"   -> 整项被删
#     "clavinet chicken scratch stabs"      -> 整项被删
# 结果 8 首里有 2 首的前奏括号被削到只剩三条中高频件。
# 所以短词一律走词边界正则，"layered" 不再命中 "layer"，"stabs" 不再命中 "stab"。
# 长词（supersaw / reverb / riser...）不存在这种衍生词冲突，保留子串匹配即可。
_RHYTHM_DROP_SUBSTR = (
    "pad", "riser", "swell", "reverb", "supersaw", "saw",
    "ambient", "atmosphere", "texture", "arpeggio",
    "chord stab", "brass", "strings", "choir", "vocal",
    "voice", "singing", "humming", "whisper", "lyric", "melody",
    "counterpoint", "sweep", "noise",
)

# 需要词边界的短词：只命中独立词，不命中 layered / stabs / arps / leading。
#
# "sing" 从 _RHYTHM_DROP_SUBSTR 移到这里（2026-10-05）。之前它待在
# 子串表里，于是两个正当词被整项误删：
#     "every single beat"  含 "sing"  -> 整项删（而这正是要修的量词句）
#     "no missing beats"   含 "sing"  -> 整项删
# "singing" 作为长词留在子串表里继续有效，"single"/"missing" 不再被牵连。
_RHYTHM_DROP_WORD = (
    "pad", "riser", "swell", "lead", "saw", "arp", "stab",
    "layer", "texture", "noise", "sing",
)

_RHYTHM_DROP_RE = tuple(
    re.compile(r"\b%s\b" % re.escape(w), re.I) for w in _RHYTHM_DROP_WORD
)


def _is_drop(part_lc):
    """判断一个逗号分项是否属于空场信号（应从前奏括号剔除）。"""
    if any(bad in part_lc for bad in _RHYTHM_DROP_SUBSTR):
        return True
    return any(rx.search(part_lc) for rx in _RHYTHM_DROP_RE)


def intro_rhythm_only(detail):
    """把配器串整理成"以节奏组为主"的前奏描述，用于 [Instrumental: ...] 括号。

    两步，缺一不可：

    1. 减法。按逗号分项整项剔除 pad / riser / reverb / lead / vocal 等
       空场信号（见 _RHYTHM_DROP）。实测只加禁令无效——GroovePure 已经
       证过四条 "no ambient intro" 类禁令全部无效，因为前奏括号里
       正向的 pad/riser 词仍然占多数，模型按多数走。

    2. 加法兜底。风格字典可能一条贝斯都没写（vocalchill 就是这样，
       过滤后只剩三条中高频）。所以不论过滤结果如何，kick 和 bass
       两条正向要求一定会写进前奏括号（见 _INTRO_MUST）。

    几点设计考虑：

    - 剔除优先级高于保留。像 "white noise riser" 里含 "noise"、
      "big reverb" 含 "reverb"，整项丢掉即可。
    - 分项按逗号切，保留原始大小写和词序，不改写用户的配器描述。
    - 结果为空时回落到 DEFAULT_INTRO_RHYTHM，保证前奏括号不会变空——
      空括号会被模型理解成"这里没有内容"，反而更容易出空场。
    - 不做词干匹配，只做子串匹配：配器描述是自然语言，不是词汇表。
    - 中文配器说明直接走回落：中文会被模型唱出来，且无法按英文词过滤。
    """
    parts_out = []

    if detail and detail.strip() and not re.search(r"[\u4e00-\u9fff]",
                                                    detail):
        for part in detail.split(","):
            p = part.strip()
            if not p:
                continue
            pl = p.lower()
            if _is_drop(pl):
                continue
            if any(good in pl for good in _RHYTHM_KEEP):
                parts_out.append(p)

    kept = ", ".join(parts_out)

    # 加法兜底：kick / bass 两条一定要在。
    # 追加项必须自身含 _RHYTHM_KEEP 词，否则本函数自己就不幂等——
    # 第二次调用时这条刚补上的分项匹配不到任何保留词，会被减法整项剔掉。
    low_kept = kept.lower()
    missing = [ph for key, ph in _INTRO_MUST if key not in low_kept]
    if missing:
        kept = (kept + ", " + ", ".join(missing)).strip(", ") if kept \
            else ", ".join(missing)

    # 第三层：把"弱表述的 kick/bass"升级成"每一拍都在打"。
    #
    # 坑位：_INTRO_MUST 只在 kick / bass 两个词**完全缺失**时才补。
    # ckfunken 写的是 "heavy four on the floor kick on beat one"，
    # 词在，兜底不触发，于是前奏括号里留下的是"第一拍有底鼓"，
    # 其余三拍模型可以自由发挥成空拍。实测这正是 Bounce 前 4 小节
    # 数字静音（sub 掉到 -83dB）的直接来源：它给了模型合法的空拍借口。
    #
    # 所以再加一道：只要没出现"每一拍"级别的表述，就显式补一条。
    # 判据用 every beat / every single beat 这类量词短语，
    # 不看 on beat one / one —— 那是弱表述，挡不住空拍。
    low_now = kept.lower()
    if not _EVERY_BEAT_RE.search(low_now):
        kept = kept + ", " + _INTRO_EVERY_BEAT

    # 第四层：电平要求。_INTRO_LEVEL 已清空（它曾被模型当歌词唱出来），
    # 但保留 if 是为了让"电平要求留在 tags 通道"这件事在代码里可见，
    # 避免以后又有人往括号里加回散文句。
    if _INTRO_LEVEL:
        kept = kept + ", " + _INTRO_LEVEL

    return kept or DEFAULT_INTRO_RHYTHM

# ---- 器乐底轨（分轨用）----
STEM_INST_LYRICS = ("[intro]\n[inst]\n[inst]\n[verse]\n[inst]\n[inst]\n"
                    "[chorus]\n[inst]\n[inst]\n[bridge]\n[inst]\n[outro]")
STEM_INST_TAGS = (
    "pure instrumental track, no vocals anywhere, "
    "no singing, no voice, no humming, no vocal ad-lib, "
    "instrumental only, this is a backing track with no lead vocal, "
    "drums bass and chords only, drums enter on the very first beat"
)


# 吟唱模式：第三张专辑专用（英文低声吟唱，突出音乐本身）。
#
# 五轮实测（InstLowChill / 2 / 3 / 4 / 5）收敛出来的结论。
# 先说结论，再说依据：
#   prompt 对"人声/器乐"的控制力真实存在，旋钮是"标签里有多少句话在描述人声"：
#     描述多   -> 人声密而响（chill 2：voc/acc 0.483，压过音乐）
#     描述中等 -> 稀疏低音语气词（chill 3：voc/acc 0.376，低于纯器乐版）
#     完全不写 -> 人声归零（chill 4 / 5：ASR 全是持续 pad 幻听）
#
# 六条实测判据（都用 vocals stem，不信完整混音）：
#   1. "16 bar 前奏"、"first 20 seconds instrumental"、
#      "voice enters very late"、"in the second half" —— 全部无效，
#      标签侧写时机/时长对人声起点没有任何作用。
#   2. build_chill_lyrics() 的结构重排只降低人声密度，不推迟起点。
#      器乐段排在人声段前面，音频里第一句仍然落在 0 秒。
#      所以下面所有"推迟人声"的注释都已按实测改写，不再声称结构重排有效。
#   3. vocals stem 的 ASR 必须配"能量对齐 + 歌词重合"两个判据才可信：
#      - 能量对齐：真唱段 vocals 轨比全曲中位数高 45～50 dB；
#        幻听段只有 +8～14 dB。
#      - 歌词重合：真唱逐字复现歌词（重合率 0.98）；
#        幻听会造出歌词里没有的词（chill 5 幻听成 "oh new healing"）。
#      - ASR 覆盖率也是判据：铺满全曲 100% 的就是持续 pad 幻听，
#        真唱只覆盖有人唱的段落（chill 3 为 58%）。
#   4. chill 5 的"少量人声"经上述判据判定为幻听，实际等同纯器乐。
#      当前唯一确认真唱的是 chill 3。
#
# 注意：绝不能出现 "no humming / no mm mm syllables / no oh oh syllables"
# 这三条。它们和"低音唱腔英文语气词"是直接冲突的（第一次改 chill
# 铁律就是为了解开这个冲突），加回来等于自己把要求唱的内容禁掉。
RULE_NEG_CHILL = (
    "no opening vocals, no pure vocal intro, no a cappella opening, "
    "no spoken intro, no rap intro talk, no intro talking, "
    "no vocal ad-lib in the opening, no singing in any breakdown, "
    "no singing over the first drop, no choir, no vocal sample, "
    "no long vocal phrases, "
    "no loud vocal belting, no aggressive full-throated singing, "
    "no vocalizing text labels, "
    "no slow start, no fade in, no beatless intro"
)

# 吟唱模式补强：音乐是主角，人声是点缀。
#
# 注意这里绝不能出现 "no long empty instrumental"（RULE_NEG 上面踩过这个坑）：
# 那条约束会逼模型用人声/哼鸣去填满每一处空档，结果就是"只有人声没有音乐"，
# 正好和这一版要的效果相反。器乐段越长、越完整，才越接近 Avicii 那种写法。
#
# 这里刻意不写 "voice is sparse and low volume, used only as sparse texture"。
# InstLowChill3 实测：那一版人声轨 rms 已经降到纯器乐版的同一水平
# (0.0682 vs 0.0680)，人声段从 15 段降到 8 段，但第一句仍然从 0.0 秒唱起。
# 也就是说"把人声写得更轻"是有效的，"把人声写得更晚"完全无效——
# 因为只要标签还在反复讨论 voice/vocal，模型就一直把人声当成本轨的主角，
# 写多少个 [breakdown] 都压不下去。人声只能由结构标记决定何时进场，
# 而不能在标签里被反复提及。
RULE_CHILL_MEMO = (
    "instrumental sections are the main focus, showcase the drop and the hook, "
    "music carries the emotion, long instrumental passages, "
    "extended instrumental breakdown, drums and bass carry the track, "
    "crisp clear English pronunciation, no straining, no autotune warble, "
    "radio ready loudness, dance floor ready"
)

# 吟唱模式保留的"人声"描述。
#
# 这条是 chill 3 和 chill 4/5 之间唯一的旋钮，位置必须固定：
#   chill 3 = 本条 + 密度/时机类描述（voice is sparse、enters late 等）
#             -> 稀疏低音语气词，真唱（能量 +45～50 dB，歌词重合 0.98）
#   chill 5 = 本条 - 密度/时机类描述
#             -> 人声归零，ASR 全部是持续 pad 幻听（+11 dB，重合 0.00）
#   chill 4 = 连本条也砍掉，只留 "male low whisper vocal"
#             -> 同样归零
# 也就是说：删掉"稀疏/很晚/少量"这类密度与时机词，人声立刻消失。
#
# 本轮（chill 6）新做法：不再声称"只靠本条就能产生稀疏人声"。
# 改为把"人声确实存在但很轻"写成独立的一句密度声明，
# 和"音乐是主角"放在同一层，用词避开会被禁掉的 timing 类词。
# 这是从 chill 3 反推出来的最小可用触发词集合。
RULE_CHILL_VOICE = (
    "male low whisper vocal, hushed and intimate, "
    "low register vocal texture, soft breathy phrases, "
    "voice is sparse and low volume, used only as sparse texture, "
    "sparse English words, mostly instrumental between phrases, "
    "no belting, no big singing"
)


# 纯器乐模式主导力：这条讲的是"曲子怎么动"，不是"人声怎么唱"。
RULE_CHILL_ENERGY = (
    "clear dynamic contrast, quiet breakdown, huge powerful drop, "
    "energetic build into every drop, wide stereo image, big reverb, "
    "sidechain compression, no flat lifeless mix, no monotone energy"
)

# 器乐模式下换掉 RULE_ENERGY/RULE_HARMONY：原来那两条在说"verse 轻 hook 响"
# "不要跑调"，那是给人声的建议，纯器乐里只会把模型往人声上推。
RULE_CHILL_HARMONY = (
    "unified minor tonality, no sour notes, no harsh dissonant clusters, "
    "use octaves fifths and fourths, no atonal noise"
)

# chill 分支原本直接复用 RULE_CONSIST，而它写着 "no breakdown"。
# RULE_CHILL_ENERGY 明确要求 quiet breakdown / extended breakdown，
# 两条同时出现就是自相矛盾，模型会挑一条执行——挑错就变回人声填充。
# 这里保留"声线/调性/速度不漂移"的稳定性要求，只把 breakdown 放出来。
#
# 同样去掉 "consistent vocal timbre / no vocal style change"：
# 这两条每轮都在提醒模型"注意人声"，而实测正是"标签里越强调人声，
# 人声越从 0 秒就进"。改成只约束乐器与调性。
RULE_CHILL_CONSIST = (
    "consistent instrumentation in every section, "
    "consistent tuning and timbre for all instruments, "
    "no style drift, no key change, no tempo change mid song"
)


# === 从第一拍就必须是完整 groove ===
#
# 先纠正一个错误结论（ab_bands.py / ab_head_rhythm.py 实测推翻）：
# 上一轮 ab_lowfreq.py 报的"前 15 秒是空场、只有 -70 dBFS"是**错的**。
# 多频段实测（前 16 秒 vs 16~32 秒，单位 dBFS）：
#
#   轨           head sub/low   mid sub/low   head full   mid full
#   GroovePure    -66.0/-69.5    -55.8/-59.4    -32.7       -25.0
#   InstPureEn    -64.6/-67.0    -17.5/-17.8    -31.1       -13.4
#   GrooveChill   -53.5/-44.5    -14.7/-18.3    -25.3       -11.9
#
# 结论完全反过来：前奏**不是空场**。GroovePure 开头有音乐，
# full band -32.7 dBFS（只比中段低 7.7 dB），hat/snare 瞬态也齐备，
# 只是 sub/low 比中段低 10~50 dB —— 缺的是底鼓和贝斯，不是"整个音乐"。
#
# 真正的根因在**歌词体**，不在标签：
# build_inst_lyrics() / build_chill_lyrics() 把风格字典的整条 instruments
# 原样塞进前奏括号：
#     [Instrumental: 8 bar instrumental intro hook, huge supersaw lead,
#      sidechain compressed pad, punchy electro kick, crisp clap, trap snare,
#      white noise riser, big reverb, melodic drop, ...]
# 这串词里会把前奏铺成空场的信号有 4 个
# （supersaw lead / sidechain pad / white noise riser / big reverb），
# 而要求底鼓贝斯从第 0 拍进的只有 kick / clap / snare 三项，
# 3 : 4 正面竞争输了，模型就把前奏铺成 ambient + riser。
#
# 修法分两层：
#   1) 前奏括号只保留节奏组（底鼓/贝斯/鼓组），剔除 lead/pad/riser/reverb
#      —— 见 intro_rhythm_only()，这是本轮真正起作用的改动；
#   2) 标签侧保留一句简短的节奏组正向要求（下面这条）。
#
# 不要试图靠堆更多禁令解决：已经实测过 "no ambient intro / no pad only
# intro / no beatless intro / no white noise riser in the opening" 四条，
# GroovePure 证明无效（开头 sub 仍只有 -66 dBFS）。因为禁令只压"不要什么"，
# 前奏括号里正向的 pad/riser 词仍占多数，模型按多数走。
RULE_GROOVE_FIRST_BEAT = (
    "full four on the floor groove from the very first beat, "
    "kick drum, sub bass and walking bass on bar one, "
    "drums and bass play from the first beat at the same level as the drop, "
    "the rhythm section carries the opening, "
    "catchy synth lead melody plays from the first bar, "
    "instrumental melody hook opens the track, "
    "the intro has a strong melodic hook played by synths, "
    "no pad only intro, no beatless intro, no riser in the opening, "
    "no ambient intro, no vocal pad intro, no vocal texture intro"
)


# 纯器乐模式：整首无人声，只测 prompt 对乐器的控制力。
#
# 为什么必须单独一套：chill 模式仍然有大量唱法描述（voice is sparse、
# vocal hook 等），模型没有理由把人声彻底拿掉。要真正验证"只剩音乐"，
# 标签里必须把所有跟人声沾边的词都清干净，并正面要求器乐段完整展开。
RULE_INST_MEMO = (
    "instrumental sections are the main focus, showcase the drop and the hook, "
    "music carries all of the emotion, no voice at all, "
    "long instrumental passages, extended instrumental breakdown, "
    "drums and bass carry the track, "
    "memorable repeating instrumental hook, strong call and response between "
    "the synth lead and the bass line, dynamic contrast between breakdown and drop, "
    "radio ready loudness, dance floor ready"
)

RULE_INST_NEG = (
    "no vocals anywhere, no singing, no voice, no lead vocal, no backing vocal, "
    "no humming, no mm mm syllables, no oh oh syllables, no vocal ad-lib, "
    "no whisper, no spoken word, no rap, no choir, no vocal sample, "
    "no a cappella, instrumental only, this is a backing track, "
    "drums bass synths chords and lead melodies only, "
    "no opening vocals, no pure vocal intro, no vocal intro, "
    "no slow start, no fade in, no beatless intro, no empty beatless intro, "
    "drums enter on the very first beat"
)

RULE_INST_CONSIST = (
    "consistent instrumentation in every section, no style drift, "
    "consistent tuning and timbre for all instruments, "
    "no key change, no tempo change mid song"
)

# chill 专用前奏：RULE_INTRO 只说"前奏 8 小节后人声才进"，实测不够。
# 第一版 InstLowChill 用的就是 RULE_INTRO，vocals stem 从 0.0 秒就有能量。
#
# 后面几版把这里加长到 16 小节、补"前 20 秒纯器乐"、补"人声只在中段"，
# 实测 vocals stem 的真实人声起点依旧没有推迟。结论有两条：
#   1. 标签侧写多长前奏、写什么时机，对人声起点都没有作用；
#   2. 唯一能把人声起点往后推的旋钮是"标签里密度/时机类人声描述的多少"，
#      而它一旦降到 chill 5 那个水平，人声就整体消失（并不是延后）。
#
# 所以这里只保留纯音乐描述，不提"什么时候进人声"。
# 段落时序交给歌词体结构标记，见 build_chill_lyrics()。
RULE_CHILL_INTRO = (
    "8 bar instrumental intro hook, "
    "instrumental opening only, "
    "the intro bass line and the synth hook are the most memorable catchy element, "
    "rhythmic drums from the very first beat, "
    "intro is instrumental only, no lyrics, "
    "long instrumental passages before any entry, "
    "instrumental breakdown and instrumental drop sections"
)

# 纯器乐专用前奏：RULE_INTRO 里有一句 "vocals enter only after the
# instrumental hook has played"，那是给"先器乐后人声"的有声曲用的。
# 纯器乐里保留它等于直接命令模型"稍后把人声加回来"，所以必须换掉。
RULE_INST_INTRO = (
    "8 bar instrumental intro hook, "
    "instrumental opening only, "
    "the intro bass line and the synth hook are the most memorable catchy element, "
    "rhythmic drums from the very first beat, "
    "no singing in any bar, no voice, no lyrics, no humming, "
    "no vocal ad-lib in the opening"
)


RULE_LEAN_INTRO = (
    "8 bar instrumental intro hook before any vocals, "
    "instrumental opening, "
    "rhythmic drums from the very first beat, "
    "bouncy kick and claps from beat one, "
    "the intro hook is the most memorable catchy element"
)

# 精简模式：只留正向描述，整段 RULE_NEG 不注入。
#
# 为什么需要：用户要的是"开头不是纯人声"，而不是"tags 里塞满 no vocals"。
# 已成功的 dy10_心动预警（kick_ratio 0.637，全批唯一）实测 13 子句、0 条否定词，
# 鼓组照样从第一拍就在。而默认 vocal 路径会注入 RULE_NEG 的 20+ 条人声否定句，
# 标签膨胀到 90 子句 / 2200 字符。对照实验显示标签长度与开头人声响度正相关
# （r=+0.513），且纯器乐版 20 条"无人声"否定句反而伴随最强 vocals stem。
# 所以这里用"正向说清楚要什么"替代"反复说不要什么"。
RULE_LEAN_MEMO = (
    "memorable repeating vocal hook, catchy singable chorus, "
    "strong call and response, dynamic contrast between verse and chorus, "
    "crisp clear pronunciation, radio ready loudness"
)

RULE_LEAN_ENERGY = (
    "clear dynamic contrast, medium verse, loud chorus, "
    "build up into every hook, no flat lifeless mix"
)


def build_tags(style, instruments="", mood="", extra="", trigger=TRIGGER,
               chill_vocal=False, instrumental=False, lean=False):
    """chill_vocal=True 时输出吟唱标签：人声退到点缀位。

    和 instrumental 分支一样，这里完全不接风格字典的 extra。
    vocalchill 的 extra 有六句、其中五句在谈人声（"male low whisper vocal,
    sparse English words, mostly instrumental between phrases, no belting,
    no big singing"），实测正是"标签里越强调人声，人声越从 0 秒就进"。
    chill 只用 RULE_CHILL_VOICE 这一条音色线索，其余全部交给歌词体结构标记。

    instrumental=True 时输出纯器乐标签：清掉所有与人声相关的措辞，
    正向要求器乐段落完整展开，用于验证 prompt 对乐器的控制力。

    lean=True 时走精简正向模式：保留"前奏器乐 + 第一拍鼓"的用户硬约束，
    但不注入 RULE_NEG 那 20+ 条人声否定句。给标签短、靠音色词自然带鼓的
    甜系 pop 这类风格用。
    """
    if instrumental:
        # 风格字典的 extra 写的是唱法（"male low whisper vocal" 之类），
        # 纯器乐下它会跟 RULE_INST_NEG 的 "no vocals anywhere" 当场对撞。
        # 所以这里不接 extra，只接风格名/配器/情绪三段。
        parts = [trigger, style.strip(), instruments.strip(), mood.strip(),
                 RULE_INST_MEMO, RULE_INST_INTRO, RULE_GROOVE_FIRST_BEAT,
                 RULE_CHILL_HARMONY,
                 RULE_CHILL_ENERGY, RULE_INST_CONSIST, RULE_INST_NEG,
                 RULE_NO_FILLER]
        return ", ".join(p for p in parts if p)
    if chill_vocal:
        parts = [trigger, style.strip(), instruments.strip(), mood.strip(),
                 RULE_CHILL_MEMO, RULE_CHILL_INTRO, RULE_GROOVE_FIRST_BEAT,
                 RULE_CHILL_HARMONY,
                 RULE_CHILL_ENERGY, RULE_CHILL_CONSIST,
                 RULE_CHILL_VOICE, RULE_NEG_CHILL, RULE_NO_FILLER]
        return ", ".join(p for p in parts if p)
    parts = [trigger, style.strip(), instruments.strip(), mood.strip(),
             RULE_MEMO, RULE_INTRO, RULE_HARMONY, RULE_ENERGY, RULE_CONSIST,
             RULE_GROOVE_FIRST_BEAT,
             extra.strip(), RULE_NEG, RULE_NO_FILLER]
    if not lean:
        # 默认分支同样必须挂 RULE_GROOVE_FIRST_BEAT（2026-10-05 补）。
        # 之前只有 lean / chill / instrumental 三条分支挂了这条规则，
        # 默认分支漏挂，于是 tags 里唯一一句写明"鼓和贝斯从第 0 拍进、
        # 且和 drop 同电平"的话根本不存在——本批 8 首全是 lean 模式，
        # 所以这个洞一直没被发现；任何非 lean 调用都会踩到。
        return ", ".join(p for p in parts if p)
    # lean：同样的硬约束，但用正向措辞，且不注入 RULE_NEG。
    #
    # RULE_NEG 被跳过是因为它和"前奏不要人声"的否定句膨胀有关（标签越长
    # 开头人声越响，r=+0.513）。但 RULE_NO_FILLER 管的是**整首歌段落之间
    # 的衬音/和声/ad-libs**，跟前奏人声起点无关。lean 跳过它纯属附带损伤——
    # 实测每首歌句首句尾都出现 嗯嗯/啊啊/oh oh/hey/yeah 等衬词，用户
    # 反馈"一听就划走"。所以从 2026-10-08 起把 RULE_NO_FILLER 加回 lean。
    parts = [trigger, style.strip(), instruments.strip(), mood.strip(),
             RULE_LEAN_MEMO, RULE_LEAN_INTRO, RULE_GROOVE_FIRST_BEAT,
             RULE_LEAN_ENERGY, RULE_HARMONY, RULE_CONSIST,
             extra.strip(), RULE_NO_FILLER]
    return ", ".join(p for p in parts if p)


def build_inst_lyrics(intro_detail=None, bars=8):
    """纯器乐歌词体：只有结构标记，没有任何可唱正文。

    结构段名刻意避开 [verse] / [chorus]。ACE-Step 1.5 见到这两个标记
    就会按"有人唱的段落"来铺内容，即使下面只挂 [inst]，也容易把人声
    请回来。纯器乐用 intro / breakdown / build / drop 这些纯器乐段名，
    模型只会把对应位置铺成器乐。

    intro_detail 会先过 intro_rhythm_only()：前奏括号里只留节奏组，
    否则 pad/riser/lead 词会把前奏铺成 ambient 空场（实测 GroovePure
    开头 sub 只有 -66 dBFS，中段是 -55.8 dBFS）。
    """
    d = intro_rhythm_only(intro_detail)
    # 括号里只写配器名词短语。"from the very first beat" 这类介词短语
    # 会被 _INTRO_CLAUSE_RE 判为可唱散文——它确实该写进 tags
    # （RULE_GROOVE_FIRST_BEAT），不该出现在歌词条件通道里。
    head = ("[Instrumental: %d bar instrumental intro hook, %s, "
            "four on the floor groove, beat one kick and bass, "
            "no vocals, no voice, no singing]" % (bars, d))
    body = [
        "[intro]",
        head,
        "[instrumental]",
        "",
        "[breakdown]",
        "[inst]",
        "",
        "[build]",
        "[inst]",
        "",
        "[drop]",
        "[inst]",
        "",
        "[breakdown]",
        "[inst]",
        "",
        "[build]",
        "[inst]",
        "",
        "[final drop]",
        "[inst]",
        "",
        "[post-drop]",
        "[inst]",
        "",
        "[breakdown]",
        "[inst]",
        "",
        "[final drop]",
        "[inst]",
        "",
        "[outro]",
        "[inst]",
    ]
    return "\n".join(body) + "\n"


def build_intro(detail=None, bars=8):
    """器乐前奏描述。必须纯英文，中文说明词会被模型唱出来。"""
    d = detail or DEFAULT_INSTRUMENT
    return ("[Instrumental: %d bar instrumental hook, %s, "
            "drums enter on the first beat, no vocals, no humming]" % (bars, d))


def build_lyrics(sections, intro_detail=None, bars=8, tail=None):
    """sections 为 [(tag, text)]，首段强制为器乐前奏。

    intro_detail 若是中文会被自动降级为默认英文配器。
    """
    if intro_detail and re.search(r"[\u4e00-\u9fff]", intro_detail):
        intro_detail = None
    out = ["[intro]", build_intro(intro_detail, bars), "[instrumental]", ""]
    for tag, text in sections:
        out.append(tag)
        out.append(text.strip())
        out.append("")
    if tail:
        out.append(tail.strip())
    return "\n".join(out) + "\n"


def strip_cn_instrumental(lines):
    """清掉正文里 [Instrumental: ...] 的中文说明，防止被唱出来。"""
    out = []
    for ln in lines:
        s = ln.strip()
        m = re.match(r"^\[Instrumental:[^\]]*\]$", s, re.I)
        if m and re.search(r"[\u4e00-\u9fff]", s):
            out.append("[Instrumental: no vocals, drums and bass only]")
            continue
        out.append(ln)
    return out


# chill 结构体里循环使用的纯器乐段模板。
# 只用 intro / breakdown / build / drop / post-drop 这些纯器乐段名，
# 不出现 [verse] / [chorus]，避免模型提前把"有人唱的段落"排到开头。
_CHILL_INST_CYCLE = [
    ("[breakdown]", "[inst]"),
    ("[build]", "[inst]"),
    ("[drop]", "[inst]"),
    ("[post-drop]", "[inst]"),
]


def build_chill_lyrics(sections, intro_detail=None, bars=8, total_bars=90):
    """chill 吟唱歌词体：把器乐段整体排在人声段之前。

    实测效果（必须照实说，不能夸大）：
        这个重排确实把 人声密度 压下来了（chill 3 的 voc/acc 0.376，
        比纯器乐版的 0.389 还低），但它并没有把 人声起点 往后推——
        chill 3 的第一句真唱仍然落在 0.0 秒。
        所以它是"密度工具"，不是"推迟工具"。

    为什么不能靠标签解决：
        ACE-Step 按歌词体的结构标记顺序分配段落时间，不按时长。
        InstLowChill2 的标签已经写了 16 小节前奏 + "前 20 秒纯器乐"
        + "人声不进第二次 drop"，vocals stem 仍然从 0.0 秒唱起。

    total_bars 来自 meter.capacity()，用它算出器乐段要占多少小节，
    让"器乐占大部分时长"这个听感成立，而不是随手写三段 [inst]。
    """
    d = intro_rhythm_only(intro_detail)
    out = ["[intro]",
           ("[Instrumental: %d bar instrumental intro hook, %s, "
            "four on the floor groove, beat one kick and bass, "
            "no vocals, no voice, no singing]" % (bars, d)),
           "[instrumental]", ""]

    # 先铺满器乐段。留 1/4 给结尾的器乐收尾，其余全放前面。
    inst_bars = max(4.0, float(total_bars) - float(bars))
    tail_bars = max(2.0, inst_bars * 0.25)
    head_bars = inst_bars - tail_bars
    unit = max(1.0, float(bars)) * 2.0
    cycles = max(2, int(round(head_bars / unit)))

    for i in range(cycles):
        tag, mark = _CHILL_INST_CYCLE[i % len(_CHILL_INST_CYCLE)]
        out += [tag, mark, ""]

    for tag, text in sections:
        out += [tag, text.strip(), ""]

    tail = max(1, int(round(tail_bars / float(bars))))
    for i in range(tail):
        tag, mark = _CHILL_INST_CYCLE[(i + 2) % len(_CHILL_INST_CYCLE)]
        out += [tag, mark, ""]
    out += ["[outro]", "[inst]"]
    return "\n".join(out) + "\n"


def filter_intro_bracket(lyrics):
    """把已有 [intro] 里的 [Instrumental: ...] 括号过一遍 rhythm 过滤。

    为什么手写歌词也要过滤（2026-10-05）：
        assemble() 原来分两条路。build_lyrics() / build_chill_lyrics() /
        build_inst_lyrics() 这三条自己拼前奏，都调用了 intro_rhythm_only()；
        但"歌词文件已经自带 [intro] 头"这条分支只调 force_intro_bars()
        改了个小节数，括号内容原样透传。
        于是作者手写的前奏描述成为唯一一条绕过过滤的路径——而
        intro_rhythm_only() 的整段注释（pad/riser 词数 3:4 压过 kick/snare
        3 项，模型按多数走）说的正是这条路径会出的问题。实测 ckfunk
        批次 12/16 个 seed 前奏 sub 掉到 -80 dB 以下。

    只处理 [intro] 段里的第一行括号，段落正文一个字都不动：
        正文是要唱的，过滤词表（lead/melody/vocal 等）是为配器串设计的，
        拿去过滤歌词会把 "I lead me to the dance floor" 这类正常句子改坏。

    括号被过滤到全空时不能空手进模型：空括号等于"这里没内容"，模型更容易
    直接铺成空场。此时退回 DEFAULT_INTRO_RHYTHM。
    作者写的前奏描述优先，不拿风格字典去覆盖它——作者可能刻意写了
    "808 sub slide into the hook" 这种会被整项丢掉的词。
    """
    lines = lyrics.split("\n")
    out = []
    in_intro = False

    def _emit(text, src):
        """按原行尾回写，避免 CRLF 文件被改写成混合行尾。

        split("\\n") 之后 CRLF 的 "\\r" 仍挂在行尾。直接拼新串会把它丢掉，
        而其余行还带着 "\\r"，join 之后同一文件里就有两种行尾了。
        """
        if src.endswith("\r") and not text.endswith("\r"):
            return text + "\r"
        return text

    for ln in lines:
        s = ln.strip()
        if re.match(r"^\[intro\]$", s, re.I):
            in_intro = True
            out.append(ln)
            continue
        # 顺序要紧：必须先判 [Instrumental: ...]，再判"段落结束"。
        # 反过来的话它自己就以 "[" 开头，会被当成 intro 收尾，
        # 过滤分支永远进不去（2026-10-05 修的就是这个）。
        if in_intro and re.match(r"^\[instrumental:", s, re.I):
            detail = re.sub(r"^\[Instrumental:\s*", "", s, flags=re.I)
            detail = re.sub(r"\s*\]$", "", detail)
            # 中文括号整段放行。
            #
            # intro_rhythm_only() 的分项词表全是英文，中文项匹配不到任何
            # KEEP / DROP，只会走兜底把整段替换成 DEFAULT_INTRO_RHYTHM——
            # 也就是把作者写的中文前奏描述整句丢掉换成英文。作者写中文说明
            # 他清楚自己要什么，改写他反而破坏意图。前奏低频现在主要由标签
            # 侧的 RULE_GROOVE_FIRST_BEAT 兜住，不依赖这里过滤中文。
            if re.search(r"[\u4e00-\u9fff]", detail):
                out.append(ln)
                in_intro = False
                continue
            # 保留 "8 bar instrumental intro hook" 这类纯结构性前缀，
            # 只对逗号后面的配器串做过滤。
            #
            # 这里必须先验前缀"干净"再决定保留，不能只看有没有逗号。
            # 正则里 (?:,\s*(.*))? 是可选组：写
            #     [Instrumental: 8 bar intro hook with ambient pad]
            # 这种没有逗号的句子时，lazy 的 .*? 会回溯到把整串吃进前缀，
            # rest 为空 → 过滤完全不执行，pad 原样留在前奏里。
            # 所以前缀只接受不含任何配器词、只由 bar/instrumental/intro/
            # hook/groove/break 这类结构词组成的形态，否则整串送过滤。
            prefix, rest = "", detail
            mpre = re.match(
                r"^(\d+\s*bar\b[^,]*?)\s*(?:,\s*(.*))?$", detail, re.I | re.S
            )
            if mpre and _INTRO_PREFIX_OK.match(mpre.group(1).strip()):
                prefix, rest = mpre.group(1).strip(), mpre.group(2) or ""
            newd = prefix
            if rest.strip():
                filtered = intro_rhythm_only(rest)
                if filtered:
                    newd = ("%s, %s" % (prefix, filtered)) if prefix \
                        else filtered
            elif not prefix:
                newd = DEFAULT_INTRO_RHYTHM
            out.append(_emit("[Instrumental: %s]" % newd, ln))
            in_intro = False
            continue
        # intro 段结束：遇到下一个结构标记就收手
        if in_intro and s.startswith("["):
            in_intro = False
        out.append(ln)
    return "\n".join(out)


def force_intro_bars(lyrics, bars):
    """把已有 [intro] 头里的前奏小节数改写为 bars。

    标签和歌词头必须写同一个数字。之前 chill 标签改成 16 小节、歌词头还是
    8 小节，模型同时收到两个互相矛盾的指令，前奏长度完全随机。
    这里只改数字，不动描述内容。
    """
    def _repl(_m):
        return "%d bar" % bars

    out = []
    for i, ln in enumerate(lyrics.split("\n")):
        if i >= 12:
            out.append(ln)
            continue
        s = ln.strip()
        if s.lower().startswith("[instrumental:") and \
                re.search(r"\d+\s*bar", s, re.I):
            out.append(re.sub(r"(\d+)\s*bar", _repl, s, count=1, flags=re.I))
        else:
            out.append(ln)
    return "\n".join(out)


# 前奏括号里只允许出现"配器名词短语"的机器判据。
#
# 2026-10-05 实测踩坑（两次）：
#     1. _INTRO_LEVEL 写成散文句 -> Bounce_s8 把
#        "The opening grooves in base front, ace front and center at full
#        level." 和 "No quiet or sparse openin' No fading in, no swelling in"
#        原样唱了出来。逐词对得上，且不在作者手写的 Bounce.txt 里。
#     2. _INTRO_EVERY_BEAT 写成 24 词的长句 -> 同样全是可唱的英文。
#
# 括号是**歌词条件通道**，不是 prompt 通道。判断标准因此不是"语义是否
# 合理"，而是"这是不是一个会被唱出来的句子"。
#     配器名词短语  punchy kick, deep sub bass, tight closed hi-hats
#     要求散文      the beat drops hard right at the start
# 前者没有子句，后者有。
#
# 命中集合刻意只收"配器描述里绝对不会出现"的成分：系动词、否定副词、
# 义务/能力情态、代词、从属连词、介词短语引导词。
#
# 下面这些**不能**收，否则会误伤本批真实括号（全部为实测样例）：
#     and      "tambourine and hand claps"
#     with     "dry crisp snare with ghost notes"
#     on       "tight closed hi-hats accenting the offbeats"
#     every    "dry crisp snare with ghost notes on every sixteenth"（拍一下）
#     no       "no vocals, no voice, no singing"（build_inst_lyrics 硬编码）
#     bar      "kick drum and bass on bar one"
# 前四个是配器描述的正常连接词/副词；后两个虽然也是散文，但它们表达的是
# "这里没有人声"，属于结构标记而非会被当内容唱的句子，且已由 tags 侧
# RULE_INTRO / RULE_NEG 覆盖，歌词括号里重复写没有额外收益。
_INTRO_CLAUSE_RE = re.compile(
    r"\b(?:is|are|was|were|am|be|been|being|"
    r"not|never|none|nothing|neither|nor|"
    r"must|should|will|would|can|could|may|might|"
    r"it|its|they|them|their|you|your|our|we|us|his|her|she|him|"
    r"that|which|who|whose|because|than|then|when|while|where|why|how|"
    r"if|though|although|so|but|"
    r"from|into|onto|upon|through|across|around|during|"
    r"before|after|until|unless|whether)\b",
    re.I,
)

# 已经实证被模型唱出来的具体措辞（2026-10-05，Bounce_s8）。
#
# 为什么单独列：上面那套子句成分判据是"结构信号"，而泄漏句里最狠的几条
# 恰恰不含任何子句成分——它们是一串并列的动名词/介词短语：
#     the opening groove is loud and punchy from bar one ...  <- 含 is/from，被抓
#     no fading in, no swelling in                              <- 不含任何子句词
# 只靠 _INTRO_CLAUSE_RE 放行了第二句。所以把真实泄漏过的措辞原样列出来，
# 命中即报错。这不是猜测，是从 ASR 输出里逐词抄回来的。
_INTRO_LEAK_PHRASE_RE = re.compile(
    r"fad(?:ing|e)\s+in|"
    r"swelling\s+in|"
    r"swell(?:ing|s)\s+up|"
    r"quiet\s+or\s+sparse|"
    r"sparse\s+open(?:ing|ing')|"
    r"front\s+and\s+center|"
    r"at\s+full\s+level|"
    r"louder\s+than\s+the\s+vocals?|"
    r"no\s+empty\s+beat|"
    r"no\s+gaps?,?\s+no\s+rests?|"
    r"beats?\s+up\s+from|"
    r"drop(?:s|ped)?\s+hard",
    re.I,
)


def _check_intro_bracket_speakable(head):
    """前奏括号里不许出现会被唱出来的散文句。

    为什么必须机器判（不靠 code review 兜住）：括号内容直接进模型的
    歌词条件通道，已经两次因为往里塞长句而把生成指令唱进了成品。
    第一次靠注释提醒，第二次照样漏——所以把规则写成检查。

    两套判据，取并集：
      1. _INTRO_CLAUSE_RE  —— 结构性信号（系动词 / 代词 / 介词短语…）
      2. _INTRO_LEAK_PHRASE_RE —— 实证泄漏过的具体措辞
    只用第 1 套会漏掉 "no fading in, no swelling in" 这类纯并列短语，
    只用第 2 套则拦不住新写的散文句。两套互补，缺一不可。

    白名单前缀 "8 bar instrumental intro hook" 只含 bar / instrumental /
    intro / hook，不含任何子句信号，不会被误判。
    """
    errs = []
    m = re.search(r"\[instrumental:(.*?)\]", head, re.I | re.S)
    if not m:
        return errs
    body = m.group(1)
    hits = _INTRO_CLAUSE_RE.findall(body)
    if hits:
        uniq = sorted(set(h.lower() for h in hits))
        errs.append(
            "前奏括号含会被唱出的子句成分 [%s]：括号是歌词条件通道，"
            "只允许配器名词短语（kick / sub bass / snare / hand claps），"
            "要求句与量词句请写进 tags"
            % ", ".join(uniq)
        )
    leak = _INTRO_LEAK_PHRASE_RE.findall(body)
    if leak:
        uniq = sorted(set(l.lower() for l in leak))
        errs.append(
            "前奏括号含已实证会被唱出的生成指令措辞 [%s]："
            "这几句在 Bounce_s8 的成品里被原样唱了出来，"
            "同类要求请写进 tags（RULE_GROOVE_FIRST_BEAT）"
            % ", ".join(uniq)
        )
    return errs


def check_rules(lyrics, tags, lean=False):
    """结构铁律校验。返回问题列表。

    lean=True 时不检查 RULE_NEG 的两条人声否定句（"no opening vocals" /
    "no pure vocal intro"），因为 lean 刻意不注入 RULE_NEG。

    但 RULE_NO_FILLER（衬音禁令）在 lean 下仍然必须存在：它管的是整首歌
    段落之间的衬音填充，跟前奏人声起点无关。2026-10-08 起加回 lean 路径。
    "swlvoice" 触发词始终要求保留（跟 LoRA 权重绑定）。
    """
    errs = []
    if not lyrics.lstrip().lower().startswith("[intro]"):
        errs.append("歌词首行必须是 [intro]")
    head = lyrics[:400]
    if "[instrumental:" not in head.lower():
        errs.append("歌词开头必须有 [Instrumental: ...] 器乐前奏描述")
    elif not re.search(r"\[instrumental:[^\]]*\d+\s*bar", head, re.I):
        errs.append("器乐前奏必须写明小节数（如 '8 bar instrumental hook'）")
    for ln in lyrics.split("\n"):
        s = ln.strip()
        if s.startswith("[") and s.endswith("]") and \
                re.search(r"[\u4e00-\u9fff]", s):
            errs.append("结构标记含中文会被唱出来: %s" % s[:40])
            break
    errs += _check_intro_bracket_speakable(head)
    low = tags.lower()
    need_neg = not lean
    for kw in ("no opening vocals", "no pure vocal intro", "swlvoice"):
        if kw == "swlvoice" or need_neg:
            if kw not in low:
                errs.append("tags 缺少 '%s'" % kw)
    # RULE_NO_FILLER 在 lean 下也必须存在：它是唯一一条约束整首歌
    # 人声只唱歌词、不唱衬音的规则。lean 跳过 RULE_NEG 是因为前奏否定句
    # 膨胀，但衬音禁令跟前奏无关，不能一起跳过。
    # 2026-10-08：从负面禁词（no ooh / no 嗯嗯）改为正面强制
    # "sing only the exact words"，校验关键词也跟着改。
    for kw in ("sing only the exact words", "instrumental only"):
        if kw not in low:
            errs.append("tags 缺少 '%s'（衬音禁令，lean 下也必须存在）" % kw)
    # 器乐前奏小节数：普通歌曲 8 小节，chill 吟唱 8 小节，纯器乐 8 小节。
    # 三种模式现在统一 8 小节（chill 曾试过 16，实测对人声起点毫无作用）。
    # 这里仍然只校验"写了且在 4~32 范围内"，不写死具体数字：
    # 旧校验把整句 "8 bar instrumental intro hook" 写死后，chill 换成 16 小节
    # 直接被拦下，而且报错完全指不到真实原因。改成匹配"N bar"后，
    # 两种措辞（带 hook / 不带 hook）都接受。
    m = re.search(r"(\d+)\s*bar instrumental intro(?: hook)?", low)
    if not m:
        errs.append("tags 必须写明器乐前奏小节数"
                    "（如 '8 bar instrumental intro hook'）")
    elif not (4 <= int(m.group(1)) <= 32):
        errs.append("器乐前奏小节数 %s 超出合理范围 4~32"
                    % m.group(1))
    else:
        # tags 与歌词头必须写同一个小节数。之前 chill 标签改成 16 小节、
        # 歌词头还停在 8 小节，模型同时收到两个互相打架的指令，
        # 结果是前奏长度完全随机。这里直接把不一致判成错误。
        mh = re.search(r"\[instrumental:[^\]]*?(\d+)\s*bar", head, re.I)
        if mh and int(mh.group(1)) != int(m.group(1)):
            errs.append("tags 写 %s 小节前奏，歌词头写 %s 小节，"
                        "两处必须一致"
                        % (m.group(1), mh.group(1)))
    return errs


def check_lexicon(lyrics, lexicon):
    """身份词典校验。命中禁用词直接报错，防止再写出长板/护具。"""
    errs = []
    body = "\n".join(l for l in lyrics.split("\n")
                     if l.strip() and not l.strip().startswith("["))
    low = body.lower()
    for key, words in (lexicon.get("banned") or {}).items():
        if key.startswith("_"):
            continue
        for w in words:
            if w.lower() in low:
                errs.append("命中禁用词[%s]: %s（身份是街头双翘滑手）" % (key, w))
    return errs


def validate(lyrics, tags, lexicon=None):
    """全部铁律 + 词典校验，不过就抛。"""
    errs = check_rules(lyrics, tags)
    if lexicon:
        errs += check_lexicon(lyrics, lexicon)
    if errs:
        raise RuleViolation("违反生歌铁律：\n  - " + "\n  - ".join(errs))
    return True


def enforce_lexicon(lyrics, lexicon):
    errs = check_lexicon(lyrics, lexicon)
    if errs:
        raise LexiconError("歌词身份校验失败：\n  - " + "\n  - ".join(errs))
    return True
