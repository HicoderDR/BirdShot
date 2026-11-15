# SPDX-License-Identifier: AGPL-3.0-only
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)')]
    [string]$LocalAddress,
    [ValidateSet(5174)]
    [int]$Port = 5174
)

$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator PowerShell is required to add this narrow firewall rule. No settings were changed.'
}
$address = Get-NetIPAddress -IPAddress $LocalAddress -AddressFamily IPv4 -ErrorAction Stop
if ($address.InterfaceIndex -notin @(Get-NetAdapter -Physical | Select-Object -ExpandProperty ifIndex)) {
    throw 'The selected address does not belong to a physical adapter. No rule was added.'
}
$profile = Get-NetConnectionProfile -InterfaceIndex $address.InterfaceIndex -ErrorAction Stop
if ($profile.NetworkCategory -ne 'Private') {
    throw 'The selected interface is not Private. No network profile or firewall settings were changed.'
}
$listener = Get-NetTCPConnection -LocalAddress $LocalAddress -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
$process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $listener.OwningProcess)
if ($process.CommandLine -notmatch 'batch_culling.py.*serve' -or $process.CommandLine -notmatch [regex]::Escape($LocalAddress)) {
    throw 'The listener is not the explicitly configured BirdShot report service. No rule was added.'
}
$name = 'BirdShot-Report-' + $Port + '-Private-LAN-' + $LocalAddress.Replace('.', '')
$existing = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
if ($existing) {
    $ports = $existing | Get-NetFirewallPortFilter
    $addresses = $existing | Get-NetFirewallAddressFilter
    $application = $existing | Get-NetFirewallApplicationFilter
    $localPorts = @($ports.LocalPort)
    $localAddresses = @($addresses.LocalAddress)
    $remoteAddresses = @($addresses.RemoteAddress)
    $matches = $existing.Direction.ToString() -eq 'Inbound' -and $existing.Action.ToString() -eq 'Allow' `
        -and $existing.Enabled.ToString() -eq 'True' -and $existing.Profile.ToString() -eq 'Private' `
        -and $ports.Protocol.ToString() -in @('TCP', '6') -and $localPorts.Count -eq 1 -and $localPorts[0] -eq $Port.ToString() `
        -and $localAddresses.Count -eq 1 -and $localAddresses[0] -eq $LocalAddress `
        -and $remoteAddresses.Count -eq 1 -and $remoteAddresses[0] -eq 'LocalSubnet' `
        -and $application.Program -ieq $process.ExecutablePath
    if (!$matches) {
        throw 'A different rule already uses this name. Nothing was overwritten; inspect it manually.'
    }
    Write-Output 'The exact restricted rule already exists. No changes were needed.'
    exit 0
}
New-NetFirewallRule -Name $name -DisplayName ('BirdShot report ' + $Port + ' (Private LAN only)') `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -LocalAddress $LocalAddress `
    -RemoteAddress LocalSubnet -Program $process.ExecutablePath -Profile Private -Enabled True | Out-Null
Write-Output ('Created only the Private/LocalSubnet rule for http://' + $LocalAddress + ':' + $Port + '/')
