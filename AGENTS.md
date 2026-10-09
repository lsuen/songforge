# songforge — Agent 生歌手册

> 远程 / channel Agent 读这份文件即可做歌。上下文不够时**重新打开本文件**。
> 人类规范见 ComfyUI 仓内 `生歌规则.md`（若仍嵌套部署）；行为冲突以 `config/default.toml` 为准。

## 0. 交付物

1. 一次 `gen` 的 `run_id`
2. ComfyUI 产出的 flac
3. `qc --baseline` → **PASS**

`style_fam` 只是弱参考，不要当 PASS 条件。

## 1. 工作目录

**推荐（独立仓）：** 在 clone 根目录执行：

```powershell
cd D:\CodeFiles\ProProjects\songforge   # 或任意 clone 路径
.\.venv\Scripts\python.exe songforge.py <命令>
```

**嵌套在 ComfyUI 时**也可：

```powershell
cd <ComfyUI>\user\default\workflows
python songforge\songforge.py <命令>
```

禁止：手写 tags / 手写前奏 / 改 workflow JSON 调参 / 换 YuE2·Music3·aio / 放宽 QC 阈值。

## 2. 流水线

```text
doctor → target → 写歌词 → check → gen → qc --baseline
```

- `check` 不过 → **只改歌词**
- `doctor` 服务不可达 → 先起 ComfyUI；检查 `config/local.toml` 的 `engine.server` / `paths.comfy_root`
- `qc` 必须带 `--baseline`
- 索引：`catalog` / `catalog --rebuild`

## 3. 硬锁定

| 项 | 值 |
|----|-----|
| checkpoint | `ace_step_1.5_turbo_swl_fused.safetensors` |
| LoRA | `swl_voice_best_ep257.safetensors`（0.5 / 0.5） |
| 采样 | shift=5.5 / steps=28 / cfg=2.0 / temperature=0.68 |
| 时长 | 完整歌 **150–200** 秒 |
| 前奏 | 8 小节器乐 hook；CLI 自动注入 |

算法 → `config/default.toml`；本机路径 → `config/local.toml`（gitignore）。

```text
命令行 > SONGFORGE_* > local.toml > default.toml > DEFAULTS
```

CLI = 项目 `.venv`；QC = `local.toml` 的 `qc_python`。

## 4. 目录（相对仓根）

| 级别 | 路径 |
|------|------|
| 项目 | `input/lyrics/`、`config/baseline/`、`workflows/template.json` |
| 本机 | `config/local.toml` |
| 数据 | `data/runs/YYYY-MM-DD/`、`data/tmp/`、`index/catalog.jsonl`、`cache/` |
| 外部 | `<comfy_root>/output/MyMusic/` |

## 5. 写歌词

1. 先 `target`，音节进放行区间。
2. 不要写 `[intro]` / tags——`assemble` 会注入。
3. 段落用 `[verse]` `[chorus]` 等。
4. 英文低音吟唱用 `--chill-vocal`。

## 6. 失败速查

| 现象 | 动作 |
|------|------|
| doctor FAIL 服务 | 启动 ComfyUI；核对 local.toml server |
| doctor FAIL 模型 | comfy_root 下是否有锁定 checkpoint/LoRA |
| check 密度失败 | 改歌词 |
| qc voice_nn=null | 漏了 `--baseline` |
| qc FAIL | 修歌/重 gen，不改阈值 |

```powershell
.\.venv\Scripts\python.exe songforge.py selftest
```

## 7. 验收清单

```text
[ ] local.toml 已填 comfy_root / server
[ ] doctor 全绿
[ ] check OK
[ ] gen 产出 flac，时长 150–200
[ ] qc --baseline → PASS
```
