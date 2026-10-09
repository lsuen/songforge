# songforge — Agent 手册（必读）

> **任何 Agent / 自动化：做歌或改本仓前先读完本文。** 上下文不够时**重新打开本文件**，不要凭记忆改参数。
>
> 人类操作细节见 [`docs/使用手册.md`](docs/使用手册.md)。算法默认见 [`config/default.toml`](config/default.toml)。
> 若仍嵌套在 ComfyUI 大仓，踩坑长文还可对照 `user/default/workflows/生歌规则.md`；**冲突时以本仓 `default.toml` + 本文为准**。

---

## 0. 你是谁、交什么

你是 songforge 流水线操作员。一次合格交付：

1. `gen` 的 `run_id`
2. ComfyUI 产出的 flac（路径在 manifest / catalog）
3. `qc --baseline` → **PASS**

`style_fam` / `style_conf` 是弱参考，**不要**当 PASS 条件，也**不要**对外说「风格已验证」。

---

## 1. 遇事先问（硬规则）

下列情况**停止猜测、先向用户提问**，得到答案再继续：

| 情况 | 要问什么 |
|------|----------|
| 没有 `config/local.toml` | ComfyUI 安装根路径？服务地址（默认 `http://127.0.0.1:8188`）？QC 用的 Python？ |
| `doctor` 报服务不可达 | ComfyUI 是否已启动？端口是否不是 8188？ |
| `doctor` 报缺 checkpoint/LoRA | 模型是否装在该 `comfy_root`？能否指出模型目录？ |
| 用户没说风格 / 歌名 / 主题 | `--style`、标题、情绪或参考歌？中文还是英文？ |
| 歌词来源不明 | 用户提供正文，还是要你按 `target` 区间新写？ |
| `qc` 连续 FAIL 且原因不像歌词密度 | 是重 gen、换 seed，还是先听某一版再决定？ |
| 要改采样 / 换模型 / 放宽 QC | **默认拒绝**；若用户坚持，先说明会破坏铁律，再确认是否真的要改 `default.toml` |
| 要提交 `.secrets/`、`local.toml`、token | **拒绝**；问用户是否只推代码 |
| 仓库路径 / clone 位置不清 | 当前工作目录是哪？独立仓还是嵌套在 ComfyUI 里？ |
| 两个远程（GitHub / Gitea）要推哪个 | 推哪个 remote？是否允许 push？ |

可以自行处理、**不必问**的：

- `check` 密度失败 → 只改歌词到 `target` 区间后重 check
- 漏了 `--baseline` → 补上再 qc
- `catalog` 空 → `catalog --rebuild`
- 明显笔误的歌词标点 / 多空行（不改采样）

---

## 2. 首次上手（独立仓，推荐）

本仓可 clone 到任意目录，**不必**放进 ComfyUI 的 `workflows`。

```powershell
cd <任意父目录>
git clone https://git.nnrc.ren/swl/songforge.git
# 或 https://github.com/lsuen/songforge.git
cd songforge

.\scripts\doctor_all.ps1
# 缺 local.toml 时会复制 example 并退出；编辑并启动 ComfyUI 后再跑
# 模型文件名见 README「需要的模型」；没有作者 LoRA 时先问用户
```

入口（在**仓根**）：

```powershell
.\.venv\Scripts\python.exe songforge.py <命令>
```

若仍嵌套在 ComfyUI：

```powershell
cd <ComfyUI>\user\default\workflows
python songforge\songforge.py <命令>
```

**禁止：** 手写 tags / 手写前奏 / 改 workflow JSON 调参 / 换 YuE2·Music3·aio / 放宽 QC 阈值 / 把 token 写进文档或提交进 git。

---

## 3. 强制流水线

```text
doctor → target → 写歌词 → check → gen → qc --baseline
```

```powershell
.\.venv\Scripts\python.exe songforge.py doctor
.\.venv\Scripts\python.exe songforge.py target --style pop --bpm 104 --dur 180
# 按 target 区间写歌词到 input/lyrics/xxx.txt（勿写 [intro] / tags）
.\.venv\Scripts\python.exe songforge.py check --style pop --title 歌名 --lyrics-file input\lyrics\xxx.txt --bpm 104 --dur 180
.\.venv\Scripts\python.exe songforge.py gen   --style pop --title 歌名 --lyrics-file input\lyrics\xxx.txt --bpm 104 --dur 180
.\.venv\Scripts\python.exe songforge.py qc    --run <run_id> --baseline
.\.venv\Scripts\python.exe songforge.py catalog --limit 20
```

