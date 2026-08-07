<#
.SYNOPSIS
    One-shot setup of a SPEADE machine: installs the four system tools at their
    pinned versions, machine-wide, and verifies the result.

.DESCRIPTION
    SPEADE ships as a folder + four separately-installed tools (see
    docs/tech-stack.md for why they are not bundled). This script performs the
    whole of docs/runbook.md sections 2-5 in one run, and is written for the
    deployment realities of a managed, SHARED university PC:

      * every tool lands MACHINE-WIDE, on the system PATH, so a second Windows
        login on the same PC finds them too (the per-user install trap);
      * it is re-runnable -- anything already present is reported and skipped;
      * it verifies each tool by actually invoking it, then prints one verdict.

    It does NOT install SPEADE itself (copy the signed speade-desktop folder),
    does NOT need the internet if you pass -OfflineDir with archived installers,
    and changes nothing else on the machine.

.PARAMETER OfflineDir
    Folder holding archived installers for an offline build-out. Recognised
    names (any version suffix): OpenJDK*.msi, tesseract-ocr-*.exe,
    verapdf-installer*.zip, opendataloader-pdf*.whl. Without this, winget and
    the network are used.

.PARAMETER VeraPdfHome
    Where veraPDF is installed. Default C:\Program Files\verapdf.

.PARAMETER SkipVerify
    Install only; do not run the verification pass.

.EXAMPLE
    # normal, networked machine (run as Administrator)
    .\setup-machine.ps1

.EXAMPLE
    # air-gapped build-out from the archived installer set
    .\setup-machine.ps1 -OfflineDir D:\speade-installers

.NOTES
    Run as Administrator. Pinned versions are the frozen-v1 set recorded in
    docs/tech-stack.md; change them there and here together, never separately.
#>

[CmdletBinding()]
param(
    [string] $OfflineDir,
    [string] $VeraPdfHome = "C:\Program Files\verapdf",
    [switch] $SkipVerify
)

$ErrorActionPreference = "Stop"

# --- the frozen v1 ship set (docs/tech-stack.md) -----------------------------
$PINNED = @{
    Java           = "11.0.29"   # Temurin JRE; the tested set
    Tesseract      = "5.5.0"
    VeraPdf        = "1.30.2"
    OpenDataLoader = "2.5.0"
}

$script:Failures = @()
$script:Notes = @()

function Write-Step([string] $Text) {
    Write-Host ""
    Write-Host "== $Text" -ForegroundColor Cyan
}

function Write-Ok([string] $Text) { Write-Host "   [ok]   $Text" -ForegroundColor Green }
function Write-Skip([string] $Text) { Write-Host "   [have] $Text" -ForegroundColor DarkGray }

function Write-Fail([string] $Text) {
    Write-Host "   [FAIL] $Text" -ForegroundColor Red
    $script:Failures += $Text
}

function Write-Note([string] $Text) {
    Write-Host "   [note] $Text" -ForegroundColor Yellow
    $script:Notes += $Text
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Update-SessionPath {
    # a tool installed a moment ago is not on THIS shell's PATH until we re-read
    # the machine + user values from the registry
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = ($machine, $user | Where-Object { $_ }) -join ";"
}

function Add-MachinePath([string] $Folder) {
    <# Put a folder on the SYSTEM path (so every login on a shared PC sees it). #>
    $current = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $parts = $current -split ";" | Where-Object { $_ }
    if ($parts -contains $Folder) {
        Write-Skip "already on the system PATH: $Folder"
        return
    }
    [Environment]::SetEnvironmentVariable("Path", ($current.TrimEnd(";") + ";" + $Folder), "Machine")
    Update-SessionPath
    Write-Ok "added to the system PATH: $Folder"
}

function Invoke-Tool([string] $Exe, [string[]] $ToolArgs) {
    <# Run a native tool, returning its merged output and exit code. java
       -version prints to STDERR, and under $ErrorActionPreference = "Stop"
       Windows PowerShell wraps redirected stderr lines in ErrorRecords and
       throws on the FIRST one -- a healthy tool then reads as a failed launch.
       Relax the preference around the call; a genuine launch failure (blocked
       or missing exe) still throws for the caller to catch. #>
    $eap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # capture, THEN read $LASTEXITCODE: piping into Select-Object -First
        # short-circuits the pipeline and leaves the exit code stale/unset.
        $out = & $Exe @ToolArgs 2>&1
        return @{ Output = $out; ExitCode = $LASTEXITCODE }
    } finally {
        $ErrorActionPreference = $eap
    }
}

function Find-Offline([string] $Pattern) {
    if (-not $OfflineDir) { return $null }
    $hit = Get-ChildItem -Path $OfflineDir -Filter $Pattern -File -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($hit) { return $hit.FullName }
    return $null
}

