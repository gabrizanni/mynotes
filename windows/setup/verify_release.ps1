param(
    [Parameter(Mandatory)][string]$Installer,
    [Parameter(Mandatory)][string]$Checksum,
    [Parameter(Mandatory)][string]$ExpectedSignerThumbprint
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$checksumPath = (Resolve-Path -LiteralPath $Checksum).Path
$name = [IO.Path]::GetFileName($installerPath)
if ($name -cne 'MyNotes-Setup-x64.exe') {
    throw 'Expected the public MyNotes-Setup-x64.exe asset.'
}

$digestLine = [regex]::Match((Get-Content -Raw -LiteralPath $checksumPath).Trim(),
    '^([0-9a-fA-F]{64})  MyNotes-Setup-x64\.exe$')
if (-not $digestLine.Success) {
    throw 'Expected one SHA-256 digest for the public installer.'
}
$expectedHash = $digestLine.Groups[1].Value
$actualHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash
if ($actualHash -ne $expectedHash) {
    throw 'The downloaded installer does not match its checksum.'
}

$signature = Get-AuthenticodeSignature -LiteralPath $installerPath
if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or -not $signature.TimeStamperCertificate) {
    throw "The installer requires a trusted, timestamped Authenticode signature. Status: $($signature.Status)."
}
$thumbprint = $ExpectedSignerThumbprint -replace '\s', ''
if ($thumbprint -notmatch '^[0-9a-fA-F]{40}$' -or $signature.SignerCertificate.Thumbprint -ne $thumbprint) {
    throw 'The installer signature does not match the expected publisher certificate.'
}

[pscustomobject]@{
    Installer = $name
    SHA256 = $actualHash
    Publisher = $signature.SignerCertificate.Subject
    Authenticode = $signature.Status
}
