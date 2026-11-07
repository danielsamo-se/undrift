param([Parameter(Mandatory=$true)][string]$Config)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$activePath = (Resolve-Path -LiteralPath $Config).Path
$privateRoot = (Join-Path $root '.tools') + '\'
if (-not $activePath.StartsWith($privateRoot) -or
    $activePath.Substring($privateRoot.Length) -notmatch '^reliability-v[1-9][0-9]*\\active\.json$') { throw 'Unapproved evaluation configuration path.' }
$active = Get-Content -LiteralPath $activePath -Raw | ConvertFrom-Json
$targets = @(@{service='mcp';port=8001}, @{service='api';port=8401})
$owners = @{}
foreach ($target in $targets) {
    $listener = @(Get-NetTCPConnection -State Listen -LocalPort $target.port -ErrorAction SilentlyContinue)
    if ($listener.Count -eq 0) {
        $previous = Get-Content -LiteralPath (Join-Path (Split-Path -Parent $activePath) ($target.service + '-runtime.json')) -Raw | ConvertFrom-Json
        if (Get-Process -Id $previous.pid -ErrorAction SilentlyContinue) { throw 'Previous service is still alive without a listener; inspect before restarting.' }
        $owners[$target.service] = $null
        continue
    }
    if ($listener.Count -ne 1 -or $listener[0].LocalAddress -ne '127.0.0.1') { throw 'Unexpected evaluation listener.' }
    $owner = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $listener[0].OwningProcess)
    if ($owner.ExecutablePath -ne $python -or $owner.CommandLine -notmatch ('(start_clarification_demo\.py|evaluation_service\.py)"?\s+' + $target.service + '(\s|$)')) { throw 'Listener not owned by an approved demo service.' }
    $runtimePath = Join-Path (Split-Path -Parent $activePath) ($target.service + '-runtime.json')
    if (Test-Path -LiteralPath $runtimePath) {
        $runtime = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
        $expectedOwner = $runtime.pid
    } elseif ($owner.CommandLine -match 'evaluation_service\.py"?\s+\w+\s+--config\s+"([^"]+)"') {
        # A new series may take over the same two ports only from the recorded
        # owner of the previous series. Never fall back to a guessed process ID.
        $priorConfig = $Matches[1]
        if (-not $priorConfig.StartsWith($privateRoot) -or
            $priorConfig.Substring($privateRoot.Length) -notmatch '^reliability-v[1-9][0-9]*\\active\.json$') { throw 'Unapproved previous evaluation configuration.' }
        $runtime = Get-Content -LiteralPath (Join-Path (Split-Path -Parent $priorConfig) ($target.service + '-runtime.json')) -Raw | ConvertFrom-Json
        $expectedOwner = $runtime.pid
    } else {
        $runtime = Get-Content -LiteralPath (Join-Path $root ('.tools\clarification-' + $target.service + '-runtime.json')) -Raw | ConvertFrom-Json
        $expectedOwner = $runtime.process_id
    }
    if ($owner.ProcessId -ne $expectedOwner) { throw 'Listener does not match the saved runtime owner.' }
    $owners[$target.service] = $owner.ProcessId
}
$archives = @{}
foreach ($port in 8101,8301,8200,4040) { $archives[$port] = @(Get-NetTCPConnection -State Listen -LocalPort $port).OwningProcess }
foreach ($target in $targets) {
    if ($null -ne $owners[$target.service]) {
        $owner = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $owners[$target.service])
        if ($owner.CommandLine -notmatch ('(start_clarification_demo\.py|evaluation_service\.py)"?\s+' + $target.service + '(\s|$)')) { throw 'Ownership changed.' }
        Stop-Process -Id $owner.ProcessId
    }
    Start-Sleep -Milliseconds 350
    $logPrefix = Join-Path (Split-Path -Parent $activePath) ($active.slot.ToString() + '-' + $target.service)
    Start-Process -FilePath $python -ArgumentList @('-B', ('"' + (Join-Path $PSScriptRoot 'evaluation_service.py') + '"'), $target.service, '--config', ('"' + $activePath + '"')) -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput ($logPrefix + '.stdout.log') -RedirectStandardError ($logPrefix + '.stderr.log') | Out-Null
}
foreach ($port in 8101,8301,8200,4040) {
    if (Compare-Object @($archives[$port]) @(@(Get-NetTCPConnection -State Listen -LocalPort $port).OwningProcess)) { throw 'Unrelated listener changed.' }
}
Write-Output ('Evaluation services activated for slot ' + $active.slot)
