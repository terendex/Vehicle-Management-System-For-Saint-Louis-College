"""The Windows "Select Folder" window, for the backup folder's Browse button.

The folder setting names a folder on the SERVER. When the admin is sitting at
the server PC itself (the campus PC, or the instructor demo), the friendliest
picker is the one every Windows program uses: the File Explorer style window,
with Quick access, the drives, USB sticks and New folder. A browser cannot
show it with a usable path, so the server opens it on its own desktop and
hands back what was picked.

That only makes sense when the person clicking is looking at that desktop, so
`available()` refuses anyone on another computer (a guard PC on the LAN, the
cloud site, a tunnel) and any server without a desktop (Railway, a Windows
service). The page then falls back to the in-app folder list
(backup_utils.browse_folders), which works from anywhere.

Picking only fills the field. The Save that follows still runs
check_scheduled_dir's write test, as with the in-app list.
"""
from __future__ import annotations

import base64
import html
import os
import re
import socket
import subprocess
import threading

DIALOG_TIMEOUT = 300                 # seconds; an unanswered window is closed so the request cannot hang forever

# One window at a time: a second click while one is open would stack another
# window behind it and hold a second worker thread.
_lock = threading.Lock()


class FolderDialogBusy(Exception):
    """A folder window is already open on the server PC."""


