# Default is a check only. This script does not grant public-exposure approval.
param([switch]$ApprovedPublicExposure)
$ErrorActionPreference = 'Stop'
$root = 'C:\ProgramData\AEI-Sentinel-Demo'
$user = Get-LocalUser -Name AEIDemo
$marker = Get-Content -LiteralPath (Join-Path $root 'isolation.json') -Raw | ConvertFrom-Json
if ($marker.sid -ne $user.SID.Value -or -not $user.Enabled) { throw 'Isolation identity mismatch.' }
$mode = '--check-only'
if ($ApprovedPublicExposure) {
    if ((Read-Host 'Public exposure needs explicit final owner approval. Type OPEN to start the fixed endpoint') -cne 'OPEN') { throw 'Public exposure was not confirmed.' }
    $mode = '--approved-public-exposure'
}
$name = $user.SID.Translate([Security.Principal.NTAccount]).Value
$credential = Get-Credential -UserName $name -Message 'Enter AEIDemo credentials privately. Default operation checks only and never opens an endpoint.'
if (-not $credential -or $credential.UserName -ne $name) { throw 'Use the displayed isolated Windows account.' }
$python = Join-Path $root 'python\python.exe'
$setup = '$env:USERPROFILE=[Environment]::GetFolderPath(''UserProfile'');$env:APPDATA=[Environment]::GetFolderPath(''ApplicationData'');$env:LOCALAPPDATA=[Environment]::GetFolderPath(''LocalApplicationData'');Remove-Module PSReadLine -ErrorAction SilentlyContinue;'
$command = $setup + "& '$python' -B -m tools.run_demo_tunnel $mode"
try {
    $shell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $console = Start-Process -FilePath $shell -WorkingDirectory (Join-Path $root 'app') -Credential $credential -LoadUserProfile -WindowStyle Normal -ArgumentList @('-NoProfile','-NoExit','-Command',$command) -PassThru
} finally { $credential = $null }
@{format=1;purpose='aei-owned-agent-console';console_pid=$console.Id;mode=$mode} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'owner-agent-launch.json') -Encoding utf8
Write-Output "Isolated agent console opened. Mode: $mode. Console PID: $($console.Id)."
