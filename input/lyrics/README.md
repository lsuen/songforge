# 歌词投放目录

新歌词 `.txt` 放这里，生成时：

```powershell
cd D:\CodeFiles\Thinking\ComfyUI-MiniMaxH3\user\default\workflows
python songforge\songforge.py check --style pop --title 歌名 --lyrics-file songforge\input\lyrics\歌名.txt
```

历史素材库在 `../_archive/lyrics_corpus/`（只读）。  
不要手写 `[intro]` / tags，CLI 会注入。