function Invoke-Winget([string] $Id, [string] $Label) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Fail "$Label needs winget, which is not available. Install it, or re-run with -OfflineDir."
        return $false
    }
    # --scope machine keeps it out of one user's profile (the shared-PC trap),
    # but not every package offers a machine installer -- fall back rather than
    # leave the tool uninstalled.
    Write-Host "   installing $Label via winget ($Id), machine scope..."
    winget install --id $Id --scope machine --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Note "$Label has no machine-scope installer; retrying without --scope."
        winget install --id $Id --silent --accept-package-agreements --accept-source-agreements
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "$Label install returned exit code $LASTEXITCODE."
        return $false
    }
    Update-SessionPath
    return $true
}

# --- preflight ---------------------------------------------------------------
Write-Host ""
Write-Host "SPEADE machine setup - the four system tools" -ForegroundColor White
Write-Host "Pinned set: Java $($PINNED.Java) | Tesseract $($PINNED.Tesseract) | veraPDF $($PINNED.VeraPdf) | OpenDataLoader $($PINNED.OpenDataLoader)"
if ($OfflineDir) { Write-Host "Offline installers: $OfflineDir" }

if (-not (Test-Admin)) {
    Write-Host ""
    Write-Host "This script must run as Administrator (machine-wide installs + system PATH)." -ForegroundColor Red
    Write-Host "Right-click PowerShell -> Run as administrator, then re-run it." -ForegroundColor Red
    exit 1
}
if ($OfflineDir -and -not (Test-Path $OfflineDir)) {
    Write-Host "OfflineDir not found: $OfflineDir" -ForegroundColor Red
    exit 1
}

# --- 1. Java (hosts BOTH Java tools) ----------------------------------------
Write-Step "1/4  Java runtime (Temurin JRE $($PINNED.Java))"
if (Get-Command java -ErrorAction SilentlyContinue) {
    try {
        $ver = (Invoke-Tool "java" @("-version")).Output | Select-Object -First 1
        Write-Skip "java already present: $ver"
    } catch {
        # on the PATH but not launchable (e.g. App Control) -- keep going and
        # let the verification pass report it as the failure it is
        Write-Note "java is on the PATH but did not launch ($($_.Exception.Message.Trim())); see the verification below."
    }
} else {
    $msi = Find-Offline "OpenJDK*.msi"
    if ($msi) {
        Write-Host "   installing from $msi ..."
        # ADDLOCAL: install the launcher AND register it on the system PATH
        $args = @("/i", "`"$msi`"", "/quiet", "/norestart",
                  "ADDLOCAL=FeatureMain,FeatureEnvironment,FeatureJavaHome")
        $p = Start-Process msiexec.exe -ArgumentList $args -Wait -PassThru
        if ($p.ExitCode -ne 0) { Write-Fail "Java MSI returned exit code $($p.ExitCode)." }
        Update-SessionPath
    } else {
        Invoke-Winget "EclipseAdoptium.Temurin.11.JRE" "Temurin JRE 11" | Out-Null
        Write-Note "winget installed the latest Temurin 11 JRE; the tested pin is $($PINNED.Java). For an exact reproducible build, install from the archived 11.0.29 installer (use -OfflineDir) instead."
    }
    if (Get-Command java -ErrorAction SilentlyContinue) { Write-Ok "java installed" }
}

# --- 2. Tesseract (OCR) ------------------------------------------------------
Write-Step "2/4  Tesseract OCR $($PINNED.Tesseract)"
$tessDefault = "C:\Program Files\Tesseract-OCR"
if (Get-Command tesseract -ErrorAction SilentlyContinue) {
    Write-Skip "tesseract already on PATH"
} elseif (Test-Path (Join-Path $tessDefault "tesseract.exe")) {
    Write-Skip "tesseract present at $tessDefault (SPEADE probes this location)"
    Add-MachinePath $tessDefault
} else {
    $exe = Find-Offline "tesseract-ocr-*.exe"
    if ($exe) {
        Write-Host "   installing from $exe ..."
        $p = Start-Process $exe -ArgumentList "/S", "/D=$tessDefault" -Wait -PassThru
        if ($p.ExitCode -ne 0) { Write-Fail "Tesseract installer returned exit code $($p.ExitCode)." }
    } else {
        Invoke-Winget "tesseract-ocr.tesseract" "Tesseract" | Out-Null
    }
    if (Test-Path (Join-Path $tessDefault "tesseract.exe")) {
        Add-MachinePath $tessDefault
        Write-Ok "tesseract installed"
    } elseif (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
        Write-Fail "Tesseract not found after install. Scanned PDFs will be flagged 'ocr-unavailable'."
    }
}

# --- 3. veraPDF (the PDF/UA gate) -------------------------------------------
Write-Step "3/4  veraPDF $($PINNED.VeraPdf)"
$veraBat = Join-Path $VeraPdfHome "verapdf.bat"
if (Get-Command verapdf -ErrorAction SilentlyContinue) {
    Write-Skip "verapdf already on PATH"
} elseif (Test-Path $veraBat) {
    Write-Skip "veraPDF present at $VeraPdfHome"
    Add-MachinePath $VeraPdfHome
} else {
    $zip = Find-Offline "verapdf-installer*.zip"
    $work = Join-Path $env:TEMP "speade-verapdf-setup"
    try {
        if (-not $zip) {
            Write-Host "   downloading the veraPDF installer..."
            New-Item -ItemType Directory -Force $work | Out-Null
            $zip = Join-Path $work "verapdf-installer.zip"
            Invoke-WebRequest -Uri "https://software.verapdf.org/releases/verapdf-installer.zip" `
                -OutFile $zip -UseBasicParsing
        }
        New-Item -ItemType Directory -Force $work | Out-Null
        Expand-Archive -Path $zip -DestinationPath $work -Force
        $jar = Get-ChildItem -Path $work -Filter "verapdf-izpack-installer*.jar" -Recurse -File |
            Select-Object -First 1
        if (-not $jar) { throw "no verapdf-izpack-installer*.jar inside the archive" }

        # IzPack unattended-install descriptor (docs/runbook.md section 4)
        $auto = Join-Path $work "auto-install.xml"
        @"
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
    <com.izforge.izpack.panels.htmlhello.HTMLHelloPanel id="welcome"/>
    <com.izforge.izpack.panels.target.TargetPanel id="install_dir">
        <installpath>$VeraPdfHome</installpath>
    </com.izforge.izpack.panels.target.TargetPanel>
    <com.izforge.izpack.panels.packs.PacksPanel id="sdk_pack_select"/>
    <com.izforge.izpack.panels.install.InstallPanel id="install"/>
    <com.izforge.izpack.panels.finish.FinishPanel id="finish"/>
</AutomatedInstallation>
"@ | Set-Content -Path $auto -Encoding utf8

        Write-Host "   running the unattended veraPDF install into $VeraPdfHome ..."
        & java -jar $jar.FullName $auto
        if ($LASTEXITCODE -ne 0) { throw "installer exit code $LASTEXITCODE" }
        Add-MachinePath $VeraPdfHome
        Write-Ok "veraPDF installed"
    } catch {
        Write-Fail "veraPDF install failed: $($_.Exception.Message). The gate fails closed ('verapdf-unavailable') until this is fixed."
    } finally {
        Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    }
}