- `check` 不过 → **只改歌词**
- `doctor` 服务不可达 → 请用户启动 ComfyUI；核对 `local.toml`
- `qc` 必须带 `--baseline`；`--run` 可用 run_id 或 `data/runs/...` 路径
- 索引：`catalog` / `catalog --rebuild`

---

## 4. 硬锁定（不可谈判）

| 项 | 值 |
|----|-----|
| checkpoint | `ace_step_1.5_turbo_swl_fused.safetensors` |
| LoRA | `swl_voice_best_ep257.safetensors`（0.5 / 0.5） |
| 采样 | shift=5.5 / steps=28 / cfg=2.0 / temperature=0.68 |
| 时长 | 完整歌 **150–200** 秒 |
| 前奏 | 8 小节器乐 hook，第一拍进鼓；CLI `assemble` 自动注入 |

- 算法 / 相对路径 → `config/default.toml`（可提交）
- 本机绝对路径 → `config/local.toml`（gitignore，从 `.example` 复制）
- workflow 图 → `workflows/template.json`（JSON）；**不要**用改图来改采样

```text
命令行 > SONGFORGE_* > local.toml > default.toml > DEFAULTS
```

| 环境 | 用途 |
|------|------|
| 本仓 `.venv` | CLI |
| `local.toml` → `qc_python` | QC（librosa/torch/demucs，可指向 ComfyUI venv） |

---

## 5. 目录地图（相对仓根）

| 级别 | 路径 |
|------|------|
| 项目 | `input/lyrics/`、`config/baseline/`、`workflows/template.json`、`AGENTS.md` |
| 本机 | `config/local.toml`、`.secrets/`（勿提交） |
| 数据 | `data/runs/YYYY-MM-DD/gen-*/`、`data/tmp/`、`index/catalog.jsonl`、`cache/` |
| 外部 | `<comfy_root>/output/MyMusic/` |

过程 JSON（`manifest.json` / `qc.json`）只出现在 `data/runs/...`，不要堆在仓根。

---

## 6. 写歌词

1. 先 `target`，正文音节落进放行区间（偏稀 → 模型用拖腔填空）。
2. **不要**写 `[intro]` / Instrumental 前奏 / tags。
3. 段落用 `[verse]` `[chorus]` 等英文结构标记。
4. 英文低音吟唱用 `--chill-vocal`（自动 sparse）。

---

## 7. 失败速查

| 现象 | 动作 |
|------|------|
| 无 local.toml | **问用户**路径后创建并填写 |
| doctor FAIL 服务 | 请用户启动 ComfyUI；核对 server |
| doctor FAIL 模型 | **问**模型是否在该 comfy_root；对照 README 文件名 |
| check 密度失败 | 改歌词到 target 区间 |
| gen 提交失败 | `selftest`；必要时 `--dry-run` |
| qc voice_nn=null | 补 `--baseline` |
| qc FAIL | 修歌/重 gen，**不**改阈值；反复失败则**问用户**听哪一版 |
| tmp 太肥 | `prune --days 7`（不动 runs/音频） |
| CLI 像坏了 | `selftest`；仍异常则**问用户**并贴 log |
| 要 push | **问**推 `gitea` 还是 GitHub；勿推 secrets/archive |

```powershell
.\.venv\Scripts\python.exe songforge.py selftest
.\.venv\Scripts\python.exe songforge.py prune --days 7 --dry-run
```

冒烟歌词：`input/lyrics/pop_灯还亮着.txt`。

---

## 8. 验收清单

```text
[ ] 已读 AGENTS.md；不确定处已向用户提问
[ ] local.toml 有 comfy_root + server
[ ] doctor 全绿
[ ] check OK
[ ] gen 产出 flac，时长 150–200
[ ] qc --baseline → [PASS]
[ ] 未改阈值、未换模型、未提交 secrets
```

---

## 9. 文档索引

| 文件 | 给谁 |
|------|------|
| **AGENTS.md（本文）** | Agent：流程、铁律、**何时发问** |
| [`README.md`](README.md) | 人类：clone / 安装 / 远程 |
| [`docs/使用手册.md`](docs/使用手册.md) | 人类：命令与配置详解 |
| [`config/local.toml.example`](config/local.toml.example) | 本机连接模板 |
| [`config/default.toml`](config/default.toml) | 算法单一事实来源 |

远程：https://git.nnrc.ren/swl/songforge · https://github.com/lsuen/songforge
