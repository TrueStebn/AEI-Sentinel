# Owner-operated token entry. Never paste the token into chat or a command line.
param([Parameter(Mandatory=$true)][string]$Agent)
$ErrorActionPreference = 'Stop'
$root = 'C:\ProgramData\AEI-Sentinel-Demo'
$target = Join-Path $root 'ngrok'
 $source = (Resolve-Path -LiteralPath $Agent).Path
$digest = 'D339BCBD0713233337E860163F5249EEA679CF26750A5700510DBC241D201748'
if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $digest) { throw 'The reviewed ngrok binary changed.' }
if (Test-Path -LiteralPath $target) { throw 'The private agent directory already exists. Stop and inspect it; credentials are never overwritten.' }
$user = Get-LocalUser -Name AEIDemo
$marker = Get-Content -LiteralPath (Join-Path $root 'isolation.json') -Raw | ConvertFrom-Json
if ($marker.sid -ne $user.SID.Value) { throw 'Isolation identity mismatch.' }
$acl = Get-Acl -LiteralPath $root
$allowed = @('S-1-5-18','S-1-5-32-544',[Security.Principal.WindowsIdentity]::GetCurrent().User.Value,$user.SID.Value)
if (-not $acl.AreAccessRulesProtected) { throw 'The deployment root is not protected.' }
foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
    if ($rule.IdentityReference.Value -notin $allowed -or $rule.AccessControlType -ne 'Allow') { throw 'Unexpected access to the deployment root.' }
}
$secret = Read-Host 'Paste the ngrok authtoken from your own dashboard here (hidden input)' -AsSecureString
$pointer = [IntPtr]::Zero
$plain = $null
try {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ($plain -notmatch '^[A-Za-z0-9_./+\-]{20,200}$') { throw 'Unexpected token format. Nothing was saved.' }
    New-Item -ItemType Directory -Path $target | Out-Null
    Copy-Item -LiteralPath $source -Destination (Join-Path $target 'ngrok.exe')
    # Inherits the protected root: owner/SYSTEM/Admin full, AEIDemo read only.
    # The agent runs as AEIDemo; it does not receive access to the owner profile.
    $config = "version: 3`nagent:`n  authtoken: '$plain'`n  web_addr: false`n  console_ui: false`n  remote_management: false`n  update_check: false`n  inspect_db_size: -1`n  log: false`n  log_level: error`n"
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($config)
    $stream = [IO.File]::Open((Join-Path $target 'ngrok.yml'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $stream.Write($bytes,0,$bytes.Length) } finally { $stream.Dispose() }
    $config = $null
    [Array]::Clear($bytes,0,$bytes.Length)
} finally {
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    $plain = $null
    if ($secret) { $secret.Dispose() }
}
$checkOutput = & (Join-Path $target 'ngrok.exe') config check --config (Join-Path $target 'ngrok.yml') 2>&1
$valid = $LASTEXITCODE -eq 0
$checkOutput = $null # Never echo credential-bearing configuration or diagnostic text.
if (-not $valid) { throw 'Private ngrok configuration failed validation. No tunnel was started; inspect privately.' }
@{format=1; config_valid=$true; binary_sha256=$digest; origin='https://secular-bundle-uncapped.ngrok-free.dev'; endpoint_started=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $target 'owner-check.json') -Encoding utf8
Write-Output 'Private ngrok configuration prepared. No agent connection or public endpoint was started.'

