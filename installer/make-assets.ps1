<#
    make-assets.ps1 - generates the installer artwork from the app's own logo.

    The icon and the two wizard bitmaps are build outputs, not hand-drawn
    files: they are derived from frontend\src\assets\slclogo.jpg and the brand
    navy the web app already uses (#03396C, from styles\slc-header.css). Change
    the logo there and the installer follows on the next build, instead of
    quietly shipping last year's mark.

    Produces, in installer\assets:
        slc-vms.ico       16/32/48/64/128/256, PNG-compressed frames
        wizard-large.bmp  164 x 314, the left panel of the wizard
        wizard-small.bmp  55 x 55, the header badge
        slclogo.jpg       a copy, for the bootstrap window at install time
#>

[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$repo    = Split-Path -Parent $PSScriptRoot
$srcLogo = Join-Path $repo 'frontend\src\assets\slclogo.jpg'
$outDir  = Join-Path $PSScriptRoot 'assets'

if (-not (Test-Path $srcLogo)) { throw "Cannot find the source logo at $srcLogo" }
if (-not (Test-Path $outDir))  { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

# The brand sweep from frontend\src\index.css (--brand-gradient):
# #03396C -> #0B5C9C -> #1072B3. The wizard panel runs the same gradient the
# web app's headers do, so the installer and the site read as one thing.
$Navy = [System.Drawing.Color]::FromArgb(3, 57, 108)      # --navy   #03396C
$Mid  = [System.Drawing.Color]::FromArgb(11, 92, 156)     # --navy-500 #0B5C9C
$Blue = [System.Drawing.Color]::FromArgb(16, 114, 179)    # --blue   #1072B3
$SubTint = [System.Drawing.Color]::FromArgb(207, 227, 245) # the header subtitle tint
$Edge = [System.Drawing.Color]::FromArgb(189, 212, 229)   # --sky    #BDD4E5

$logo = [System.Drawing.Image]::FromFile($srcLogo)

function New-Canvas([int]$W, [int]$H) {
    $bmp = New-Object System.Drawing.Bitmap($W, $H, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode     = 'AntiAlias'
    $g.InterpolationMode = 'HighQualityBicubic'
    $g.PixelOffsetMode   = 'HighQuality'
    return @{ Bitmap = $bmp; G = $g }
}

# The logo is square-ish but not square, and stretching a school seal to fit a
# circle looks exactly as bad as it sounds. Crop to the centre square first.
function Draw-Mark {
    param($G, [float]$X, [float]$Y, [float]$Size, [bool]$Ring = $true)
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddEllipse($X, $Y, $Size, $Size)
    $saved = $G.Save()
    $G.SetClip($path)

    $side = [Math]::Min($logo.Width, $logo.Height)
    $src  = New-Object System.Drawing.Rectangle(
        [int](($logo.Width - $side) / 2), [int](($logo.Height - $side) / 2), $side, $side)
    $dst  = New-Object System.Drawing.RectangleF($X, $Y, $Size, $Size)
    $G.DrawImage($logo, $dst, $src, [System.Drawing.GraphicsUnit]::Pixel)

    $G.Restore($saved)
    if ($Ring) {
        $pen = New-Object System.Drawing.Pen($Edge, [Math]::Max(1.0, $Size / 40))
        $G.DrawEllipse($pen, $X, $Y, $Size, $Size)
        $pen.Dispose()
    }
    $path.Dispose()
}

# ---------------------------------------------------------------------------
#  Icon
# ---------------------------------------------------------------------------
# System.Drawing cannot write a multi-resolution .ico - Icon.Save round-trips
# whatever single frame it was constructed from, so a file made that way looks
# soft on the desktop and blocky in the taskbar. The container is 16 bytes of
# directory entry per frame plus a 6-byte header, so it is written by hand.
#
# Frame encoding is not a free choice. PNG payloads are legal from Vista on and
# the shell reads them, but GDI+ and the Inno compiler both still expect a DIB
# and render a PNG-only icon as noise. So: DIB everywhere, PNG only at 256,
# where the DIB would be a megabyte and every consumer that understands 256px
# icons at all also understands PNG frames.
function ConvertTo-IconDib {
    param($Bitmap)

    $w = $Bitmap.Width; $h = $Bitmap.Height
    $rect = New-Object System.Drawing.Rectangle(0, 0, $w, $h)
    $data = $Bitmap.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::ReadOnly,
                             [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $pixels = New-Object byte[] ($data.Stride * $h)
    [System.Runtime.InteropServices.Marshal]::Copy($data.Scan0, $pixels, 0, $pixels.Length)
    $Bitmap.UnlockBits($data)

    $ms = New-Object System.IO.MemoryStream
    $bw = New-Object System.IO.BinaryWriter($ms)

    # BITMAPINFOHEADER. biHeight is doubled because an icon DIB is the colour
    # bitmap and the AND mask stacked in one image.
    $bw.Write([uint32]40); $bw.Write([int32]$w); $bw.Write([int32]($h * 2))
    $bw.Write([uint16]1);  $bw.Write([uint16]32); $bw.Write([uint32]0)
    $bw.Write([uint32]($w * $h * 4)); $bw.Write([int32]0); $bw.Write([int32]0)
    $bw.Write([uint32]0);  $bw.Write([uint32]0)

    # Bottom-up, which is what a DIB means by row order.
    for ($y = $h - 1; $y -ge 0; $y--) { $bw.Write($pixels, $y * $data.Stride, $w * 4) }

    # AND mask: obsolete for 32bpp, where the alpha channel already carries the
    # shape, but the format still requires the bytes. Zero means "opaque".
    $maskRow = [int][Math]::Ceiling($w / 8.0)
    if ($maskRow % 4 -ne 0) { $maskRow += 4 - ($maskRow % 4) }
    $bw.Write((New-Object byte[] ($maskRow * $h)))

    $bw.Flush()
    $bytes = $ms.ToArray()
    $bw.Close(); $ms.Dispose()
    # The comma matters. A bare `return $bytes` streams the array element by
    # element into the pipeline, and the caller gets an Object[] of boxed bytes
    # that BinaryWriter then encodes as text - which is how the directory ended
    # up describing frames longer than the file that held them.
    return ,$bytes
}

function Write-Ico {
    param([string]$Path, [int[]]$Sizes)

    $frames = @()
    foreach ($s in $Sizes) {
        $c = New-Canvas $s $s
        $c.G.Clear([System.Drawing.Color]::Transparent)

        # A disc of navy behind the mark keeps the seal readable at 16px, where
        # the artwork inside it is only a few pixels across.
        $brush = New-Object System.Drawing.SolidBrush($Navy)
        $c.G.FillEllipse($brush, 0, 0, $s - 1, $s - 1)
        $brush.Dispose()

        $inset = [Math]::Max(1, [int]($s * 0.06))
        Draw-Mark -G $c.G -X $inset -Y $inset -Size ($s - 2 * $inset) -Ring ($s -ge 32)

        if ($s -ge 256) {
            $ms = New-Object System.IO.MemoryStream
            $c.Bitmap.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
            $bytes = $ms.ToArray()
            $ms.Dispose()
        } else {
            $bytes = ConvertTo-IconDib $c.Bitmap
        }
        $frames += ,@{ Size = $s; Bytes = $bytes }
        $c.G.Dispose(); $c.Bitmap.Dispose()
    }

    $fs = [System.IO.File]::Create($Path)
    $bw = New-Object System.IO.BinaryWriter($fs)
    $bw.Write([uint16]0)                 # reserved
    $bw.Write([uint16]1)                 # 1 = icon
    $bw.Write([uint16]$frames.Count)

    $offset = 6 + 16 * $frames.Count
    foreach ($f in $frames) {
        # 256 is stored as 0 - the field is a single byte.
        $dim = if ($f.Size -ge 256) { 0 } else { $f.Size }
        $bw.Write([byte]$dim)            # width
        $bw.Write([byte]$dim)            # height
        $bw.Write([byte]0)               # palette size (0 = truecolour)
        $bw.Write([byte]0)               # reserved
        $bw.Write([uint16]1)             # colour planes
        $bw.Write([uint16]32)            # bits per pixel
        $bw.Write([uint32]$f.Bytes.Length)
        $bw.Write([uint32]$offset)
        $offset += $f.Bytes.Length
    }
    foreach ($f in $frames) { $bw.Write([byte[]]$f.Bytes) }
    $bw.Flush(); $bw.Close(); $fs.Dispose()
}

# ---------------------------------------------------------------------------
#  Wizard bitmaps
# ---------------------------------------------------------------------------
function Write-Bmp {
    param($Bitmap, [string]$Path)
    # Inno wants a plain BMP. Saving a 32bpp ARGB bitmap straight out writes an
    # alpha channel the wizard renders as black, so it is flattened onto an
    # opaque 24bpp surface first.
    $flat = New-Object System.Drawing.Bitmap($Bitmap.Width, $Bitmap.Height,
                [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [System.Drawing.Graphics]::FromImage($flat)
    $g.Clear($Navy)
    $g.DrawImage($Bitmap, 0, 0)
    $g.Dispose()
    $flat.Save($Path, [System.Drawing.Imaging.ImageFormat]::Bmp)
    $flat.Dispose()
}

function Write-WizardLarge([string]$Path) {
    $W = 164; $H = 314
    $c = New-Canvas $W $H

    $rect = New-Object System.Drawing.Rectangle(0, 0, $W, $H)
    $grad = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        $rect, $Navy, $Blue, [System.Drawing.Drawing2D.LinearGradientMode]::Vertical)
    # The middle stop is what makes it the brand sweep rather than a plain
    # two-colour fade.
    $blend = New-Object System.Drawing.Drawing2D.ColorBlend(3)
    $blend.Colors    = @($Navy, $Mid, $Blue)
    $blend.Positions = @(0.0, 0.55, 1.0)
    $grad.InterpolationColors = $blend
    $c.G.FillRectangle($grad, $rect)
    $grad.Dispose()

    # A few off-angle strokes so the panel is not a flat wash. Low alpha on
    # purpose - it should read as texture, not as decoration competing with the
    # seal above it.
    $pen = New-Object System.Drawing.Pen(
        [System.Drawing.Color]::FromArgb(20, 255, 255, 255), 26.0)
    for ($x = -120; $x -lt $W + 160; $x += 54) { $c.G.DrawLine($pen, $x, $H, $x + 150, -20) }
    $pen.Dispose()

    # White disc behind the seal, the way the web header rings it, so the mark
    # sits on its own ground instead of on the gradient.
    $disc = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
    $c.G.FillEllipse($disc, 38, 50, 88, 88)
    $disc.Dispose()
    Draw-Mark -G $c.G -X 42 -Y 54 -Size 80 -Ring $false

    $title = New-Object System.Drawing.Font('Segoe UI', 11, [System.Drawing.FontStyle]::Bold)
    $sub   = New-Object System.Drawing.Font('Segoe UI', 8)
    $fmt   = New-Object System.Drawing.StringFormat
    $fmt.Alignment = 'Center'

    $white = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
    $muted = New-Object System.Drawing.SolidBrush($SubTint)
    $c.G.DrawString("Vehicle`nManagement`nSystem", $title, $white,
        (New-Object System.Drawing.RectangleF(8, 152, ($W - 16), 70)), $fmt)
    $c.G.DrawString("Saint Louis College`nCampus deployment", $sub, $muted,
        (New-Object System.Drawing.RectangleF(8, 232, ($W - 16), 50)), $fmt)

    $white.Dispose(); $muted.Dispose(); $title.Dispose(); $sub.Dispose(); $fmt.Dispose()
    Write-Bmp $c.Bitmap $Path
    $c.G.Dispose(); $c.Bitmap.Dispose()
}

function Write-WizardSmall([string]$Path) {
    $c = New-Canvas 55 55
    $brush = New-Object System.Drawing.SolidBrush($Navy)
    $c.G.FillRectangle($brush, 0, 0, 55, 55)
    $brush.Dispose()
    $disc = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
    $c.G.FillEllipse($disc, 4, 4, 47, 47)
    $disc.Dispose()
    Draw-Mark -G $c.G -X 6 -Y 6 -Size 43 -Ring $false
    Write-Bmp $c.Bitmap $Path
    $c.G.Dispose(); $c.Bitmap.Dispose()
}

# ---------------------------------------------------------------------------
Write-Ico        (Join-Path $outDir 'slc-vms.ico') @(16, 32, 48, 64, 128, 256)
Write-WizardLarge (Join-Path $outDir 'wizard-large.bmp')
Write-WizardSmall (Join-Path $outDir 'wizard-small.bmp')
Copy-Item $srcLogo (Join-Path $outDir 'slclogo.jpg') -Force

$logo.Dispose()

Get-ChildItem $outDir | ForEach-Object {
    Write-Host ("  {0,-20} {1,8:N0} bytes" -f $_.Name, $_.Length) -ForegroundColor DarkGray
}
Write-Host "Assets written to $outDir" -ForegroundColor Green
