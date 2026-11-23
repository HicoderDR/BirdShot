# SPDX-License-Identifier: AGPL-3.0-only
# Optional build machine setup. No administrator, policy, firewall or product changes.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$folder = Join-Path $root '.tools\inno-setup'
$compiler = Join-Path $folder 'compiler\ISCC.exe'
if (Test-Path -LiteralPath $compiler) {
    Write-Output 'Inno Setup compiler already exists; use BIRDSHOT_ISCC to choose another installation.'
    exit 0
}
New-Item -ItemType Directory -Force $folder | Out-Null
$installer = Join-Path $folder 'innosetup-7.1.1-x64.exe'
$source = 'https://github.com/jrsoftware/issrc/releases/download/is-7_1_1/innosetup-7.1.1-x64.exe'
$expected = '8a5a6711c3766a6e5fb74f280ba0e3551e719ce94538d901fdbd1f8f9920b3f0'
if (!(Test-Path -LiteralPath $installer)) {
    Invoke-WebRequest -Uri $source -OutFile $installer -UseBasicParsing
}
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
    throw 'Official compiler installer checksum mismatch; not executing.'
}
$signature = Get-AuthenticodeSignature -LiteralPath $installer
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Pyrsys B\.V\.') {
    throw 'Official compiler installer signature is not valid; not executing.'
}
$target = Join-Path $folder 'compiler'
$process = Start-Process -FilePath $installer -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/NOICONS', ('/DIR="' + $target + '"')
) -PassThru -Wait
if ($process.ExitCode -ne 0 -or !(Test-Path -LiteralPath $compiler)) {
    throw 'Build-tool installation failed; no BirdShot installation was changed.'
}
Write-Output 'Verified official Inno Setup 7.1.1 installed for the current user in .tools.'
