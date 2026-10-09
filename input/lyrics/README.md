# 歌词投放目录

新歌词 `.txt` 放这里。仓内样例（可直接 `check` / 冒烟）：

| 文件 | 风格建议 |
|------|----------|
| `pop_灯还亮着.txt` | `--style pop` |
| `hiphop_楼下那条街.txt` | `--style urbanhiphop` 或 hiphop 系 |
| `punk_去它妈的班.txt` | `--style punk` |

```powershell
.\.venv\Scripts\python.exe songforge.py check --style pop --title 灯还亮着 --lyrics-file input\lyrics\pop_灯还亮着.txt --bpm 104 --dur 180
```

不要手写 `[intro]` / tags，CLI 会注入。
