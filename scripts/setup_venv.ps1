# Rebuild project .venv for CLI. QC can keep using ComfyUI venv via local.toml.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$PyLauncher = $null
$PyArgs = @()
foreach ($pair in @(
    @("py", @("-3.13")),
    @("py", @("-3.12")),
    @("py", @("-3.11")),
    @("python", @())
  )) {
  $exe = $pair[0]
  $a = $pair[1]
  try {
    $ver = & $exe @($a + @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")) 2>$null
    if ($ver -match '^3\.(1[1-9]|[2-9]\d)') {
      $PyLauncher = $exe
      $PyArgs = $a
      break
    }
  } catch {}
}
if (-not $PyLauncher) {
  Write-Error "Need Python >= 3.11 (try: py -3.13)"
}

Write-Host "Using: $PyLauncher $($PyArgs -join ' ')"
if (Test-Path .venv) {
  Write-Host "Removing old .venv ..."
  Remove-Item -Recurse -Force .venv
}
& $PyLauncher @($PyArgs + @("-m", "venv", ".venv"))
& .\.venv\Scripts\python.exe -m pip install -U pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host ""
Write-Host "OK: .venv ready"
Write-Host "CLI: .\.venv\Scripts\python.exe songforge.py doctor"
Write-Host "QC:  set paths.qc_python in config\local.toml"
