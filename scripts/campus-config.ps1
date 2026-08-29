<#
    campus-config.ps1 - the one definition of "what the campus half needs to know".

    Dot-sourced by run-campus.ps1 (the console path), campus-launcher.ps1 (the
    GUI) and the installer's bootstrap. Before this file existed the list of
    secrets and the .env read/write helpers were inlined in run-campus.ps1, so
    anything else that wanted them had to copy them - and a copy is how two
    entry points end up disagreeing about which keys matter.

    Nothing here has side effects. Dot-source it and call what you need.
#>

# The values a machine cannot work out for itself.
#
# Required: without it this half is not part of the shared system at all.
#   SECRET_KEY   signs the JWTs BOTH halves accept - a mismatch means a token
#                minted by one is rejected by the other.
#   DATABASE_URL is what makes the two halves one system rather than two.
#   R2_*         the same bucket, so evidence photos uploaded at the gate are
#                the ones the Railway URL serves.
#
# Optional: the campus half deliberately differs from Railway on email. Railway
# blocks outbound SMTP and goes through Brevo over HTTPS; this machine has
# unrestricted egress, so plain Gmail works. Leaving these blank is legitimate -
# the server still runs, it just cannot send mail from here.
$script:CampusSecretSpec = @(
    @{ Key = 'SECRET_KEY';           Label = 'Django secret key';  Required = $true;  Hidden = $true
       Prompt = "Django SECRET_KEY (byte-identical to Railway's)"
       Help   = "Copy from Railway. A different key here rejects logins issued there." },

    @{ Key = 'DATABASE_URL';         Label = 'Neon database URL';  Required = $true;  Hidden = $true
       Prompt = 'Neon DATABASE_URL (the same one Railway uses)'
       Help   = 'The same Neon connection string Railway uses. This is what shares the data.' },

    @{ Key = 'R2_ACCESS_KEY_ID';     Label = 'R2 access key id';   Required = $true;  Hidden = $false
       Prompt = 'Cloudflare R2 access key id'
       Help   = 'Cloudflare R2 - the same bucket Railway uses.' },

    @{ Key = 'R2_SECRET_ACCESS_KEY'; Label = 'R2 secret key';      Required = $true;  Hidden = $true
       Prompt = 'Cloudflare R2 secret access key'
       Help   = '' },

    @{ Key = 'R2_BUCKET_NAME';       Label = 'R2 bucket name';     Required = $true;  Hidden = $false
       Prompt = 'R2 bucket name'
       Help   = '' },

    @{ Key = 'R2_ACCOUNT_ID';        Label = 'R2 account id';      Required = $true;  Hidden = $false
       Prompt = 'R2 account id'
       Help   = '' },

    @{ Key = 'R2_PUBLIC_URL';        Label = 'R2 public URL';      Required = $true;  Hidden = $false
       Prompt = 'R2 public URL'
       Help   = '' },

    @{ Key = 'EMAIL_HOST_USER';      Label = 'Gmail address';      Required = $false; Hidden = $false
       Prompt = 'Gmail address this machine sends from (optional)'
       Help   = 'Optional. The same address Railway sends from, so students see one sender.' },

    @{ Key = 'EMAIL_HOST_PASSWORD';  Label = 'Gmail app password'; Required = $false; Hidden = $true
       Prompt = 'Its 16-character Gmail App Password (optional)'
       Help   = 'Optional. A Gmail App Password, not the account password.' }
)

function Get-CampusSecretSpec { return $script:CampusSecretSpec }

# A value still carrying a template placeholder counts as unset. The campus
# template ships angle-bracket placeholders and phrases like "same as Railway",
# and treating those as real values is how a machine starts up with a
# SECRET_KEY of literally "<paste the same SECRET_KEY used on Railway>".
function Test-CampusPlaceholder {
    param([string]$Value)
    if ($null -eq $Value) { return $true }
    $v = $Value.Trim()
    return ($v -eq '') -or ($v -match '^<.*>$') -or ($v -match 'same as Railway|CHANGE|your-|paste')
}

