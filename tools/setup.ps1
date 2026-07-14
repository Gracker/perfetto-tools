$ErrorActionPreference = "Stop"

function ConvertFrom-EnvFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    $values = @{}
    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        $parts = $line.Split("=", 2)
        if ($parts.Count -ne 2 -or -not $parts[0] -or -not $parts[1]) {
            throw "Malformed version manifest line: $rawLine"
        }
        $values[$parts[0]] = $parts[1]
    }
    return $values
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$BinDir = Join-Path $RepoRoot ".bin"
$versions = ConvertFrom-EnvFile (Join-Path $ScriptDir "tool-versions.env")

if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne [System.Runtime.InteropServices.Architecture]::X64) {
    throw "Windows setup supports x86_64 only."
}

$asset = $versions["UV_WINDOWS_AMD64_ASSET"]
$expected = $versions["UV_WINDOWS_AMD64_SHA256"]
$uvDir = Join-Path $BinDir "uv"
$uv = Join-Path $uvDir "uv.exe"
$uvReady = $false
if (Test-Path -LiteralPath $uv) {
    $uvReady = ((& $uv --version) -like "uv $($versions['UV_VERSION'])*")
}

if (-not $uvReady) {
    $downloads = Join-Path $BinDir "downloads"
    New-Item -ItemType Directory -Force -Path $downloads | Out-Null
    $archive = Join-Path $downloads $asset
    $url = "https://github.com/astral-sh/uv/releases/download/$($versions['UV_VERSION'])/$asset"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        Remove-Item -Force -LiteralPath $archive
        throw "uv archive checksum mismatch (expected $expected, got $actual)."
    }
    $tempDir = Join-Path $BinDir ("uv." + [Guid]::NewGuid().ToString("N"))
    Expand-Archive -LiteralPath $archive -DestinationPath $tempDir
    $candidate = Get-ChildItem -Path $tempDir -Filter "uv.exe" -File -Recurse | Select-Object -First 1
    if (-not $candidate) { throw "uv archive layout is invalid: $asset" }
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -LiteralPath $uvDir
    New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
    Move-Item -LiteralPath $candidate.FullName -Destination $uv
    Remove-Item -Recurse -Force -LiteralPath $tempDir
}

$env:UV_CACHE_DIR = Join-Path $BinDir "uv-cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $BinDir "python"
$env:UV_MANAGED_PYTHON = "1"
$env:UV_NO_CONFIG = "1"
$env:UV_PROJECT_ENVIRONMENT = Join-Path $RepoRoot ".venv"

Push-Location $RepoRoot
try {
    & $uv sync --frozen --python $versions["PYTHON_VERSION"]
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE" }
    $python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    & $python (Join-Path $ScriptDir "setup_runtime.py") @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
