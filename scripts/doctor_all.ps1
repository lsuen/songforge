# One-shot setup check: venv -> local.toml -> doctor -> next steps
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "== songforge doctor_all =="
Write-Host "pkg: $Root"

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "No .venv — running setup_venv.ps1 ..."
  & powershell -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\setup_venv.ps1")
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$local = Join-Path $Root "config\local.toml"
if (-not (Test-Path $local)) {
  $ex = Join-Path $Root "config\local.toml.example"
  Copy-Item $ex $local
  Write-Host ""
  Write-Host "CREATED config\local.toml from example."
  Write-Host "Edit comfy_root / server / qc_python, start ComfyUI, then re-run:"
  Write-Host "  .\scripts\doctor_all.ps1"
  exit 2
}

Write-Host "Using: $py"
& $py (Join-Path $Root "songforge.py") doctor @args
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
  Write-Host "NEXT:"
  Write-Host "  .\.venv\Scripts\python.exe songforge.py target --style pop --bpm 104 --dur 180"
  Write-Host "  .\.venv\Scripts\python.exe songforge.py check --style pop --title demo --lyrics-file input\lyrics\pop_灯还亮着.txt"
  Write-Host "  .\.venv\Scripts\python.exe songforge.py gen   --style pop --title demo --lyrics-file input\lyrics\pop_灯还亮着.txt"
} else {
  Write-Host "doctor FAILED (exit $code)."
  Write-Host "Common fixes:"
  Write-Host "  - Start ComfyUI (default http://127.0.0.1:8188)"
  Write-Host "  - Fix config\local.toml paths.comfy_root"
  Write-Host "  - Put locked checkpoint + LoRA under <comfy_root>\models\ (see README)"
  Write-Host "  - Ask the user if paths are unknown (Agents: see AGENTS.md)"
}
exit $code
