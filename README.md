# songforge

本地 ACE-Step 生歌 CLI：锁定模型与声线 LoRA、铁律前奏注入、歌词密度硬校验、QC 验收。

**独立项目**：clone 到任意目录，写好 `config/local.toml` 指向本机 ComfyUI，即可生成。

| 文档 | 给谁 |
|------|------|
| [`AGENTS.md`](AGENTS.md) | Agent / 自动化 |
| [`docs/使用手册.md`](docs/使用手册.md) | 人类操作 |

## 换目录也能用（推荐）

```powershell
# 1) clone（GitHub 或 Gitea）
cd D:\CodeFiles\ProProjects
git clone https://git.nnrc.ren/swl/songforge.git
cd songforge

# 2) CLI 环境（Python >= 3.11）
.\scripts\setup_venv.ps1

# 3) 本机 ComfyUI 连接（只改这一份）
copy config\local.toml.example config\local.toml
# 编辑：
#   [engine] server = "http://127.0.0.1:8188"
#   [paths]  comfy_root / qc_python / music_dir

# 4) 先起 ComfyUI，再体检
.\.venv\Scripts\python.exe songforge.py doctor
```

只要 ComfyUI 在跑、`local.toml` 里 `comfy_root` 指向正确安装根（含 `models/checkpoints` 与 LoRA），**不必**把本仓放进 ComfyUI 的 `user/default/workflows`。

## 两个 Python 环境

| 环境 | 用途 |
|------|------|
| **本仓 `.venv`** | CLI：`doctor` / `check` / `gen` / `catalog` |
| **`qc_python`** | QC 音频栈（可继续用 ComfyUI 的 venv） |

## 配置：TOML vs JSON

| 文件 | 作用 |
|------|------|
| `config/default.toml` | 算法 / 相对路径（相对**本仓根**） |
| `config/local.toml` | 本机 Comfy 根、服务地址、QC Python（gitignore） |
| `workflows/template.json` | 包内 workflow 图（JSON，与配置分流） |
| `config/baseline/*.json` | QC 基线数据 |

优先级：`命令行 > SONGFORGE_* > local.toml > default.toml > DEFAULTS`

## 流水线

```powershell
.\.venv\Scripts\python.exe songforge.py target --style pop --bpm 104 --dur 180
.\.venv\Scripts\python.exe songforge.py check --style pop --title 歌名 --lyrics-file input\lyrics\歌名.txt
.\.venv\Scripts\python.exe songforge.py gen   --style pop --title 歌名 --lyrics-file input\lyrics\歌名.txt
.\.venv\Scripts\python.exe songforge.py qc    --run gen-<时间戳> --baseline
```

`qc` 必须带 `--baseline`。音频写到 ComfyUI `output/MyMusic/`（可用 `music_dir` 覆盖）。

## 硬约束（摘要）

| 项 | 值 |
|----|-----|
| 模型 | `ace_step_1.5_turbo_swl_fused` + `swl_voice_best_ep257`（0.5/0.5） |
| 采样 | shift=5.5 / steps=28 / cfg=2.0 / temperature=0.68 |
| 时长 | 完整歌 150–200 秒 |
| 前奏 | 8 小节器乐 hook；CLI 自动注入 |

## 目录

```
songforge/                 # clone 下来的仓根 = 包根
  config/default.toml
  config/local.toml        # 本机（勿提交）
  data/runs/YYYY-MM-DD/    # gen 产物
  workflows/template.json
  .venv/
```

## 远程

- GitHub: https://github.com/lsuen/songforge  
- Gitea: https://git.nnrc.ren/swl/songforge  

## License

MIT — 见 [LICENSE](LICENSE)。
