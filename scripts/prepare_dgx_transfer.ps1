[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BundlePath,
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [string]$ExpectedCommit = "",
    [string]$ExpectedBundleSha256 = "",
    [string]$PythonCommand = "python",
    [string]$DockerCommand = "docker"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function Assert-Sha256([string]$Path, [string]$Expected) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file is missing: $Path"
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "SHA-256 mismatch for $Path. Expected $Expected, got $actual"
    }
}

function Assert-Sidecar([string]$Path) {
    $sidecar = "$Path.sha256"
    if (-not (Test-Path -LiteralPath $sidecar -PathType Leaf)) {
        throw "Checksum sidecar is missing: $sidecar"
    }
    $line = Get-Content -LiteralPath $sidecar -TotalCount 1
    if ($line -notmatch '^([0-9a-fA-F]{64})\s+') {
        throw "Invalid checksum sidecar format: $sidecar"
    }
    Assert-Sha256 -Path $Path -Expected $Matches[1]
}

Assert-Command "tar.exe"
Assert-Command $PythonCommand
Assert-Command $DockerCommand

$bundle = (Resolve-Path -LiteralPath $BundlePath).Path
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $output | Out-Null

if ($ExpectedBundleSha256) {
    Assert-Sha256 -Path $bundle -Expected $ExpectedBundleSha256
} else {
    Assert-Sidecar -Path $bundle
}

# Use a unique per-run workspace. Never delete or reuse a pre-existing path.
$work = Join-Path $output (".cds-ai-transfer-work-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work -ErrorAction Stop | Out-Null

try {
    & tar.exe -xzf $bundle -C $work
    if ($LASTEXITCODE -ne 0) {
        throw "Could not extract the transfer bundle (tar exit $LASTEXITCODE)"
    }

    $manifestPath = Join-Path $work "TRANSFER_MANIFEST.txt"
    $releaseInfoPath = Join-Path $work "RELEASE_INFO.txt"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "TRANSFER_MANIFEST.txt is missing from the bundle"
    }
    if (-not (Test-Path -LiteralPath $releaseInfoPath -PathType Leaf)) {
        throw "RELEASE_INFO.txt is missing from the bundle"
    }

    $manifest = @{}
    foreach ($line in Get-Content -LiteralPath $manifestPath) {
        if ($line -match '^([a-z_]+)=(.*)$') {
            $manifest[$Matches[1]] = $Matches[2]
        }
    }
    $releaseInfo = @{}
    foreach ($line in Get-Content -LiteralPath $releaseInfoPath) {
        if ($line -match '^([a-z_]+)=(.*)$') {
            $releaseInfo[$Matches[1]] = $Matches[2]
        }
    }

    if ($ExpectedCommit -and $manifest["commit"] -ne $ExpectedCommit) {
        throw "Bundle commit mismatch. Expected $ExpectedCommit, got $($manifest['commit'])"
    }
    if ($releaseInfo["revision"] -ne $manifest["commit"] -or
        $releaseInfo["archive"] -ne $manifest["source_archive"]) {
        throw "Release metadata does not match the transfer manifest"
    }
    if ($manifest["architecture"] -ne "aarch64" -or $manifest["python_abi"] -ne "cp312") {
        throw "Unexpected wheelhouse target in transfer manifest"
    }

    $sourceArchive = Join-Path $work $manifest["source_archive"]
    $wheelArchive = Join-Path $work $manifest["wheelhouse_archive"]
    Assert-Sidecar -Path $sourceArchive
    Assert-Sidecar -Path $wheelArchive

    $sourceRoot = Join-Path $work "source"
    New-Item -ItemType Directory -Force -Path $sourceRoot | Out-Null
    Expand-Archive -LiteralPath $sourceArchive -DestinationPath $sourceRoot -Force

    $projectRoot = Join-Path $sourceRoot "CDS_AI"
    $exportScript = Join-Path $projectRoot "scripts/export_sandbox_images.py"
    if (-not (Test-Path -LiteralPath $exportScript -PathType Leaf)) {
        throw "Sandbox image export script is missing from the source archive"
    }

    $pythonVersion = & $PythonCommand --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Python is not runnable: $pythonVersion"
    }
    Write-Host "Staging Python: $pythonVersion"

    $dockerOs = & $DockerCommand info --format '{{.OSType}}' 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Docker daemon is not available: $dockerOs"
    }
    if (($dockerOs | Out-String).Trim() -ne "linux") {
        throw "Docker must be running Linux containers; detected: $dockerOs"
    }

    $builderInfo = & $DockerCommand buildx inspect --bootstrap 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Buildx is not ready: $builderInfo"
    }
    if (($builderInfo | Out-String) -notmatch 'linux/arm64') {
        throw "The active Buildx builder does not advertise linux/arm64 support"
    }

    $imageArchive = Join-Path $output "clicoding-sandbox-images-arm64.tar"
    $pythonArgs = @(
        $exportScript,
        "--docker", $DockerCommand,
        "--pull",
        "--build-typescript",
        "--output", $imageArchive
    )
    & $PythonCommand @pythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Sandbox image export failed (exit $LASTEXITCODE)"
    }

    foreach ($name in @(
        "clicoding-sandbox-images-arm64.tar",
        "clicoding-sandbox-images-arm64.tar.sha256",
        "sandbox-image-lock.json",
        "sandbox-image-lock.json.sha256"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $output $name) -PathType Leaf)) {
            throw "Expected Docker transfer output is missing: $name"
        }
    }
    Assert-Sidecar -Path $imageArchive
    Assert-Sidecar -Path (Join-Path $output "sandbox-image-lock.json")

    $bundleCopy = Join-Path $output (Split-Path -Leaf $bundle)
    if ($bundle -ne $bundleCopy) {
        Copy-Item -LiteralPath $bundle -Destination $bundleCopy -Force
    }
    $bundleHash = (Get-FileHash -LiteralPath $bundleCopy -Algorithm SHA256).Hash.ToLowerInvariant()
    "$bundleHash  $(Split-Path -Leaf $bundleCopy)" |
        Set-Content -LiteralPath "$bundleCopy.sha256" -Encoding ascii

    Write-Host ""
    Write-Host "DGX transfer staging completed."
    Write-Host "Commit: $($manifest['commit'])"
    Write-Host "Transfer directory: $output"
    Write-Host "Files to transfer:"
    Get-ChildItem -LiteralPath $output -File |
        Sort-Object Name |
        ForEach-Object { Write-Host ("  {0} ({1:N0} bytes)" -f $_.Name, $_.Length) }
    Write-Host ""
    Write-Host "No DGX host was contacted or modified."
}
finally {
    if (Test-Path -LiteralPath $work) {
        Remove-Item -LiteralPath $work -Recurse -Force
    }
}
