<#
.SYNOPSIS
  Builds the Windows installer of SPC: an embedded Python, the program and its packages (locked versions), the release manifest, a self-test (IQ/OQ on the build), and the installer.

.DESCRIPTION
  Run it on a 64 bit Windows computer that has: the same Python minor version as -PythonVersion (for example 3.11, 64 bit, "py -3.11"), internet access to python.org and PyPI,
  and Inno Setup 6 (ISCC.exe). It is a build step of the vendor, not something a customer runs.

  The embedded Python zip is checked against -PythonZipSha256 (take the value from the download page of python.org). The build stops when the hash differs.
  The self-test runs the installation and operational qualification on the built tree. A failed check stops the build.

.EXAMPLE
  .\build.ps1 -PythonVersion 3.11.9 -PythonZipSha256 <sha256 of python-3.11.9-embed-amd64.zip>
#>
param(
  [Parameter(Mandatory = $true)][string]$PythonVersion,
  [Parameter(Mandatory = $true)][string]$PythonZipSha256,
  [string]$OutDir = (Join-Path $PSScriptRoot "..\..\dist"),
  [string]$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  [string]$BuildPython = "",  # python.exe of the same minor version that builds the wheels; empty: the py launcher ("py -3.11")
  [switch]$WithOpcUa
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$minor = ($PythonVersion -split "\.")[0..1] -join "."
function Invoke-BuildPython { if ($BuildPython) { & $BuildPython @args } else { & py "-$minor" @args } }
$build = Join-Path $root "build\windows"
$app = Join-Path $build "app"
if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Force -Path $app, $OutDir | Out-Null

# version of the program
$version = (Select-String -Path (Join-Path $root "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "SPC $version, Python $PythonVersion"

# 1. the embedded Python, checked against the hash that was given
$zip = Join-Path $build "python-embed.zip"
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" -OutFile $zip
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($hash -ne $PythonZipSha256.ToLower()) { throw "The embedded Python has the SHA-256 $hash, not $PythonZipSha256. Build stopped." }
Expand-Archive -Path $zip -DestinationPath (Join-Path $app "python")

# 2. the packages go to app\site-packages; the embedded Python finds them through its ._pth file
$pth = Get-ChildItem (Join-Path $app "python") -Filter "python*._pth" | Select-Object -First 1
Add-Content -Path $pth.FullName -Value "..\site-packages"
$extras = if ($WithOpcUa) { "web,sign,opcua" } else { "web,sign" }
# the packages come from the lock file: exact versions and the SHA-256 of every wheel (pip --require-hashes stops at any other file,
# and at a dependency that the lock file does not hold);
# the lock files are written by tools/lock_packages.py --os windows and change only on purpose. Wheels only: nothing is compiled on the build machine.
$lock = Join-Path $PSScriptRoot ("requirements-" + ($extras -replace ",", "-") + ".lock")
if (-not (Test-Path $lock)) { throw "lock file not found: $lock" }
$site = Join-Path $app "site-packages"
Invoke-BuildPython -m pip install --no-warn-script-location --only-binary=:all: --require-hashes --target $site -r $lock
if ($LASTEXITCODE -ne 0) { throw "pip install of the locked packages failed" }
# then the program itself, built into a wheel, without its dependencies (they are the locked ones above)
$wheels = Join-Path $build "wheels"
Invoke-BuildPython -m pip wheel --no-deps --wheel-dir $wheels "$root"
if ($LASTEXITCODE -ne 0) { throw "the wheel of the program could not be built" }
$wheel = (Get-ChildItem $wheels -Filter "spc-$version-*.whl" | Select-Object -First 1).FullName
Invoke-BuildPython -m pip install --no-warn-script-location --no-deps --target $site $wheel
if ($LASTEXITCODE -ne 0) { throw "pip install of the program failed" }
# the IQ of step 4 checks that every package the program needs is there in the required version

# 3. the manifest of the release (SHA-256 of every file, locked package versions)
$py = Join-Path $app "python\python.exe"
& $py -m spc.qualification manifest --root (Join-Path $app "site-packages\spc")
if ($LASTEXITCODE -ne 0) { throw "the manifest could not be written" }

# 4. the self-test: IQ and OQ of the built tree
$qual = Join-Path $build "qualification"
& $py -m spc.qualification all --out $qual --require-manifest
if ($LASTEXITCODE -ne 0) { throw "The qualification of the build failed. See $qual. Build stopped." }

# 5. launchers and scripts, then the installer
Copy-Item -Recurse (Join-Path $PSScriptRoot "bin") (Join-Path $app "bin")
Copy-Item (Join-Path $PSScriptRoot "register-task.ps1"), (Join-Path $PSScriptRoot "unregister-task.ps1") $app
Copy-Item (Join-Path $root "docs\INSTALLATION.md") (Join-Path $app "INSTALLATION.md")
if (-not (Test-Path $Iscc)) { throw "Inno Setup 6 was not found at $Iscc. Install it or give -Iscc." }
& $Iscc "/DAppVersion=$version" "/DSourceDir=$app" "/O$OutDir" (Join-Path $PSScriptRoot "spc.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

# 6. the digest of the installer and the release manifest, for the delivery note
$exe = Join-Path $OutDir "SPC-Setup-$version.exe"
$sha = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
$manifest = Get-Content (Join-Path $app "site-packages\spc\RELEASE.json") -Raw | ConvertFrom-Json
@("SPC-Setup-$version.exe  sha256 $sha", "release manifest digest $($manifest.digest)", "built-in qualification record: see qualification\") | Set-Content (Join-Path $OutDir "SPC-Setup-$version.txt")
Copy-Item -Recurse $qual (Join-Path $OutDir "build-qualification-$version") -Force
Write-Host "Done: $exe`nsha256 $sha`nrelease manifest $($manifest.digest)"
