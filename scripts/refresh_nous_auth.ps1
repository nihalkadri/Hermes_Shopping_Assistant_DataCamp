<#
.SYNOPSIS
Refresh the Oracle VM's Nous Portal login for the shopping-bot container.

.DESCRIPTION
Nous's OAuth token does not survive being copied to the VM long-term (confirmed
2026-08-14 - it lasted about a day before its own keepalive required a fresh
login, not just a refresh). Run this shortly before a demo, not days ahead.

What it does:
  1. Confirms you're logged in to Nous locally (runs `hermes setup` if not).
  2. Fetches the container's current auth.json from the VM.
  3. Merges in your fresh local Nous credential, keeping everything else
     (e.g. the OpenRouter fallback key) untouched.
  4. Uploads the merged file back and restarts the container.
  5. Verifies `hermes auth status nous` inside the container says "logged in".

.PARAMETER VmIp
The Oracle VM's public IP.

.PARAMETER SshKey
Path to your SSH private key for the VM.

.EXAMPLE
.\scripts\refresh_nous_auth.ps1 -VmIp <your-vm-public-ip> -SshKey "C:\path\to\your-key.key"
#>
param(
    [Parameter(Mandatory = $true)][string]$VmIp,
    [Parameter(Mandatory = $true)][string]$SshKey,
    [string]$VmUser = "ubuntu"
)

$ErrorActionPreference = "Stop"
$localAuth = "$env:LOCALAPPDATA\hermes\auth.json"
$scratch = Join-Path $env:TEMP "nous_auth_refresh"
New-Item -ItemType Directory -Force -Path $scratch | Out-Null
$remoteAuthLocal = Join-Path $scratch "remote_auth.json"
$mergedAuthLocal = Join-Path $scratch "merged_auth.json"

Write-Output "1. Checking local Nous login..."
$status = hermes auth status nous
if ($status -notmatch "logged in") {
    Write-Output "   Not logged in - running 'hermes setup' now. Choose Quick Setup (Nous Portal)."
    hermes setup
    $status = hermes auth status nous
    if ($status -notmatch "logged in") {
        throw "Still not logged in to Nous after 'hermes setup'. Aborting."
    }
}
Write-Output "   Local Nous login OK."

Write-Output "2. Fetching the container's current auth.json..."
ssh -i "$SshKey" "$VmUser@$VmIp" "sudo cat /home/ubuntu/hermes-data/auth.json" | Out-File -Encoding utf8 $remoteAuthLocal
if (-not (Test-Path $remoteAuthLocal) -or (Get-Item $remoteAuthLocal).Length -eq 0) {
    throw "Could not fetch remote auth.json - check VM connectivity."
}

Write-Output "3. Merging fresh Nous credential..."
$local = Get-Content $localAuth | ConvertFrom-Json
$remote = Get-Content $remoteAuthLocal | ConvertFrom-Json
$remote.providers | Add-Member -NotePropertyName nous -NotePropertyValue $local.providers.nous -Force
if (-not $remote.credential_pool.nous) {
    $remote.credential_pool | Add-Member -NotePropertyName nous -NotePropertyValue @($local.credential_pool.nous) -Force
} else {
    $remote.credential_pool.nous = @($local.credential_pool.nous)
}
$remote | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 $mergedAuthLocal

Write-Output "4. Uploading merged auth.json and restarting the container..."
scp -i "$SshKey" "$mergedAuthLocal" "${VmUser}@${VmIp}:~/merged_auth.json"
ssh -i "$SshKey" "$VmUser@$VmIp" "sudo cp ~/merged_auth.json /home/ubuntu/hermes-data/auth.json && sudo chown root:root /home/ubuntu/hermes-data/auth.json && sudo chmod 600 /home/ubuntu/hermes-data/auth.json && rm ~/merged_auth.json"

ssh -i "$SshKey" "$VmUser@$VmIp" "docker restart shopping-bot"

Write-Output "5. Verifying..."
Start-Sleep -Seconds 10
$remoteStatus = ssh -i "$SshKey" "$VmUser@$VmIp" "docker exec shopping-bot hermes auth status nous"
Write-Output "   Container Nous status: $remoteStatus"
if ($remoteStatus -notmatch "logged in") {
    Write-Warning "Refresh did not take effect - check manually before the demo."
} else {
    Write-Output "Done. Nous is live on the VM again."
}
