' start-campus.vbs - what the shortcut actually runs.
'
' The shortcut could point at powershell.exe directly, but -WindowStyle Hidden
' still creates a console window and destroys it, so every launch flashes a
' black rectangle across the screen. WScript.Shell.Run with a window style of 0
' never creates one at all.
'
' It exists for that reason alone. All of the logic is in start-campus.ps1.

Option Explicit

Dim shell, fso, here, target, cmd
Set shell = CreateObject("WScript.Shell")
Set fso   = CreateObject("Scripting.FileSystemObject")

here   = fso.GetParentFolderName(WScript.ScriptFullName)
target = fso.BuildPath(here, "start-campus.ps1")

If Not fso.FileExists(target) Then
    MsgBox "start-campus.ps1 is missing from" & vbCrLf & here & vbCrLf & vbCrLf & _
           "Reinstall the Smart Parking and Vehicle Verification System to fix this.", vbCritical, "Smart Parking and Vehicle Verification System"
    WScript.Quit 1
End If

cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & target & """"
shell.Run cmd, 0, False
