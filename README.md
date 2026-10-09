# songforge

本地创作者用的 AI 歌曲生成 CLI。固定模型 + 铁律注入 + 密度硬校验 + QC 验收，稳定产出带专属声线的完整歌曲。

本目录是**独立 Git 仓**：

- `main`：只跟踪项目资产（代码 / `config/` / 基线 / 文档）
- `archive/full-snapshot`：本地备份过程数据（`data/`、`index/catalog.jsonl`、`_archive/`）；**勿默认 push**
- `cache/` 可重建（约数 GB），不进 archive

| 文档 | 给谁 |
|------|------|
| [`AGENTS.md`](AGENTS.md) | **远程 / channel Agent**（上下文不够时重读这份做歌） |
| [`../生歌规则.md`](../生歌规则.md) | 参数/铁律/踩坑唯一规范 |
| [`docs/使用手册.md`](docs/使用手册.md) | 人类操作手册 |

## 30 秒上手

工作目录必须是 `user/default/workflows`：

```powershell
cd D:\CodeFiles\Thinking\ComfyUI-MiniMaxH3\user\default\workflows

python songforge\songforge.py doctor
python songforge\songforge.py target --style pop --bpm 104 --dur 180
python songforge\songforge.py check --style pop --title 歌名 --lyrics-file songforge\input\lyrics\歌名.txt
python songforge\songforge.py gen   --style pop --title 歌名 --lyrics-file songforge\input\lyrics\歌名.txt
python songforge\songforge.py qc    --run gen-<时间戳> --baseline
python songforge\songforge.py catalog --limit 20
```

`qc` 必须带 `--baseline`。首次本机复制 `config/local.json.example` → `config/local.json` 填 Comfy 根与 QC Python。

## 硬约束（摘要）

| 项 | 值 |
|----|-----|
| 模型 | `ace_step_1.5_turbo_swl_fused` + `swl_voice_best_ep257`（0.5/0.5） |
| 采样 | shift=5.5 / steps=28 / cfg=2.0 / temperature=0.68 |
| 时长 | 完整歌 150–200 秒 |
| 前奏 | 8 小节器乐 hook；CLI 自动注入 |
| 配置 | 算法锁 `config/default.json`；本机路径 `config/local.json` |

## 项目资产 vs 过程数据

| 级别 | 路径 | 是否进 `main` |
|------|------|----------------|
| 项目 | `cli/` `core/` `config/*.json`（除 local） `config/baseline/` `workflows/` `docs/` | 是 |
| 本机 | `config/local.json` | 否（gitignore） |
| 过程 | `data/runs/YYYY-MM-DD/gen-*/` `data/tmp/` | 否 |
| 索引 | `index/catalog.jsonl` | 否（可 `--rebuild`） |
| 缓存 | `cache/qc` `cache/style` | 否 |
| 音频 | ComfyUI `output/MyMusic/` | 不在包内 |

```
songforge/
  AGENTS.md / README.md / songforge.py
  config/                 # default + styles + lexicon + local.example
    baseline/             # qc_baseline*.json + style_ref.json
    local.json            # 本机路径（勿提交）
  cli/ core/ rules/ lyrics/
  input/lyrics/           # 新歌词投放
  data/runs/YYYY-MM-DD/   # gen 正式产物
  data/tmp/YYYY-MM-DD/    # doctor/check 会话
  index/catalog.jsonl     # 全局元数据索引
  cache/                  # QC / 风格特征
  docs/ _archive/
```

## 配置优先级

```
命令行 > SONGFORGE_* 环境变量 > config/local.json > config/default.json > DEFAULTS
```

模型与采样铁律只在 `default.json`，不要放进 local。

## 日志

- `gen` → `data/runs/<日>/<run_id>/run.log`（JSONL）
- `doctor` / `check` → `data/tmp/<日>/<cmd>-*/run.log`
- `--log-level` / `--quiet` 全局生效
