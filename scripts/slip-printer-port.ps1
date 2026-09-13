<#
    slip-printer-port.ps1 - keeps the thermal slip printer on the USB port its
    device is actually plugged into.

    The visitor slip prints from the server (backend\scanning\slip_printer.py),
    so paper sizes and driver options no longer matter - but the Windows
    printer's port still does. Windows names USB printer ports USB001, USB002...
    in the order devices first appear, and a printer created while another
    device held a port keeps pointing at it. The job then fails in the spooler
    with "The system cannot find the file specified" and nothing comes out.
    Plugging the printer into a different socket can move it again, so this
    runs every time the launcher starts the server, not once at install.

    Returns one @{ Kind; Text } for the launcher log. Changes nothing unless
    the printer's port has no thermal printer behind it AND exactly one
    connected thermal device is free to move to - it never guesses between two.

        powershell -ExecutionPolicy Bypass -File scripts\slip-printer-port.ps1
#>
[CmdletBinding()]
param()

# Same pattern slip_printer.py uses to pick the printer.
$pattern = 'pos-?\s?58|jp-?\s?58|58\s?mm|thermal|receipt'

try {
    $printers = @(Get-Printer -ErrorAction Stop |
                  Where-Object { $_.Name -match $pattern -or $_.DriverName -match $pattern })
} catch {
    return @{ Kind = 'warn'; Text = "Slip printer: could not list printers ($($_.Exception.Message.Trim()))." }
}
if ($printers.Count -eq 0) {
    return @{ Kind = 'dim'; Text = 'Slip printer: none installed - visitor slips will open the print dialog.' }
}

# Every USB print device Windows has seen, with the port it was given. The
# interface key is named ##?#USB#VID_xxxx&PID_yyyy#serial#{guid}; its Device
# Parameters hold the port number.
$usbPrintClass = 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceClasses\{28d78fad-5a12-11d1-ae5b-0000f803a8c2}'
$thermalPorts = @()
foreach ($key in @(Get-ChildItem $usbPrintClass -ErrorAction SilentlyContinue)) {
    $parts = $key.PSChildName -split '#'
    if ($parts.Count -lt 6) { continue }
    $instance = "$($parts[3])\$($parts[4])\$($parts[5])"
    $params = Get-ItemProperty -LiteralPath (Join-Path $key.PSPath '#\Device Parameters') -ErrorAction SilentlyContinue
    if (-not $params -or $null -eq $params.'Port Number') { continue }

    # Only a device that is plugged in right now is a place to move to.
    $device = Get-PnpDevice -InstanceId $instance -PresentOnly -ErrorAction SilentlyContinue
    if (-not $device) { continue }

    # VID_0FE6 is the USB chip the JP-58H / POS58 family ships with; the name
    # check covers other thermal models that report themselves sensibly.
    if ($device.FriendlyName -match $pattern -or $params.'Port Description' -match $pattern -or
        $instance -match 'VID_0FE6') {
        $thermalPorts += ('USB{0:D3}' -f [int]$params.'Port Number')
    }
}

$messages = @()
foreach ($printer in $printers) {
    if ($thermalPorts -contains $printer.PortName) {
        $messages += @{ Kind = 'ok'; Text = "Slip printer: $($printer.Name) on $($printer.PortName)." }
        continue
    }
    # A network or COM port was set by hand on purpose; only USB ports drift.
    if ($printer.PortName -notlike 'USB*') {
        $messages += @{ Kind = 'dim'; Text = "Slip printer: $($printer.Name) on $($printer.PortName) (not USB - left as set)." }
        continue
    }
    $taken = @($printers | Where-Object { $_.Name -ne $printer.Name } | ForEach-Object { $_.PortName })
    $free  = @($thermalPorts | Where-Object { $taken -notcontains $_ } | Select-Object -Unique)
    if ($free.Count -eq 0) {
        $messages += @{ Kind = 'warn'; Text = "Slip printer: $($printer.Name) is not connected - plug it in and restart the server." }
    } elseif ($free.Count -gt 1) {
        $messages += @{ Kind = 'warn'; Text = "Slip printer: $($printer.Name) is on $($printer.PortName), which has no thermal printer, and $($free.Count) are connected - set its port by hand." }
    } else {
        try {
            Set-Printer -Name $printer.Name -PortName $free[0] -ErrorAction Stop
            $messages += @{ Kind = 'ok'; Text = "Slip printer: moved $($printer.Name) from $($printer.PortName) to $($free[0]), where it is plugged in." }
        } catch {
            $messages += @{ Kind = 'warn'; Text = "Slip printer: $($printer.Name) should be on $($free[0]) but could not be moved ($($_.Exception.Message.Trim())) - set its port by hand." }
        }
    }
}
return $messages
