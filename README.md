# songforge

本地 ACE-Step 生歌 CLI：锁定模型与声线 LoRA、铁律前奏注入、歌词密度硬校验、QC 验收。

自己用爽是第一目标；代码按 MIT 开源，方便别人在 ComfyUI + ACE-Step 上复用同一套流水线。

| 文档 | 给谁 |
|------|------|
| [`AGENTS.md`](AGENTS.md) | Agent / 自动化（做歌时重读） |
| [`docs/使用手册.md`](docs/使用手册.md) | 人类操作 |

## 两个 Python 环境（别混）

| 环境 | 用途 | 怎么来 |
|------|------|--------|
| **项目 `.venv`** | CLI：`doctor` / `target` / `check` / `gen` / `catalog` | `scripts\setup_venv.ps1` |
| **`qc_python`** | QC 音频栈（librosa / torch / demucs） | `config/local.toml` 指向 ComfyUI venv，或自建重环境 |

```powershell
# 1) 建 CLI 环境（推荐 Python 3.13 / ≥3.11）
cd path\to\songforge
.\scripts\setup_venv.ps1

# 2) 本机路径
copy config\local.toml.example config\local.toml
# 编辑 comfy_root / qc_python / music_dir

# 3) 工作目录：包的父目录（文件夹名必须是 songforge）
cd ..
.\songforge\.venv\Scripts\python.exe songforge\songforge.py doctor
```

嵌套在 ComfyUI 时父目录就是 `user\default\workflows`。

## 配置：TOML vs JSON

| 文件 | 格式 | 作用 |
|------|------|------|
| `config/default.toml` | **TOML** | 算法 / 采样 / 相对路径（可提交） |
| `config/local.toml` | **TOML** | 本机绝对路径（gitignore） |
| `config/styles.json` `lexicon.json` | JSON | 风格字典 / 禁词（数据） |
| `config/baseline/*.json` | JSON | QC 基线数值 |
| `workflows/*.json` 与 Comfy 模板 | JSON | **只有** workflow 图用 JSON |

优先级：`命令行 > SONGFORGE_* > local.toml > default.toml > DEFAULTS`  
（仍可读旧的 `local.json` / `default.json`，同名时 TOML 覆盖。）

## 30 秒流水线

```powershell
python songforge\songforge.py doctor
python songforge\songforge.py target --style pop --bpm 104 --dur 180
python songforge\songforge.py check --style pop --title 歌名 --lyrics-file songforge\input\lyrics\歌名.txt
python songforge\songforge.py gen   --style pop --title 歌名 --lyrics-file songforge\input\lyrics\歌名.txt
python songforge\songforge.py qc    --run gen-<时间戳> --baseline
python songforge\songforge.py catalog --limit 20
```

`qc` 必须带 `--baseline`。

## 硬约束（摘要）

| 项 | 值 |
|----|-----|
| 模型 | `ace_step_1.5_turbo_swl_fused` + `swl_voice_best_ep257`（0.5/0.5） |
| 采样 | shift=5.5 / steps=28 / cfg=2.0 / temperature=0.68 |
| 时长 | 完整歌 150–200 秒 |
| 前奏 | 8 小节器乐 hook；CLI 自动注入 |

## 目录（项目 vs 数据）

```
songforge/                 # Git 仓根 = 包根
  config/default.toml      # 算法配置
  config/local.toml        # 本机（勿提交）
  config/baseline/         # QC 基线
  cli/ core/ rules/ ...
  data/runs/YYYY-MM-DD/    # gen 产物
  data/tmp/YYYY-MM-DD/     # doctor/check
  index/catalog.jsonl
  workflows/template.json  # 包内备用模板
  .venv/                   # CLI 专用（勿提交）
  .secrets/                # 本地 token（勿提交）
```

音频成品仍在 ComfyUI `output/MyMusic/`（可用 `music_dir` 覆盖）。

## Git

- `main`：项目资产  
- `archive/full-snapshot`：本地过程数据备份，**不要 push**  
- `.secrets/`、`config/local.toml`、`.venv/`、`data/**` 均已 ignore  

## License

MIT — 见 [LICENSE](LICENSE)。
