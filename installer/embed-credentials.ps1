<#
    embed-credentials.ps1 - bake the shared credentials into the installer.

        powershell -ExecutionPolicy Bypass -File installer\embed-credentials.ps1 -FromEnv
        powershell -ExecutionPolicy Bypass -File installer\build.ps1 -WithCredentials

    Produces installer\credentials.dat, which build.ps1 -WithCredentials compiles
    into SLC-Smart-Parking-Campus-Setup.exe - the same filename a plain build
    no credential entry at all: it writes backend\.env itself and the launcher
    comes up ready to start.

    ===========================================================================
    READ THIS BEFORE USING IT

    The file is OBFUSCATED, NOT ENCRYPTED. The key that unscrambles it is in the
    installer, because the installer has to open it with nobody present to type
    anything. That is not a weakness in the cipher - it is what "works for
    anyone I give it to, with no prompts" means. Any recipient can recover:

        DATABASE_URL   full read/write on the live Neon database - every
                       student, employee, vehicle, violation and access log
        SECRET_KEY     signs the JWTs BOTH halves accept, so it can mint a
                       valid admin/CDSO token for any account
        R2_*           read/write on the evidence-photo bucket

    The scrambling stops `strings setup.exe` and a curious glance. It stops
    nothing else. Treat a credential-carrying installer as exactly as sensitive as
    the credentials themselves: do not email it, do not put it on a shared
    drive, do not upload it anywhere, and do not hand it to anyone you would not
    give direct database access.

    IF ONE LEAKS: rotate DATABASE_URL and the R2 keys, and change SECRET_KEY on
    Railway. Changing SECRET_KEY signs everyone out of both halves at once, so
    do it deliberately - but do it.

    The plain build.ps1 (no -WithCredentials) makes a clean installer that
    carries none of this and asks for the values on first launch. That is the
    one to use for anything you cannot personally hand to a person.
    ===========================================================================
#>

