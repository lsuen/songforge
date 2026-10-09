# songforge

本地 ACE-Step 生歌 CLI：锁定模型与声线 LoRA、铁律前奏注入、歌词密度硬校验、QC 验收。

**独立项目**：clone 到任意目录，写好 `config/local.toml` 指向本机 ComfyUI，即可生成。

| 文档 | 给谁 | 何时读 |
|------|------|--------|
| **[`AGENTS.md`](AGENTS.md)** | **Agent / Cursor / 自动化** | **做歌或改仓前必读**（含「遇事先问」） |
| [`docs/使用手册.md`](docs/使用手册.md) | 人类 | 命令与配置详解 |
| [`config/local.toml.example`](config/local.toml.example) | 所有人 | 第一次连本机 ComfyUI |

> 给 Agent：读完 `AGENTS.md` 再动手。缺 `local.toml`、Comfy 没起、风格/歌名不清、想改模型或阈值时——**先问用户，不要猜**。

## 一键体检

```powershell
.\scripts\doctor_all.ps1
```

会：缺 `.venv` 则创建 → 缺 `local.toml` 则从 example 复制并提示编辑 → 跑 `doctor` → 打印下一步。

## 换目录也能用（推荐）

```powershell
cd D:\CodeFiles\ProProjects
git clone https://git.nnrc.ren/swl/songforge.git
# 或：git clone https://github.com/lsuen/songforge.git
cd songforge

.\scripts\doctor_all.ps1
# 按提示编辑 config\local.toml 后，启动 ComfyUI，再跑一次 doctor_all
```

不必把本仓放进 ComfyUI 的 `user/default/workflows`。

## 需要的模型（ComfyUI 侧）

在 `local.toml` 的 `comfy_root` 下准备：

| 类型 | 文件名 | 目录 |
|------|--------|------|
| Checkpoint | `ace_step_1.5_turbo_swl_fused.safetensors` | `models/checkpoints/` |
| LoRA | `swl_voice_best_ep257.safetensors` | `models/loras/` |

- 默认 LoRA 是**作者定制声线**；别人没有同文件时 `doctor` 会 FAIL。
- 可换成自己的 LoRA，并改 `config/default.toml` 的 `[model]`（声线会变；不要用禁用名单里的 YuE2/aio）。
- 缺文件时 `doctor` 会打印人话排查步骤。

## 两个 Python 环境

| 环境 | 用途 |
|------|------|
| **本仓 `.venv`** | CLI：`doctor` / `check` / `gen` / `catalog` / `prune` |
| **`qc_python`** | QC 音频栈（可继续用 ComfyUI 的 venv） |

## 配置：TOML vs JSON

| 文件 | 作用 |
|------|------|
| `config/default.toml` | 算法 / 相对路径（相对**本仓根**） |
| `config/local.toml` | 本机 Comfy 根、服务地址、QC Python（**勿提交**） |
| `workflows/template.json` | workflow 图（JSON） |
| `config/baseline/*.json` | QC 基线数据 |
| `.secrets/` | 远程 token（**勿提交**） |

优先级：`命令行 > SONGFORGE_* > local.toml > default.toml > DEFAULTS`

## 流水线

```powershell
.\.venv\Scripts\python.exe songforge.py target --style pop --bpm 104 --dur 180
.\.venv\Scripts\python.exe songforge.py check --style pop --title 灯还亮着 --lyrics-file input\lyrics\pop_灯还亮着.txt
.\.venv\Scripts\python.exe songforge.py gen   --style pop --title 灯还亮着 --lyrics-file input\lyrics\pop_灯还亮着.txt
.\.venv\Scripts\python.exe songforge.py qc    --run gen-<时间戳> --baseline
.\.venv\Scripts\python.exe songforge.py prune --days 7          # 清过期 tmp
.\.venv\Scripts\python.exe songforge.py prune --days 7 --dry-run
```

`qc` 必须带 `--baseline`。样例歌词见 `input/lyrics/`。Agent 规则见 [`AGENTS.md`](AGENTS.md)。

## 硬约束（摘要）

| 项 | 值 |
|----|-----|
| 模型 | 见上表（0.5/0.5 LoRA） |
| 采样 | shift=5.5 / steps=28 / cfg=2.0 / temperature=0.68 |
| 时长 | 完整歌 150–200 秒 |
| 前奏 | 8 小节器乐 hook；CLI 自动注入 |

## 远程（双 remote）

| 名 | URL | 用途 |
|----|-----|------|
| `gitea` | https://git.nnrc.ren/swl/songforge | 常用推送 |
| `origin` / GitHub | https://github.com/lsuen/songforge | 公开镜像 |

```powershell
git remote -v
git push gitea main
git push origin main    # 或经 ghfast
```

Agent 推送前先问用户推哪个 remote；**不要**推 `archive/*`、`.secrets`、`local.toml`。

## License

MIT — 见 [LICENSE](LICENSE)。
