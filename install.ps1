# Install from a release bundle or a source checkout. Uses a native executable.
# Windows PowerShell 5.1 compatible: powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$root = $PSScriptRoot
Set-Location -LiteralPath $root
$rest = @($args)
$prepareOnly = $rest.Count -gt 0 -and $rest[0] -eq '--prepare-only'
if ($prepareOnly) { $rest = @($rest | Select-Object -Skip 1) }
$version = (Select-String -LiteralPath 'herdr-plugin.toml' -Pattern '^version = "([^"]*)"' |
    Select-Object -First 1).Matches[0].Groups[1].Value
$bin = Join-Path $root 'bin'
$exe = Join-Path $bin 'herdr-agent-grid.exe'

function Invoke-Native([string]$program, [string[]]$arguments) {
    & $program @arguments
    if ($LASTEXITCODE -ne 0) { throw "$program exited with code $LASTEXITCODE" }
}

# A running grid locks its executable, but Windows allows renaming it aside.
function Install-Binary([string]$source) {
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    Get-ChildItem -LiteralPath $bin -Filter '.herdr-agent-grid.old-*.exe' -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    $next = Join-Path $bin '.herdr-agent-grid.new.exe'
    Copy-Item -LiteralPath $source -Destination $next -Force
    if (Test-Path -LiteralPath $exe) {
        Rename-Item -LiteralPath $exe -NewName ".herdr-agent-grid.old-$PID.exe"
    }
    Rename-Item -LiteralPath $next -NewName 'herdr-agent-grid.exe'
}

$cargo = if ($env:CARGO) { $env:CARGO } else { 'cargo' }
if (-not (Get-Command $cargo -ErrorAction SilentlyContinue)) {
    $cargo = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'
}
if ((Test-Path -LiteralPath 'Cargo.toml') -and (Get-Command $cargo -ErrorAction SilentlyContinue)) {
    Invoke-Native $cargo @('build', '--release', '--locked', '--package', 'herdr-agent-grid', '--bin', 'herdr-agent-grid')
    Install-Binary (Join-Path $root 'target\release\herdr-agent-grid.exe')
} else {
    $installed = ''
    if (Test-Path -LiteralPath $exe) { $installed = (& $exe --version | Out-String).Trim() }
    if ($installed -ne "herdr-agent-grid $version") {
        # OSArchitecture sees through x64 emulation on Arm64; it needs .NET 4.7.1+.
        try {
            $arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
        } catch {
            $arch = $env:PROCESSOR_ARCHITECTURE
        }
        switch ($arch) {
            { $_ -in 'X64', 'AMD64' } { $target = 'x86_64-pc-windows-msvc' }
            'Arm64' { $target = 'aarch64-pc-windows-msvc' }
            default { throw "Unsupported platform ($arch); build from source with Rust 1.88+." }
        }
        $asset = "herdr-agent-grid-v$version-$target.tar.gz"
        # HERDR_AGENT_GRID_RELEASE_URL lets CI test this path against local archives.
        $url = if ($env:HERDR_AGENT_GRID_RELEASE_URL) { $env:HERDR_AGENT_GRID_RELEASE_URL } else {
            "https://github.com/jeffarese/herdr-agent-grid/releases/download/v$version"
        }
        $stage = Join-Path ([System.IO.Path]::GetTempPath()) ("herdr-agent-grid-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $stage | Out-Null
        try {
            [Net.ServicePointManager]::SecurityProtocol =
                [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            $ProgressPreference = 'SilentlyContinue'
            $archive = Join-Path $stage $asset
            $sums = Join-Path $stage 'SHA256SUMS'
            try {
                Invoke-WebRequest -UseBasicParsing -Uri "$url/$asset" -OutFile $archive
                Invoke-WebRequest -UseBasicParsing -Uri "$url/SHA256SUMS" -OutFile $sums
            } catch {
                throw "Cannot download $asset ($($_.Exception.Message)). Install Rust 1.88+ from https://rustup.rs and run this installer again to build from source."
            }
            $expected = Get-Content -LiteralPath $sums | ForEach-Object {
                $fields = $_.Trim() -split '\s+'
                if ($fields.Count -eq 2 -and $fields[1] -eq $asset) { $fields[0] }
            } | Select-Object -First 1
            if (-not $expected) { throw "SHA256SUMS has no entry for $asset" }
            $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash
            if ($actual -ne $expected) { throw "Checksum mismatch for $asset" }
            # Prefer the inbox bsdtar: GNU tar from Git treats "C:" as a remote host.
            $tar = Join-Path $env:SystemRoot 'System32\tar.exe'
            if (-not (Test-Path -LiteralPath $tar)) { $tar = 'tar' }
            Invoke-Native $tar @('-xzf', $archive, '-C', $stage)
            Install-Binary (Join-Path $stage 'herdr-agent-grid\bin\herdr-agent-grid.exe')
        } finally {
            Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
if ($prepareOnly) { exit 0 }
& $exe install @rest
exit $LASTEXITCODE
