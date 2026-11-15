# SPDX-License-Identifier: AGPL-3.0-only
param(
    [string]$PhotoDirectory,
    [int]$Port = 5173
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$node = if ($nodeCommand) { $nodeCommand.Source } else { Join-Path $root '.tools\node-v24.11.1-win-x64\node.exe' }
if (!(Test-Path -LiteralPath $node)) { throw '需要 Node.js 22.12+；本机已准备的 .tools 运行时也可使用。' }
if (!(Test-Path -LiteralPath 'node_modules\vite\bin\vite.js')) { throw '依赖未准备，请先运行 npm ci。' }
if (!(Test-Path -LiteralPath 'public\culling\models\bird-yolo11s-seg.onnx')) { throw '分割模型未准备，请先运行 scripts\prepare-culling.py。' }
if ($PhotoDirectory) {
    if (!(Test-Path -LiteralPath $PhotoDirectory -PathType Container)) { throw '照片目录不存在。' }
    $python = Join-Path $root '.tools\venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $python)) { $python = Join-Path $root '.venv\Scripts\python.exe' }
    if (!(Test-Path -LiteralPath $python)) { throw '需要已安装 Pillow 的 Python 虚拟环境来只读扫描 EXIF。' }
    & $python scripts\prepare-local-photos.py $PhotoDirectory
    if ($LASTEXITCODE -ne 0) { throw '本地照片清单准备失败，服务未启动。' }
    $env:BIRDSHOT_PHOTO_DIR = (Resolve-Path -LiteralPath $PhotoDirectory).Path
}
Write-Host "BirdShot：http://127.0.0.1:$Port/cull（仅本机，原片只读）"
& $node node_modules\vite\bin\vite.js --host 127.0.0.1 --port $Port --strictPort
exit $LASTEXITCODE
