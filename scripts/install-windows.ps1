# Requires uv on PATH. No administrator rights or execution-policy changes needed.
[CmdletBinding()]
param(
    [string]$PymolPython,
    [string]$Blender,
    [string]$StartupFile,
    [switch]$SkipRuntime,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$controller = Join-Path $projectRoot '.venv\Scripts\python.exe'
Push-Location $projectRoot
try {
    if (-not $Uninstall) {
        $uv = Get-Command uv -ErrorAction Stop
        & $uv.Source sync --locked --inexact
        if ($LASTEXITCODE -ne 0) { throw 'Controller installation failed.' }
    }
    if (-not (Test-Path -LiteralPath $controller)) { throw 'Controller missing. Install before uninstalling.' }
    $setupArgs = @('-X', 'utf8', 'scripts/windows_setup.py')
    if ($PymolPython) { $setupArgs += @('--pymol-python', $PymolPython) }
    if ($Blender) { $setupArgs += @('--blender', $Blender) }
    if ($StartupFile) { $setupArgs += @('--startup-file', $StartupFile) }
    if ($SkipRuntime) { $setupArgs += '--skip-runtime' }
    if ($Uninstall) { $setupArgs += '--uninstall' }
    & $controller @setupArgs
    if ($LASTEXITCODE -ne 0) { throw 'Windows setup failed; see diagnostic above.' }
} finally { Pop-Location }