# IFileOpenDialog with FOS_PICKFOLDERS: the modern Explorer style picker
# (Vista and later), not the old tree only FolderBrowserDialog that Windows
# PowerShell 5.1's .NET Framework would give.
#
# The dialog is owned by a hidden, always-on-top window of this process, and
# Arrange() centres it over the window in front (the browser the admin just
# clicked in) and brings it forward. It must NOT be owned by the browser
# itself: a dialog disables its owner while open, and if this process is killed (the timeout below, or the launcher stopping
# the server) nothing re-enables it, leaving the browser dead to clicks.
_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class SlcFolderDialog {
    [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")] class FileOpenDialogCoClass {}

    [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IFileOpenDialog {
        [PreserveSig] int Show(IntPtr hwndOwner);
        void SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);
        void SetFileTypeIndex(uint iFileType);
        void GetFileTypeIndex(out uint piFileType);
        void Advise(IntPtr pfde, out uint pdwCookie);
        void Unadvise(uint dwCookie);
        void SetOptions(uint fos);
        void GetOptions(out uint pfos);
        void SetDefaultFolder(IShellItem psi);
        void SetFolder(IShellItem psi);
        void GetFolder(out IShellItem ppsi);
        void GetCurrentSelection(out IShellItem ppsi);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
        void GetResult(out IShellItem ppsi);
    }

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellItem {
        void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
        void GetParent(out IShellItem ppsi);
        void GetDisplayName(uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
        void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
        void Compare(IShellItem psi, uint hint, out int piOrder);
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHCreateItemFromParsingName(string pszPath, IntPtr pbc, ref Guid riid, out IShellItem ppv);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hWnd, IntPtr pid);
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr CreateWindowEx(uint exStyle, string cls, string name, uint style,
        int x, int y, int w, int h, IntPtr parent, IntPtr menu, IntPtr inst, IntPtr param);
    [DllImport("user32.dll")] static extern bool DestroyWindow(IntPtr hWnd);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] static extern IntPtr MonitorFromWindow(IntPtr hWnd, uint flags);
    [DllImport("user32.dll")] static extern bool GetMonitorInfo(IntPtr mon, ref MONITORINFO mi);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr hWnd, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr ctx);
    delegate bool EnumProc(IntPtr hWnd, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumThreadWindows(uint tid, EnumProc f, IntPtr l);
    [StructLayout(LayoutKind.Sequential)] struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] struct MONITORINFO { public int cbSize; public RECT rcMonitor; public RECT rcWork; public uint dwFlags; }

    const uint WS_POPUP = 0x80000000, WS_EX_TOPMOST = 0x8, WS_EX_TOOLWINDOW = 0x80;
    const uint SWP_NOSIZE = 0x1, SWP_NOZORDER = 0x4, SWP_NOACTIVATE = 0x10, MONITOR_DEFAULTTONEAREST = 2;
    const uint FOS_PICKFOLDERS = 0x20, FOS_FORCEFILESYSTEM = 0x40, FOS_PATHMUSTEXIST = 0x800;
    const uint SIGDN_FILESYSPATH = 0x80058000;
    const int ERROR_CANCELLED = unchecked((int)0x800704C7);

    // Waits for the dialog on `dialogThread` to appear, centres it over
    // `front` (kept inside that screen's work area) and brings it forward.
    // Windows places it by its own memory otherwise, often half off screen.
    static void Arrange(uint dialogThread, IntPtr front) {
        // The dialog draws itself DPI aware while PowerShell is not, so on a
        // scaled screen (125% and up) this thread would read and set
        // virtualized coordinates and miss. Real pixels for this thread only.
        try { SetThreadDpiAwarenessContext(new IntPtr(-4)); } catch (EntryPointNotFoundException) { }   // PER_MONITOR_AWARE_V2; older Windows: best effort
        for (int tries = 0; tries < 200; tries++) {          // up to 10 s, the first run compiles slowly
            IntPtr found = IntPtr.Zero;
            EnumThreadWindows(dialogThread, (h, l) => {
                StringBuilder cls = new StringBuilder(32);
                GetClassName(h, cls, 32);
                if (cls.ToString() == "#32770" && IsWindowVisible(h)) { found = h; return false; }
                return true;
            }, IntPtr.Zero);
            if (found != IntPtr.Zero) {
                MONITORINFO mi = new MONITORINFO();
                mi.cbSize = Marshal.SizeOf(typeof(MONITORINFO));
                GetMonitorInfo(MonitorFromWindow(front != IntPtr.Zero ? front : found, MONITOR_DEFAULTTONEAREST), ref mi);
                RECT area = mi.rcWork, target = mi.rcWork, d;
                if (front != IntPtr.Zero) GetWindowRect(front, out target);
                GetWindowRect(found, out d);
                int w = d.Right - d.Left, h = d.Bottom - d.Top;
                int x = (target.Left + target.Right - w) / 2, y = (target.Top + target.Bottom - h) / 2;
                x = Math.Max(area.Left, Math.Min(x, area.Right - w));
                y = Math.Max(area.Top, Math.Min(y, area.Bottom - h));
                SetWindowPos(found, IntPtr.Zero, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
                // Sharing the clicked-in window's input state for a moment is
                // what lets a background process take the foreground at all.
                uint frontThread = front != IntPtr.Zero ? GetWindowThreadProcessId(front, IntPtr.Zero) : 0;
                uint me = GetCurrentThreadId();
                bool attached = frontThread != 0 && AttachThreadInput(me, frontThread, true);
                SetForegroundWindow(found);
                BringWindowToTop(found);
                if (attached) AttachThreadInput(me, frontThread, false);
                return;
            }
            Thread.Sleep(50);
        }
    }

    public static string Pick(string initial, string title) {
        IFileOpenDialog dlg = (IFileOpenDialog)new FileOpenDialogCoClass();
        uint opts;
        dlg.GetOptions(out opts);
        dlg.SetOptions(opts | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST);
        dlg.SetTitle(title);
        if (!String.IsNullOrEmpty(initial)) {
            try {
                Guid iid = typeof(IShellItem).GUID;
                IShellItem start;
                SHCreateItemFromParsingName(initial, IntPtr.Zero, ref iid, out start);
                dlg.SetFolder(start);
            } catch (Exception) { }     // gone (USB pulled out): Windows opens its usual place instead
        }
        IntPtr front = GetForegroundWindow();
        // Hidden and always on top, so the dialog it owns stays in front of a
        // full screen kiosk browser too.
        IntPtr owner = CreateWindowEx(WS_EX_TOPMOST | WS_EX_TOOLWINDOW, "STATIC", "", WS_POPUP,
                                      0, 0, 0, 0, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
        uint me = GetCurrentThreadId();
        Thread arrange = new Thread(() => Arrange(me, front));
        arrange.IsBackground = true;
        arrange.Start();
        int hr;
        try { hr = dlg.Show(owner); }
        finally { if (owner != IntPtr.Zero) DestroyWindow(owner); }
        if (hr == ERROR_CANCELLED) return null;
        Marshal.ThrowExceptionForHR(hr);
        IShellItem item;
        dlg.GetResult(out item);
        string path;
        item.GetDisplayName(SIGDN_FILESYSPATH, out path);
        return path;
    }
}
"@
$picked = [SlcFolderDialog]::Pick($env:SLC_DIALOG_START, $env:SLC_DIALOG_TITLE)
if ($picked) { [Console]::Out.Write('PICKED:' + $picked) } else { [Console]::Out.Write('CANCELLED') }
'''


def _local_addresses() -> set[str]:
    """This computer's own IP addresses (best effort)."""
    addrs = {'127.0.0.1', '::1'}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addrs.add(info[4][0])
    except OSError:
        pass
    return addrs


def _has_desktop() -> bool:
    """True when this process runs in a signed-in user's session.

    A Windows service lives in session 0, where a window opens on a desktop
    nobody can see and the request would wait for a click that never comes.
    """
    import ctypes
    session = ctypes.c_ulong(0)
    kernel32 = ctypes.windll.kernel32
    if not kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session)):
        return False
    return session.value != 0


def available(request) -> bool:
    """Whether the folder window would open in front of the person asking:
    a Windows server with a desktop, and a request from that same computer.

    Same computer = the connection comes from this machine's own address. The
    campus browser on the server PC uses the LAN address (the launcher opens
    http://<LAN IP>), which is still one of ours. A request that came through
    a proxy or tunnel carries X-Forwarded-For and is treated as remote, since
    the tunnel itself connects from 127.0.0.1.
    """
    if os.name != 'nt':
        return False
    meta = request.META
    if meta.get('HTTP_X_FORWARDED_FOR') or meta.get('HTTP_X_REAL_IP'):
        return False
    client = (meta.get('REMOTE_ADDR') or '').strip()
    if not client:
        return False
    if client != meta.get('SERVER_NAME') and client not in _local_addresses():
        return False
    return _has_desktop()


def _error_text(stderr: bytes) -> str:
    """PowerShell's error, readable. With its streams redirected it writes
    errors as CLIXML (<S S="Error">…</S> with _x000D__x000A_ for newlines)."""
    text = stderr.decode('utf-8', errors='replace')
    parts = re.findall(r'<S S="Error">(.*?)</S>', text, flags=re.S)
    if parts:
        text = ''.join(parts).replace('_x000D_', '').replace('_x000A_', ' ')
        text = html.unescape(text)
    return ' '.join(text.split())[:300]


def pick_folder(initial: str = '', title: str = 'Choose where to save automatic backups') -> str | None:
    """Open the window and wait. The picked full path, or None if cancelled.

    Raises FolderDialogBusy when a window is already open, RuntimeError when
    the window could not be shown, subprocess.TimeoutExpired after
    DIALOG_TIMEOUT seconds with no answer (the window is closed).
    """
    if not _lock.acquire(blocking=False):
        raise FolderDialogBusy()
    try:
        encoded = base64.b64encode(_SCRIPT.encode('utf-16-le')).decode('ascii')
        env = {**os.environ, 'SLC_DIALOG_START': (initial or '').strip(), 'SLC_DIALOG_TITLE': title}
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-STA',
             '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
            capture_output=True, env=env, timeout=DIALOG_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = result.stdout.decode('utf-8', errors='replace').strip()
        if out.startswith('PICKED:'):
            return out[len('PICKED:'):]
        if out == 'CANCELLED':
            return None
        raise RuntimeError(_error_text(result.stderr) or f'exit code {result.returncode}')
    finally:
        _lock.release()
