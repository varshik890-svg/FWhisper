# F Whisper one-click setup: downloads llama.cpp (CUDA 13.5 build for RTX 50-series) and the models in config.json.
# Run:  powershell -ExecutionPolicy Bypass -File setup.ps1        (or double-click setup.bat)
# Optional: -Only qwen3.5-2b   to download just one candidate model.
param([string]$Only = "")

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$cfg = Get-Content "$root\config.json" -Raw | ConvertFrom-Json
$build = $cfg.llama_cpp_build
$llamaDir = Join-Path $root "tools\llama"

function Get-File($url, $dest) {
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    Write-Host "Downloading $(Split-Path $dest -Leaf) ..."
    & curl.exe -L --fail --retry 3 -C - -o "$dest" "$url"
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $url" }
}

# 1) Python check (standard library only - no pip packages needed)
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "Python 3.10+ not found. Install from https://www.python.org/downloads/ and re-run." }
Write-Host "Python: $(& python --version)"

# 2) llama.cpp CUDA build
if (-not (Test-Path "$llamaDir\llama-server.exe")) {
    $base = "https://github.com/ggml-org/llama.cpp/releases/download/$build"
    $tmp = Join-Path $root "tools\_dl"
    Get-File "$base/llama-$build-bin-win-cuda-13.5-x64.zip" "$tmp\llama.zip"
    Get-File "$base/cudart-llama-bin-win-cuda-13.5-x64.zip" "$tmp\cudart.zip"
    New-Item -ItemType Directory -Force $llamaDir | Out-Null
    Expand-Archive "$tmp\llama.zip" $llamaDir -Force
    Expand-Archive "$tmp\cudart.zip" $llamaDir -Force
    # some releases nest files in a subfolder - flatten it
    $nested = Get-ChildItem $llamaDir -Recurse -Filter llama-server.exe | Select-Object -First 1
    if ($nested -and $nested.DirectoryName -ne $llamaDir) {
        Get-ChildItem $nested.DirectoryName | Move-Item -Destination $llamaDir -Force
    }
    Remove-Item $tmp -Recurse -Force
} else { Write-Host "llama.cpp already installed." }

# 3) Models
foreach ($m in $cfg.candidate_models) {
    if ($Only -and $m.name -ne $Only) { continue }
    if (-not $m.url) { Write-Host "Skipping $($m.name) (local file, no download)"; continue }
    $dest = if ([IO.Path]::IsPathRooted($m.file)) { $m.file } else { Join-Path $root $m.file }
    if (Test-Path $dest) { Write-Host "$($m.name) already downloaded."; continue }
    Get-File $m.url $dest
}

Write-Host "`nSetup complete. Start with start.bat, then configure Handy (see README)."