function Get-CampusEnvValue {
    param([Parameter(Mandatory=$true)][string]$EnvFile, [Parameter(Mandatory=$true)][string]$Key)
    if (-not (Test-Path $EnvFile)) { return '' }
    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
        if ($line -like "$Key=*") { return $line.Substring($Key.Length + 1).Trim() }
    }
    return ''
}

# Every key in one pass. Callers that want more than one value should use this:
# asking Get-CampusEnvValue nine times re-reads and re-scans the file nine
# times, which is what the credentials panel and the missing-secret check were
# both doing on every open.
function Get-CampusEnvMap {
    param([Parameter(Mandatory=$true)][string]$EnvFile)
    $map = @{}
    if (-not (Test-Path $EnvFile)) { return $map }
    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
        # Comments and blanks are the bulk of the template; skip before the
        # split so the common line costs one character comparison.
        if ($line.Length -eq 0) { continue }
        $c = $line[0]
        if ($c -eq '#' -or $c -eq ' ' -or $c -eq "`t") { continue }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { continue }
        $map[$line.Substring(0, $eq)] = $line.Substring($eq + 1).Trim()
    }
    return $map
}

# Written with a plain prefix match and an explicit rewrite rather than
# PowerShell's -replace. In a -replace replacement string, "$1" and "$&" are
# capture references: a Django SECRET_KEY or a Neon password containing a
# dollar sign would silently come back mangled. Nothing here interprets the
# value at all.
#
# No BOM: python-dotenv opens .env as plain utf-8, so a BOM would become part
# of the first key's name. Set-Content -Encoding utf8 emits one on PS 5.1.
function Set-CampusEnvValue {
    param(
        [Parameter(Mandatory=$true)][string]$EnvFile,
        [Parameter(Mandatory=$true)][string]$Key,
        [Parameter(Mandatory=$true)][AllowEmptyString()][string]$Value
    )
    $lines = @()
    $found = $false
    if (Test-Path $EnvFile) { $lines = @([System.IO.File]::ReadAllLines($EnvFile)) }

    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -like "$Key=*") {
            $lines[$i] = "$Key=$Value"
            $found = $true
            break
        }
    }
    if (-not $found) { $lines += "$Key=$Value" }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($EnvFile, $lines, $utf8NoBom)
}

# The spec entries that still need answering. -RequiredOnly is what gates "can
# this machine serve at all"; the full list is what the installer asks about,
# optional entries included.
function Get-CampusMissingSecrets {
    param([Parameter(Mandatory=$true)][string]$EnvFile, [switch]$RequiredOnly)
    $spec = $script:CampusSecretSpec
    if ($RequiredOnly) { $spec = $spec | Where-Object { $_.Required } }
    $map = Get-CampusEnvMap $EnvFile
    return @($spec | Where-Object {
        $v = if ($map.ContainsKey($_.Key)) { $map[$_.Key] } else { '' }
        Test-CampusPlaceholder $v
    })
}

# This machine's LAN address, recomputed on every run rather than stored.
# Preferring 192.168.* is deliberate: the cameras live on 192.168.137.x, and on
# a box with a Hyper-V or WSL adapter the first address returned is often a
# virtual one no guard's browser can reach.
function Get-CampusLanAddress {
    $addr = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
             Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
             Sort-Object -Property @{ Expression = { $_.IPAddress -like '192.168.*' } } -Descending |
             Select-Object -First 1).IPAddress
    if (-not $addr) { return '127.0.0.1' }
    return $addr
}

# The environment both entry points hand to Django. These go through the process
# environment rather than into .env because python-dotenv does not override
# variables already set in the process - so a new DHCP lease never means editing
# a file. Returns the origin it settled on.
function Set-CampusRuntimeEnvironment {
    param([Parameter(Mandatory=$true)][string]$Lan, [Parameter(Mandatory=$true)][int]$Port)
    $origin = "http://${Lan}:$Port"
    $env:ALLOWED_HOSTS        = "localhost,127.0.0.1,$Lan"
    $env:FRONTEND_URL         = $origin
    $env:BACKEND_URL          = $origin
    $env:CSRF_TRUSTED_ORIGINS = $origin
    $env:SECURE_SSL_REDIRECT  = 'false'   # plain HTTP on the LAN; the redirect would loop
    $env:RUN_MIGRATIONS       = 'false'   # Railway owns the schema for the shared DB
    $env:PYTHONUNBUFFERED     = '1'       # so a parent reading our stdout sees lines as they happen
    return $origin
}

