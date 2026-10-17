param(
    [Parameter(Mandatory = $true)][string]$Python,
    [ValidateRange(1024,65535)][int]$Port = 8515,
    [string]$Origin = ''
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Supply the installed Python executable with project dependencies.' }
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop)
if (@($listeners | Where-Object { $_.LocalPort -eq 11434 -and $_.LocalAddress -notin @('127.0.0.1','::1') }).Count -gt 0) {
    throw 'Ollama is listening beyond loopback. Stop and correct OLLAMA_HOST before starting the demo.'
}
if (@($listeners | Where-Object { $_.LocalPort -eq $Port }).Count -gt 0) { throw 'The selected application port is already in use.' }
if (@($listeners | Where-Object { $_.LocalPort -eq 11434 }).Count -eq 0) {
    Write-Warning 'Ollama is unavailable. The app will still offer Manual Search. No service or weights will be installed.'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    $demoArgs = @('-m','tools.run_public_demo','--port',"$Port")
    if ($Origin) { $demoArgs += @('--origin',$Origin) }
    & $Python @demoArgs
    if ($LASTEXITCODE -ne 0) { throw 'Public demo stopped with an error. Inspect the local console; do not publish it.' }
} finally {
    Pop-Location
}
