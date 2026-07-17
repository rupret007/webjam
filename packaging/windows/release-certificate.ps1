[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Validate", "Prepare", "Cleanup")]
    [string] $Command
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$PfxPath = Join-Path $env:RUNNER_TEMP "webjam-release-codesign.pfx"
$StatePath = Join-Path $env:RUNNER_TEMP "webjam-release-certificate-thumbprints.txt"

function Get-RequiredEnvironmentValue([string] $Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Windows release signing requires $Name."
    }
    return $value
}

function Get-PfxCertificates {
    $encoded = Get-RequiredEnvironmentValue "WINDOWS_CODESIGN_PFX"
    $password = Get-RequiredEnvironmentValue "WINDOWS_CODESIGN_PASSWORD"
    $expectedSubject = Get-RequiredEnvironmentValue "WINDOWS_CODESIGN_SUBJECT"
    try {
        $bytes = [Convert]::FromBase64String(($encoded -replace "\s", ""))
    } catch {
        throw "WINDOWS_CODESIGN_PFX is not valid base64."
    }
    if ($bytes.Length -eq 0) {
        throw "WINDOWS_CODESIGN_PFX decodes to an empty value."
    }

    $certificates = [Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
    try {
        $certificates.Import(
            $bytes,
            $password,
            [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
        )
    } catch {
        throw "WINDOWS_CODESIGN_PFX could not be opened with the supplied password."
    }
    $signers = @($certificates | Where-Object { $_.HasPrivateKey })
    if ($signers.Count -ne 1) {
        throw "Expected exactly one certificate with a private key; found $($signers.Count)."
    }
    $certificate = $signers[0]
    if ($certificate.Subject -cne $expectedSubject) {
        throw "Signing certificate subject does not match WINDOWS_CODESIGN_SUBJECT."
    }

    $now = [DateTime]::UtcNow
    if ($certificate.NotBefore.ToUniversalTime() -gt $now -or
        $certificate.NotAfter.ToUniversalTime() -le $now) {
        throw "Signing certificate is not currently valid."
    }
    $sha1SignatureOids = @("1.2.840.113549.1.1.5", "1.2.840.10045.4.1")
    if ($sha1SignatureOids -contains $certificate.SignatureAlgorithm.Value) {
        throw "Signing certificate uses a forbidden SHA-1 certificate signature."
    }

    $ekuExtensions = @(
        $certificate.Extensions | Where-Object {
            $_ -is [Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]
        }
    )
    if ($ekuExtensions.Count -ne 1) {
        throw "Signing certificate must contain exactly one EKU extension."
    }
    $ekuOids = @($ekuExtensions[0].EnhancedKeyUsages | ForEach-Object { $_.Value })
    if ($ekuOids -notcontains "1.3.6.1.5.5.7.3.3") {
        throw "Signing certificate is missing the Code Signing EKU."
    }

    $keyUsageExtensions = @(
        $certificate.Extensions | Where-Object {
            $_ -is [Security.Cryptography.X509Certificates.X509KeyUsageExtension]
        }
    )
    if ($keyUsageExtensions.Count -gt 1) {
        throw "Signing certificate contains ambiguous key-usage extensions."
    }
    if ($keyUsageExtensions.Count -eq 1) {
        $digitalSignature = [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature
        if (($keyUsageExtensions[0].KeyUsages -band $digitalSignature) -eq 0) {
            throw "Signing certificate key usage does not permit digital signatures."
        }
    }

    $rsa = [Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey(
        $certificate
    )
    try {
        if ($null -eq $rsa) {
            throw "Signing certificate must use RSA for Windows application-control compatibility."
        }
        if ($rsa.KeySize -lt 2048 -or $rsa.KeySize -gt 4096) {
            throw "RSA signing key must be between 2048 and 4096 bits."
        }
    } finally {
        if ($null -ne $rsa) { $rsa.Dispose() }
    }

    return [PSCustomObject]@{
        Certificates = $certificates
        Signer = $certificate
    }
}

function Remove-PreparedCertificate {
    $cleanupErrors = [Collections.Generic.List[string]]::new()
    $thumbprints = @()
    if (Test-Path -LiteralPath $StatePath) {
        try {
            $recordedThumbprints = @(Get-Content -LiteralPath $StatePath -ErrorAction Stop)
        } catch {
            $cleanupErrors.Add("could not read the certificate cleanup state")
            $recordedThumbprints = @()
        }
        foreach ($recordedThumbprint in $recordedThumbprints) {
            $thumbprint = $recordedThumbprint.Trim().ToUpperInvariant()
            if ($thumbprint -notmatch "^[A-F0-9]{40}$") {
                $cleanupErrors.Add("certificate cleanup state contains an invalid thumbprint")
                continue
            }
            $thumbprints += $thumbprint
        }
        $thumbprints = @($thumbprints | Sort-Object -Unique)
    }

    foreach ($thumbprint in $thumbprints) {
        $certificatePath = "Cert:\CurrentUser\My\$thumbprint"
        try {
            if (Test-Path -LiteralPath $certificatePath) {
                Remove-Item -LiteralPath $certificatePath `
                    -DeleteKey -Force -ErrorAction Stop
            }
            if (Test-Path -LiteralPath $certificatePath) {
                $cleanupErrors.Add("certificate $thumbprint remains in CurrentUser\\My")
            }
        } catch {
            $cleanupErrors.Add("could not remove certificate $thumbprint from CurrentUser\\My")
        }
    }

    foreach ($path in @($PfxPath, $StatePath)) {
        try {
            if (Test-Path -LiteralPath $path) {
                Remove-Item -LiteralPath $path -Force -ErrorAction Stop
            }
            if (Test-Path -LiteralPath $path) {
                $cleanupErrors.Add("temporary signing file remains: $path")
            }
        } catch {
            $cleanupErrors.Add("could not remove temporary signing file: $path")
        }
    }

    if ($cleanupErrors.Count -gt 0) {
        throw "Windows release certificate cleanup failed: $($cleanupErrors -join '; ')."
    }
}

function Write-GitHubEnvironment([string] $Name, [string] $Value) {
    if ([string]::IsNullOrWhiteSpace($env:GITHUB_ENV)) {
        throw "GITHUB_ENV is required when preparing Windows release signing."
    }
    Add-Content -LiteralPath $env:GITHUB_ENV -Encoding utf8 -Value "$Name=$Value"
}

function Prepare-Certificate {
    Remove-PreparedCertificate
    $validated = Get-PfxCertificates
    $thumbprints = @(
        $validated.Certificates |
            ForEach-Object { $_.Thumbprint.ToUpperInvariant() } |
            Sort-Object -Unique
    )
    foreach ($thumbprint in $thumbprints) {
        if (Test-Path -LiteralPath "Cert:\CurrentUser\My\$thumbprint") {
            throw "Refusing to replace a certificate already present in CurrentUser\\My."
        }
    }

    try {
        $encoded = Get-RequiredEnvironmentValue "WINDOWS_CODESIGN_PFX"
        $password = Get-RequiredEnvironmentValue "WINDOWS_CODESIGN_PASSWORD"
        [IO.File]::WriteAllBytes(
            $PfxPath,
            [Convert]::FromBase64String(($encoded -replace "\s", ""))
        )
        # Record the cleanup allowlist before importing anything so a failure
        # during or immediately after Import-PfxCertificate is still reversible.
        [IO.File]::WriteAllLines($StatePath, $thumbprints, [Text.Encoding]::ASCII)
        $securePassword = ConvertTo-SecureString $password -AsPlainText -Force
        Import-PfxCertificate -FilePath $PfxPath `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -Password $securePassword | Out-Null

        $signerThumbprint = $validated.Signer.Thumbprint.ToUpperInvariant()
        $imported = Get-Item -LiteralPath "Cert:\CurrentUser\My\$signerThumbprint"
        if (-not $imported.HasPrivateKey) {
            throw "Imported signing certificate lost its private key."
        }
        $signtool = (
            Get-ChildItem -Path "C:\Program Files (x86)\Windows Kits\10\bin" `
                -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
                Sort-Object FullName |
                Select-Object -Last 1
        ).FullName
        if ([string]::IsNullOrWhiteSpace($signtool)) {
            throw "signtool.exe was not found."
        }

        Write-GitHubEnvironment "WEBJAM_WINDOWS_CODESIGN_THUMBPRINT" $signerThumbprint
        Write-GitHubEnvironment "WEBJAM_WINDOWS_CODESIGN_SUBJECT" $validated.Signer.Subject
        Write-GitHubEnvironment "WEBJAM_WINDOWS_SIGNTOOL" $signtool
        Write-Host "Prepared one code-signing certificate for '$($validated.Signer.Subject)'."
    } catch {
        Remove-PreparedCertificate
        throw
    } finally {
        if (Test-Path -LiteralPath $PfxPath) {
            Remove-Item -LiteralPath $PfxPath -Force -ErrorAction Stop
        }
        if (Test-Path -LiteralPath $PfxPath) {
            throw "Temporary signing PFX remains after certificate preparation."
        }
    }
}

switch ($Command) {
    "Validate" {
        $validated = Get-PfxCertificates
        Write-Host "Validated code-signing certificate '$($validated.Signer.Subject)'."
    }
    "Prepare" { Prepare-Certificate }
    "Cleanup" { Remove-PreparedCertificate }
}