# Launcher preferences, kept out of the checkout so a `git pull` or a
# reinstall never resets them. The branch lives here rather than being read
# from the checkout's current HEAD: the campus box should track one named
# branch on purpose, not whatever someone last left it on.
function Get-CampusLauncherConfigPath {
    return (Join-Path $env:LOCALAPPDATA 'SLC-VMS\launcher.json')
}

# Bump when a key's MEANING changes, not when one is added. A new key with a
# default needs no migration - the merge below supplies it. This exists for the
# case that merge cannot handle: a value that has to be rewritten, not filled in.
$script:CampusConfigVersion = 1

# Rewrites settings written by an older layout. Runs before the defaults merge,
# on the raw object read from disk.
#
#   (none) -> 1  The first versioned schema. Kiosk and OpenOnStart were added
#                after the first release; both take defaults, so there is
#                nothing to rewrite - this only stamps the version so a future
#                migration knows where it is starting from.
function Convert-CampusLauncherConfig {
    param($Raw)
    $from = 0
    if ($Raw.PSObject.Properties['ConfigVersion']) { $from = [int]$Raw.ConfigVersion }
    if ($from -ge $script:CampusConfigVersion) { return $Raw }

    # if ($from -lt 2) { ...rewrite here when there is a version 2... }

    return $Raw
}

function Get-CampusLauncherConfig {
    $defaults = [ordered]@{
        ConfigVersion     = $script:CampusConfigVersion
        # Only a fallback for a checkout with no launcher.json - a real install
        # gets its branch from the installer, which fixes it at build time.
        # main because it is the branch that only receives merged work.
        Branch            = 'main'
        Port              = 8000
        UpdatePollMinutes = 5
        AutoStart         = $true
        # Kiosk is the default because the usual machine for this is a terminal
        # bolted to a gate house desk. A guard should get the scanner and
        # nothing else - no address bar to mistype, no tabs to lose it behind.
        Kiosk             = $true
        # Which page to open by itself once the server is up: 'none', 'guard'
        # or 'admin'. A gate terminal wants 'guard'; a machine someone also
        # does paperwork on wants 'none'.
        OpenOnStart       = 'none'
        # Directories the installer found Python, Node and Git in. Windows only
        # publishes a machine PATH change to processes started after it, and the
        # launcher is often started from an Explorer that has been running since
        # before the install - so the paths are recorded rather than trusted to
        # PATH having caught up.
        PathPrepend       = @()
    }
    $path = Get-CampusLauncherConfigPath
    if (Test-Path $path) {
        try {
            $saved = Convert-CampusLauncherConfig (Get-Content $path -Raw | ConvertFrom-Json)
            foreach ($k in @($defaults.Keys)) {
                if ($null -ne $saved.PSObject.Properties[$k]) { $defaults[$k] = $saved.$k }
            }
            # Always the current version on the way out, whatever was on disk.
            $defaults['ConfigVersion'] = $script:CampusConfigVersion
        } catch {
            # A corrupt config must not stop the gate terminal from starting.
            # Defaults are always serviceable, so fall through to them.
        }
    }
    return [pscustomobject]$defaults
}

# Prepends the recorded tool directories to this process's PATH, skipping any
# that are already there so a long-lived launcher does not grow the variable
# every time this is called.
function Add-CampusToolPaths {
    param([Parameter(Mandatory=$true)]$Config)
    if (-not $Config.PathPrepend) { return }
    $existing = @($env:PATH -split ';' | Where-Object { $_ })
    $add = @()
    foreach ($p in @($Config.PathPrepend)) {
        if ($p -and (Test-Path $p) -and ($existing -notcontains $p)) { $add += $p }
    }
    if ($add.Count) { $env:PATH = ($add -join ';') + ';' + $env:PATH }
}

function Save-CampusLauncherConfig {
    param([Parameter(Mandatory=$true)]$Config)
    $path = Get-CampusLauncherConfigPath
    $dir  = Split-Path -Parent $path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $Config | ConvertTo-Json | Set-Content -Path $path -Encoding UTF8
}
