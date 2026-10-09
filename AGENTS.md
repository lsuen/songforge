# songforge — Agent 生歌手册

> 远程 / channel Agent 读这份文件即可做歌。上下文不够时**重新打开本文件**，不要凭记忆改参数。
> 人类规范全文见 `../生歌规则.md`。行为冲突时以那份 + `config/default.json` 为准。

## 0. 你是谁、要交什么

你是 songforge 流水线操作员。交付物是：

1. 一次 `gen` 的 `run_id`
2. ComfyUI 产出的 flac
3. `qc --baseline` 结论为 **PASS**

`style_fam` 只是弱参考，不要把它当 PASS 条件，也不要对外说「风格已验证」。

## 1. 工作目录（硬）

```text
cd D:\CodeFiles\Thinking\ComfyUI-MiniMaxH3\user\default\workflows
```

唯一入口：

```text
python songforge\songforge.py <命令>
```

禁止：手写 tags / 手写前奏 / 改 workflow JSON 调参 / 换 YuE2·Music3·aio / 放宽 QC 阈值。

## 2. 强制流水线（按顺序）

```text
doctor → target → 写歌词 → check → gen → qc --baseline
```

```powershell
python songforge\songforge.py doctor
python songforge\songforge.py target --style pop --bpm 104 --dur 180
# 按 target 打印的放行区间写歌词（勿手写 [intro] / tags）
python songforge\songforge.py check --style pop --title 歌名 --lyrics-file <路径.txt> --bpm 104 --dur 180
python songforge\songforge.py gen   --style pop --title 歌名 --lyrics-file <路径.txt> --bpm 104 --dur 180
python songforge\songforge.py qc    --run <run_id> --baseline
```

- `check` 不过 → **只改歌词**，不准改 shift/steps/cfg/temperature
- `doctor` 服务不可达 → 先起 ComfyUI（`http://127.0.0.1:8188`）
- `qc` 必须带 `--baseline`；`--run` 用 `gen-...` 或 `songforge\data\runs\<日>\gen-...` 均可
- 索引：`python songforge\songforge.py catalog` / `catalog --rebuild`

## 3. 硬锁定（不可谈判）

| 项 | 值 |
|----|-----|
| checkpoint | `ace_step_1.5_turbo_swl_fused.safetensors` |
| LoRA | `swl_voice_best_ep257.safetensors`（0.5 / 0.5） |
| 采样 | shift=5.5 / steps=28 / cfg=2.0 / temperature=0.68 |
| 时长 | 完整歌 **150–200** 秒（默认跟风格字典；pop 常 180） |
| 前奏 | 8 小节器乐 hook，第一拍进鼓；CLI `assemble` 自动注入 |

改算法参数只动 `config/default.json`；本机路径用 `config/local.json`（gitignore）。不要改 `data/runs/` 里的 JSON。

配置优先级：

```text
命令行 > SONGFORGE_* > config/local.json > config/default.json > DEFAULTS
```

## 4. 目录地图（项目 vs 数据）

路径键在 `config/default.json` → `paths`。相对 `user/default/workflows`：

| 级别 | 用途 | 默认路径 |
|------|------|----------|
| 项目 | 歌词投放 | `songforge/input/lyrics/` |
| 项目 | QC/风格基线 | `songforge/config/baseline/` |
| 项目 | 规范文档 | `生歌规则.md` / `AGENTS.md` / `docs/使用手册.md` |
| 数据 | 正式运行 | `songforge/data/runs/YYYY-MM-DD/gen-*/` |
| 数据 | 临时会话 | `songforge/data/tmp/YYYY-MM-DD/` |
| 数据 | 全局索引 | `songforge/index/catalog.jsonl` |
| 数据 | QC/风格缓存 | `songforge/cache/qc/` `cache/style/` |
| 外部 | 音频成品 | `<ComfyUI>/output/MyMusic/`（`local.json` 可改） |

本机路径写 `config/local.json`（从 `local.json.example` 复制）：`comfy_root` / `qc_python` / `music_dir`。

## 5. 写歌词要点

1. 先 `target`，正文音节落进放行区间（偏稀 → 模型用拖腔填空）。
2. **不要**写 `[intro]` / Instrumental 前奏 / tags——`assemble` 会注入。
3. 段落用 `[verse]` `[chorus]` 等英文结构标记。
4. 默认密度档 = 人声填满；英文低音吟唱用 `--chill-vocal`（自动 sparse）。

可用素材示例（区间已合适）：

```text
songforge\_archive\lyrics_corpus\拍一下.txt
```

## 6. 失败速查

| 现象 | 动作 |
|------|------|
| doctor FAIL 服务 | 启动 ComfyUI |
| check 密度失败 | 改歌词到 target 区间 |
| gen 提交失败 | 跑 `selftest`；用 `--dry-run` 看 payload |
| qc voice_nn=null | 漏了 `--baseline` |
| qc FAIL | 修歌/重 gen，**不**改阈值 |
| CLI 崩溃 | `--dry-run` 或 fallback 旧 `remake_v5.py`（勿删） |

怀疑 CLI 坏了：

```powershell
python songforge\songforge.py selftest
```

## 7. 上下文不够时

1. 重读本文件第 1–3 节。
2. 打开 `songforge/config/default.json` 核对模型/采样/paths。
3. 打开本次 `runs/<run_id>/run.log` 与 `manifest.json`。
4. 不要发明新采样参数或新模型名。

## 8. 最小验收清单

```text
[ ] doctor 全绿（模型锁定 + 采样在带内）
[ ] check OK
[ ] gen 产出 flac，时长在 150–200
[ ] qc --baseline → [PASS]
[ ] 未改阈值、未换模型
```
