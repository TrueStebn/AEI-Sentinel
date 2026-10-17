# Run only after owner approval, from an elevated PowerShell window.
# Creates one standard local account and one new deployment directory. No services.
param(
    [Parameter(Mandatory=$true)][string]$PublicationManifest,
    [string]$Source = 'C:\Users\Denis\PycharmProjects\JunctionX\AEI-Sentinel',
    [string]$BuildPython = 'C:\Users\Denis\PycharmProjects\JunctionX\.venv\Scripts\python.exe',
    [switch]$Resume,
    [string]$PreviousPublicationManifest
)
$ErrorActionPreference = 'Stop'
$destination = 'C:\ProgramData\AEI-Sentinel-Demo'
$account = 'AEIDemo'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run in an elevated PowerShell window after owner approval.' }
if (Get-LocalUser -Name $account -ErrorAction SilentlyContinue) { throw 'The account already exists. Stop and inspect it; no account is overwritten.' }
if (Test-Path -LiteralPath (Join-Path $destination 'isolation.json')) { throw 'An isolation marker already exists. Do not repeat installation.' }
if ($Resume) {
    if (-not (Test-Path -LiteralPath $destination -PathType Container) -or -not $PreviousPublicationManifest) { throw 'Resume requires an existing partial copy and its previous publication manifest.' }
} elseif (Test-Path -LiteralPath $destination) { throw 'Deployment directory already exists. Inspect it and use explicit -Resume with the previous manifest.' }
$Source = (Resolve-Path -LiteralPath $Source).Path
$manifest = Get-Content -LiteralPath $PublicationManifest -Raw | ConvertFrom-Json
if ($manifest.format -ne 1 -or @($manifest.files).Count -lt 104) { throw 'Invalid reviewed publication manifest.' }
foreach ($item in $manifest.files) {
    if ($item.path -notmatch '^[A-Za-z0-9_.\-/]+$' -or $item.path -match '(^|/)\.\.(/|$)|(^|/)(work|outputs|\.git|\.venv|__pycache__)(/|$)|\.(db|sqlite|gguf|pem|key|log)$') { throw 'Unsafe publication path.' }
    $candidate = [IO.Path]::GetFullPath((Join-Path $Source $item.path))
    if (-not $candidate.StartsWith($Source + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Publication path escaped source.' }
    if ((Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash -ne $item.sha256) { throw "Source changed: $($item.path)" }
}
$runtime = (& $BuildPython -c 'import sys;print(sys.base_prefix)').Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $runtime 'python.exe'))) { throw 'Installed Python runtime was not found.' }
$assets = Join-Path $Source 'data\runtime\public-demo'
$assetManifest = Get-Content -LiteralPath (Join-Path $assets 'manifest.json') -Raw | ConvertFrom-Json
$assetNames = @('laboratory.db','scenario-0.db','scenario-1.db','scenario-2.db','scenario-3.db')
foreach ($name in $assetNames) {
    if ((Get-FileHash -LiteralPath (Join-Path $assets $name) -Algorithm SHA256).Hash -ne $assetManifest.files.$name.sha256) { throw 'Snapshot checksum mismatch.' }
}
if ($Resume) {
    $previous = Get-Content -LiteralPath $PreviousPublicationManifest -Raw | ConvertFrom-Json
    if ($previous.format -ne 1 -or @($previous.files).Count -lt 104) { throw 'Invalid previous publication manifest.' }
    $copiedApp = Join-Path $destination 'app'
    foreach ($item in $previous.files) {
        if ($item.path -notmatch '^[A-Za-z0-9_.\-/]+$' -or $item.path -match '(^|/)\.\.(/|$)') { throw 'Unsafe previous publication path.' }
        $copiedFile = [IO.Path]::GetFullPath((Join-Path $copiedApp $item.path))
        if (-not $copiedFile.StartsWith($copiedApp + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Previous path escaped the copy.' }
        if ((Get-FileHash -LiteralPath $copiedFile -Algorithm SHA256).Hash -ne $item.sha256) { throw "Partial source changed unexpectedly: $($item.path)" }
    }
    $oldNames = @($previous.files | ForEach-Object path)
    $newNames = @($manifest.files | ForEach-Object path)
    foreach ($name in $oldNames) { if ($name -notin $newNames) { throw 'Resume cannot remove previously copied source files.' } }
    foreach ($file in Get-ChildItem -LiteralPath $copiedApp -File -Recurse -Force) {
        $relative = $file.FullName.Substring($copiedApp.Length + 1).Replace('\','/')
        if ($relative -notin $oldNames -and $relative -notlike 'data/runtime/*') { throw "Unexpected file in partial source: $relative" }
    }
    $acl = Get-Acl -LiteralPath $destination
    $allowedSids = @('S-1-5-18','S-1-5-32-544',$identity.User.Value)
    $rules = @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    if (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 3) { throw 'Unexpected partial-copy permissions.' }
    foreach ($rule in $rules) {
        if ($rule.IdentityReference.Value -notin $allowedSids -or $rule.AccessControlType -ne 'Allow' -or $rule.FileSystemRights -ne 'FullControl') { throw 'Unexpected partial-copy access rule.' }
    }
    if (@(Get-ChildItem -LiteralPath $destination -Force | Where-Object Name -NotIn @('app','python','packages')).Count) { throw 'Unexpected partial-copy top-level files.' }
    foreach ($name in @('python.exe','python312.dll')) {
        if ((Get-FileHash -LiteralPath (Join-Path $destination "python\$name")).Hash -ne (Get-FileHash -LiteralPath (Join-Path $runtime $name)).Hash) { throw 'Copied Python identity mismatch.' }
    }
    foreach ($name in @('manifest.json') + $assetNames) {
        if ((Get-FileHash -LiteralPath (Join-Path $copiedApp "data\runtime\public-demo\$name")).Hash -ne (Get-FileHash -LiteralPath (Join-Path $assets $name)).Hash) { throw 'Partial snapshot mismatch.' }
    }
    if (@(Get-ChildItem -LiteralPath (Join-Path $copiedApp 'data\runtime') -Force | Where-Object Name -NotIn @('public-demo','demo-control','demo-admission')).Count) { throw 'Unexpected runtime storage in partial copy.' }
    if (@(Get-ChildItem -LiteralPath (Join-Path $copiedApp 'data\runtime\public-demo') -Force | Where-Object Name -NotIn (@('manifest.json') + $assetNames)).Count) { throw 'Unexpected snapshot file in partial copy.' }
}
# Refuse reparse points throughout every copied source tree.
foreach ($tree in @($Source, $runtime) + $(if ($Resume) { @($destination) } else { @() })) {
    if (@(Get-ChildItem -LiteralPath $tree -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { throw 'Source contains a reparse point. No copy was made.' }
}
if (-not $Resume) {
New-Item -ItemType Directory -Path $destination | Out-Null
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true,$false)
foreach ($sid in @('S-1-5-18','S-1-5-32-544',$identity.User.Value)) {
    $rule = [Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $destination -AclObject $acl
$app = New-Item -ItemType Directory -Path (Join-Path $destination 'app')
} else {
    $app = Get-Item -LiteralPath (Join-Path $destination 'app')
}
foreach ($item in $manifest.files) {
    $target = Join-Path $app.FullName $item.path
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $Source $item.path) -Destination $target
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $item.sha256) { throw 'Copied source hash mismatch. Stop and inspect the partial installation.' }
}
if (-not $Resume) {
$pythonRoot = New-Item -ItemType Directory -Path (Join-Path $destination 'python')
foreach ($name in @('python.exe','pythonw.exe','python3.dll','python312.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt')) {
    $file = Join-Path $runtime $name
    if (Test-Path -LiteralPath $file) { Copy-Item -LiteralPath $file -Destination $pythonRoot.FullName }
}
foreach ($name in @('DLLs','Lib')) {
    & robocopy (Join-Path $runtime $name) (Join-Path $pythonRoot.FullName $name) /E /XJ /XD site-packages __pycache__ /XF '*.pyc' /R:0 /W:0 /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw 'Python copy failed. Stop and inspect the partial installation.' }
}
$packages = Join-Path $destination 'packages'
& $BuildPython -m pip install --disable-pip-version-check --no-cache-dir --no-compile --only-binary=:all: --target $packages -r (Join-Path $app.FullName 'requirements-lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Pinned dependency installation failed. No account was created.' }
} else {
    $pythonRoot = Get-Item -LiteralPath (Join-Path $destination 'python')
    $packages = Join-Path $destination 'packages'
}
$assetTarget = New-Item -ItemType Directory -Path (Join-Path $app.FullName 'data\runtime\public-demo') -Force
foreach ($name in @('manifest.json') + $assetNames) { Copy-Item -LiteralPath (Join-Path $assets $name) -Destination $assetTarget.FullName }
foreach ($name in $assetNames) {
    if ((Get-FileHash -LiteralPath (Join-Path $assetTarget.FullName $name) -Algorithm SHA256).Hash -ne $assetManifest.files.$name.sha256) { throw 'Copied snapshot hash mismatch.' }
}
# pip --target moves directories from its elevated temporary location. Normalize
# child ACLs to inherit our protected root; never reset the root or owner profile.
$checkedDestination = [IO.Path]::GetFullPath($destination)
if ($checkedDestination -ne 'C:\ProgramData\AEI-Sentinel-Demo') { throw 'Unexpected ACL target.' }
& icacls (Join-Path $checkedDestination '*') /reset /T /Q
if ($LASTEXITCODE -ne 0) { throw 'Child ACL inheritance preparation failed. No account was created.' }
$localPython = Join-Path $pythonRoot.FullName 'python.exe'
Push-Location -LiteralPath $app.FullName
try {
    # Module invocation survives Windows PowerShell 5.1 native argument quoting.
    & $localPython -B -m tools.prepare_demo_runtime --packages $packages
    if ($LASTEXITCODE -ne 0) { throw 'Isolated runtime preparation failed.' }
} finally { Pop-Location }
# Owner supplies a new password in the secure console prompt, never in chat.
$password = Read-Host 'New password for the standard local AEIDemo account' -AsSecureString
$user = New-LocalUser -Name $account -Password $password -Description 'AEI Sentinel synthetic demo only'
$usersGroup = Get-LocalGroup -SID 'S-1-5-32-545'
if (-not (Get-LocalGroupMember -Group $usersGroup.Name | Where-Object SID -EQ $user.SID)) { Add-LocalGroupMember -Group $usersGroup.Name -Member $user }
$reader = [Security.AccessControl.FileSystemAccessRule]::new($user.SID,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow')
$acl.AddAccessRule($reader)
Set-Acl -LiteralPath $destination -AclObject $acl
foreach ($name in @('demo-control','demo-admission')) {
    $path = Join-Path $app.FullName "data\runtime\$name"
    $writable = Get-Acl -LiteralPath $path
    $writable.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($user.SID,'Modify','ContainerInherit,ObjectInherit','None','Allow'))
    Set-Acl -LiteralPath $path -AclObject $writable
}
@{format=1; sid=$user.SID.Value; owner_profile='C:\Users\Denis'; origin='https://secular-bundle-uncapped.ngrok-free.dev'; publication_files=@($manifest.files).Count} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination 'isolation.json') -Encoding utf8
Write-Output 'Installation prepared. No server, service or public tunnel was started. Next: test actual account access and kernel limits before approving exposure.'
