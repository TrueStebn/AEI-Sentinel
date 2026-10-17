# Owner-operated credential prompt. No password is saved or put on a command line.
$ErrorActionPreference = 'Stop'
$root = 'C:\ProgramData\AEI-Sentinel-Demo'
$app = Join-Path $root 'app'
$python = Join-Path $root 'python\python.exe'
$marker = Get-Content -LiteralPath (Join-Path $root 'isolation.json') -Raw | ConvertFrom-Json
$user = Get-LocalUser -Name AEIDemo
if (-not $user.Enabled -or $user.SID.Value -ne $marker.sid) { throw 'Isolated account identity mismatch.' }
if (Get-NetTCPConnection -LocalPort 8515 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8515 is already occupied. No second server is started.' }
if (Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue | Where-Object LocalAddress -NotIn @('127.0.0.1','::1')) { throw 'Ollama is not restricted to loopback.' }
$accountName = $user.SID.Translate([Security.Principal.NTAccount]).Value
$credential = Get-Credential -UserName $accountName -Message 'Enter the password created for AEIDemo during installation. Check the keyboard layout and Caps Lock. No public tunnel will start.'
if (-not $credential) { throw 'Owner cancelled credential entry.' }
if ($credential.UserName -ne $accountName) { throw 'Use the exact isolated Windows account displayed in the prompt.' }
$setup = '$env:USERPROFILE=[Environment]::GetFolderPath(''UserProfile'');$env:APPDATA=[Environment]::GetFolderPath(''ApplicationData'');$env:LOCALAPPDATA=[Environment]::GetFolderPath(''LocalApplicationData'');Remove-Module PSReadLine -ErrorAction SilentlyContinue;'
$command = $setup + "& '$python' -B -m src.windows_job --isolated"
$shell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
# The visible console keeps startup errors available for owner review.
try {
    $console = Start-Process -FilePath $shell -WorkingDirectory $app -Credential $credential -LoadUserProfile -WindowStyle Normal -ArgumentList @('-NoProfile','-NoExit','-Command',$command) -PassThru
} finally {
    $credential = $null
}
Write-Output "Isolated local console opened. Console PID: $($console.Id). Keep it open. No public tunnel was started."
@{format=1; purpose='aei-owned-isolated-console'; console_pid=$console.Id; account_sid=$marker.sid} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'owner-launch.json') -Encoding utf8