# --- 4. OpenDataLoader (the tagging engine) ---------------------------------
# The subtle one on a SHARED PC: `uv tool install` puts its launcher in the
# INSTALLING user's profile, so the next login gets "not found on PATH". Install
# it into a machine-wide Python instead, and put that Scripts folder on the
# system PATH.
Write-Step "4/4  OpenDataLoader PDF $($PINNED.OpenDataLoader)  (machine-wide)"
$odlHome = "C:\Program Files\SPEADE\opendataloader"   # a dedicated venv
$odlBin = "C:\Program Files\SPEADE\bin"               # holds the .cmd shim
$existing = Get-Command opendataloader-pdf -ErrorAction SilentlyContinue
if ($existing -and $existing.Source -notlike "$env:USERPROFILE*") {
    Write-Skip "opendataloader-pdf already on PATH: $($existing.Source)"
} else {
    if ($existing) {
        Write-Note "opendataloader-pdf found at $($existing.Source) -- that is inside ONE user's profile, so other logins on this PC would not see it. Installing a machine-wide copy as well."
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    $python = Get-Command python -ErrorAction SilentlyContinue
    $bootstrap = if ($py) { @("py", @("-3")) } elseif ($python) { @("python", @()) } else { $null }
    if (-not $bootstrap) {
        Write-Fail "No machine-wide Python found. Install Python 3 for all users (winget install Python.Python.3.13 --scope machine), then re-run."
    } else {
        try {
            $exeName = $bootstrap[0]
            $baseArgs = $bootstrap[1]
            $wheel = Find-Offline "opendataloader_pdf*.whl"
            $target = if ($wheel) { $wheel } else { "opendataloader-pdf==$($PINNED.OpenDataLoader)" }

            # an isolated venv: never pollutes the system Python's site-packages
            if (-not (Test-Path (Join-Path $odlHome "Scripts\python.exe"))) {
                Write-Host "   creating the tool environment in $odlHome ..."
                & $exeName @($baseArgs + @("-m", "venv", $odlHome))
                if ($LASTEXITCODE -ne 0) { throw "venv creation exit code $LASTEXITCODE" }
            }
            $venvPy = Join-Path $odlHome "Scripts\python.exe"
            Write-Host "   installing $target ..."
            & $venvPy @("-m", "pip", "install", "--quiet", "--upgrade", $target)
            if ($LASTEXITCODE -ne 0) { throw "pip exit code $LASTEXITCODE" }

            # The shim calls `python -m`, NOT the generated .exe launcher: those
            # launchers are exactly what App Control blocks on managed machines
            # (docs/runbook.md). Verified: opendataloader_pdf ships a __main__.
            New-Item -ItemType Directory -Force $odlBin | Out-Null
            @"
@echo off
rem SPEADE machine-wide launcher for the tagging engine (see scripts/setup-machine.ps1)
"$venvPy" -m opendataloader_pdf %*
"@ | Set-Content -Path (Join-Path $odlBin "opendataloader-pdf.cmd") -Encoding ascii
            Add-MachinePath $odlBin
            Write-Ok "opendataloader-pdf installed machine-wide"
        } catch {
            Write-Fail "OpenDataLoader install failed: $($_.Exception.Message). Born-digital tagging will not run."
        }
    }
}

# --- verification ------------------------------------------------------------
if (-not $SkipVerify) {
    Write-Step "Verifying (actually invoking each tool)"
    Update-SessionPath
    # Probes are per-tool because they differ: OpenDataLoader has NO --version
    # (it exits 2 with a usage message), so --help is the honest check. Fallback
    # paths mirror how SPEADE itself discovers a tool, so this pass agrees with
    # what the app will find at runtime -- no false alarms, no false comfort.
    $checks = @(
        @{ Name = "Java"; Cmd = "java"; Args = @("-version"); Fallback = $null
           Effect = "the two Java tools cannot run at all" },
        @{ Name = "Tesseract"; Cmd = "tesseract"; Args = @("--version")
           Fallback = "C:\Program Files\Tesseract-OCR\tesseract.exe"
           Effect = "scanned PDFs are flagged 'ocr-unavailable'" },
        @{ Name = "veraPDF"; Cmd = "verapdf"; Args = @("--version")
           Fallback = (Join-Path $VeraPdfHome "verapdf.bat")
           Effect = "the automatic check fails closed ('verapdf-unavailable')" },
        @{ Name = "OpenDataLoader"; Cmd = "opendataloader-pdf"; Args = @("--help")
           Fallback = (Join-Path $odlBin "opendataloader-pdf.cmd")
           Effect = "born-digital documents are not tagged" }
    )
    foreach ($c in $checks) {
        $resolved = Get-Command $c.Cmd -ErrorAction SilentlyContinue
        $exe = if ($resolved) { $c.Cmd } elseif ($c.Fallback -and (Test-Path $c.Fallback)) { $c.Fallback } else { $null }
        if (-not $exe) {
            Write-Fail "$($c.Name): not found -- $($c.Effect)."
            continue
        }
        try {
            $probe = Invoke-Tool $exe $c.Args
        } catch {
            # e.g. "An Application Control policy has blocked this file"
            Write-Fail "$($c.Name): could not be launched ($($_.Exception.Message.Trim())) -- $($c.Effect)."
            continue
        }
        if ($probe.ExitCode -ne 0) {
            Write-Fail "$($c.Name): found but exited $($probe.ExitCode) -- $($c.Effect)."
            continue
        }
        if (-not $resolved) {
            Write-Note "$($c.Name) is not on the PATH but SPEADE probes its default location, so it will work."
        }
        $first = ($probe.Output | Select-Object -First 1)
        Write-Ok "$($c.Name): $(("$first" -replace '\s+', ' ').Trim())"
    }
}

# --- verdict -----------------------------------------------------------------
Write-Host ""
if ($script:Failures.Count -eq 0) {
    Write-Host "All four tools are installed and runnable on this machine." -ForegroundColor Green
    Write-Host "Next: copy the signed speade-desktop\ folder over, launch it, and process one PDF."
} else {
    Write-Host "$($script:Failures.Count) problem(s) need attention:" -ForegroundColor Red
    $script:Failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host ""
    Write-Host "SPEADE still runs with a tool missing: affected documents are flagged"
    Write-Host "in the review list rather than crashing the batch. See docs/runbook.md."
}
if ($script:Notes.Count) {
    Write-Host ""
    Write-Host "Notes:" -ForegroundColor Yellow
    $script:Notes | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
}
Write-Host ""
Write-Host "PATH changes apply to NEW processes: open a fresh terminal (or have the"
Write-Host "user log out and back in) before launching SPEADE."
Write-Host ""

exit ([Math]::Min($script:Failures.Count, 1))