[CmdletBinding()]
param(
    # Read the values out of backend\.env instead of asking for them. This is
    # the usual case: the machine building the installer is normally one that
    # already has a working campus .env.
    [switch]$FromEnv,
    [string]$EnvFile,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$repo = Split-Path -Parent $here

# One definition of which keys matter, shared with the launcher and run-campus.
. (Join-Path $repo 'scripts\campus-config.ps1')

$outFile = Join-Path $here 'credentials.dat'
if (-not $EnvFile) { $EnvFile = Join-Path $repo 'backend\.env' }

function Say($m, $c = 'Cyan') { Write-Host "[credentials] $m" -ForegroundColor $c }

# ---------------------------------------------------------------------------
#  The scrambling
# ---------------------------------------------------------------------------
# Deliberately simple and deliberately documented. AES is here so the blob is
# not readable with a hex editor; the key sits three lines below it, so this is
# a speed bump and is described as one everywhere it appears. Using something
# stronger would only make the label more misleading.
#
# Duplicated in bootstrap.ps1 rather than shared: the bootstrap ships inside the
# installer and runs before the repository exists, so it cannot dot-source
# anything from scripts\.
function Get-ObfuscationKey {
    $seed = 'SLC-VMS::campus-installer::v1'
    return [System.Security.Cryptography.SHA256]::Create().ComputeHash(
        [System.Text.Encoding]::UTF8.GetBytes($seed))
}

function Protect-Blob {
    param([string]$PlainText)
    $aes = [System.Security.Cryptography.Aes]::Create()
    $aes.Key = Get-ObfuscationKey
    $aes.GenerateIV()
    $enc = $aes.CreateEncryptor()
    $plain = [System.Text.Encoding]::UTF8.GetBytes($PlainText)
    $cipher = $enc.TransformFinalBlock($plain, 0, $plain.Length)
    # IV first, so the reader can pick it back off the front.
    $out = New-Object byte[] ($aes.IV.Length + $cipher.Length)
    [Array]::Copy($aes.IV, 0, $out, 0, $aes.IV.Length)
    [Array]::Copy($cipher, 0, $out, $aes.IV.Length, $cipher.Length)
    $enc.Dispose(); $aes.Dispose()
    return ,$out
}

# ---------------------------------------------------------------------------
#  Collect the values
# ---------------------------------------------------------------------------
$spec   = Get-CampusSecretSpec
$values = [ordered]@{}

if ($FromEnv) {
    if (-not (Test-Path $EnvFile)) { Write-Error "No .env at $EnvFile - pass -EnvFile, or drop -FromEnv to be asked instead." }
    Say "Reading from $EnvFile"
    $map = Get-CampusEnvMap $EnvFile
    foreach ($s in $spec) {
        $v = if ($map.ContainsKey($s.Key)) { $map[$s.Key] } else { '' }
        if (Test-CampusPlaceholder $v) {
            if ($s.Required) { Write-Error "$($s.Key) is not set in $EnvFile - the installer would carry a placeholder." }
            continue
        }
        $values[$s.Key] = $v
    }
} else {
    Say 'Enter the values to bake in. Press Enter to skip an optional one.' 'Yellow'
    foreach ($s in $spec) {
        $entered = Read-Host "  $($s.Prompt)"
        if ($entered -and $entered.Trim()) {
            $values[$s.Key] = $entered.Trim()
        } elseif ($s.Required) {
            Write-Error "$($s.Key) is required."
        }
    }
}

$missing = @($spec | Where-Object { $_.Required -and -not $values.Contains($_.Key) })
if ($missing.Count) {
    Write-Error ("Missing required values: " + (($missing | ForEach-Object { $_.Key }) -join ', '))
}

# ---------------------------------------------------------------------------
#  Confirm, then write
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '  About to bake these into installer\credentials.dat:' -ForegroundColor White
foreach ($k in $values.Keys) {
    $v = $values[$k]
    # Never echo a secret in full - a build log or a shoulder is enough to lose it.
    $shown = if ($k -match 'SECRET|KEY|DATABASE|PASSWORD') {
        '(' + $v.Length + ' chars, ending ...' + $v.Substring([Math]::Max(0, $v.Length - 4)) + ')'
    } else { $v }
    Write-Host ("    {0,-24} {1}" -f $k, $shown) -ForegroundColor DarkGray
}
Write-Host ''
Write-Host '  Anyone who receives the resulting installer can recover all of the above.' -ForegroundColor Red
Write-Host '  It is not encrypted against them - the key ships with it. Hand it only to' -ForegroundColor Red
Write-Host '  people you would give direct database access.' -ForegroundColor Red
Write-Host ''

if (-not $Force) {
    $ok = Read-Host '  Type EMBED to continue'
    if ($ok -ne 'EMBED') { Say 'Cancelled - nothing written.' 'Yellow'; exit 1 }
}

$lines = foreach ($k in $values.Keys) { "$k=$($values[$k])" }
$blob  = Protect-Blob ($lines -join "`n")
[System.IO.File]::WriteAllBytes($outFile, $blob)

# Belt and braces on top of .gitignore. The repository is public; a credentials
# file reaching it would publish the production database.
if (-not (git -C $repo check-ignore installer/credentials.dat 2>$null)) {
    Write-Host ''
    Write-Host '  WARNING: installer\credentials.dat is NOT gitignored. Fix that before' -ForegroundColor Red
    Write-Host '  committing anything - this repository is public.' -ForegroundColor Red
}

Write-Host ''
Say "Wrote $outFile ($($blob.Length) bytes, $($values.Count) values)" 'Green'
Say 'Next: installer\build.ps1 -WithCredentials' 'Green'
Write-Host ''
Write-Host '  Delete credentials.dat when you are done building - it is as sensitive' -ForegroundColor DarkGray
Write-Host '  as the installer it produces.' -ForegroundColor DarkGray
Write-Host ''
