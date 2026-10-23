# Shared helpers for the PowerShell scripts: log in to Orchestrate and run CLI commands with retries.
# Dot-source it:  . (Join-Path $PSScriptRoot "_orch.ps1")   then   Connect-Orch $url $key
$script:OrchExe = Join-Path (Split-Path -Parent $PSScriptRoot) "venv\Scripts\orchestrate.exe"

function Connect-Orch([string]$Url, [string]$Key) {
    # PowerShell 5.1 turns a native command's stderr into terminating errors under "Stop";
    # the CLI writes warnings there, so read everything as plain text.
    $ErrorActionPreference = "Continue"
    $script:OrchUrl, $script:OrchKey = $Url, $Key
    $envs = & $script:OrchExe env list 2>&1 | Out-String
    if ($envs -notmatch "nyp_env") { & $script:OrchExe env add -n nyp_env -u $Url 2>&1 | Out-Null }
    $out = & $script:OrchExe env activate nyp_env --api-key $Key 2>&1 | ForEach-Object { "$_" }
    $out | Write-Host
    if (-not ($out -match "is now active")) {
        throw "Could not log in to Orchestrate (see above). If it says Forbidden, the key has no role on the instance: ask the facilitator."
    }
}

# Run one CLI command. Retries 403 "does not have roles" (the shared tenant flickers) and
# logs in again when the session is "missing or expired".
function Invoke-Orch {
    $ErrorActionPreference = "Continue"
    for ($try = 1; $try -le 4; $try++) {
        $out = & $script:OrchExe @args 2>&1 | ForEach-Object { "$_" }
        $out | Write-Host
        if ($LASTEXITCODE -eq 0 -and -not ($out -match "status_code=403|does not have roles|missing or expired")) { return }
        if ($try -eq 4) { break }
        if ($out -match "missing or expired") {
            Write-Host "  ...session expired, logging in again (try $try of 4)"
            Connect-Orch $script:OrchUrl $script:OrchKey
        } else {
            Write-Host "  ...Orchestrate refused (try $try of 4), retrying in 10s"
            Start-Sleep -Seconds 10
        }
    }
    throw "Orchestrate kept refusing: orchestrate $($args[0..1] -join ' ') (see the messages above)."
}
