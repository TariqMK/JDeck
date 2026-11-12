"""Start at login for JDeck Server. This is the one file JDeck Server writes outside its
data folder. It exists only while the option is on.

- Windows: a shortcut in the user's Startup folder.
- macOS:   a LaunchAgent in ~/Library/LaunchAgents/.
- Linux:   a .desktop file in ~/.config/autostart/."""

import os
import plistlib
import subprocess
import sys

LABEL = "io.github.jdeck.server"
NAME = "JDeck Server"


def launch_command():
    """The program and arguments that start this JDeck Server."""
    if getattr(sys, "frozen", False):
        return [os.path.abspath(sys.executable)]
    python = sys.executable
    if sys.platform == "win32":  # pythonw.exe starts without a console window.
        candidate = os.path.join(os.path.dirname(python), "pythonw.exe")
        python = candidate if os.path.isfile(candidate) else python
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jdeck_server.py")
    return [python, script]


def entry_path(platform=None, home=None, appdata=None):
    platform = platform or sys.platform
    home = home or os.path.expanduser("~")
    if platform == "win32":
        appdata = appdata or os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
                            f"{NAME}.lnk")
    if platform == "darwin":
        return os.path.join(home, "Library", "LaunchAgents", f"{LABEL}.plist")
    return os.path.join(home, ".config", "autostart", "jdeck-server.desktop")


def is_enabled(**where):
    return os.path.isfile(entry_path(**where))


def _desktop_quote(arg):
    """Quote one argument for the Exec key of a .desktop file."""
    if not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def desktop_entry(command):
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={NAME}\n"
            f"Exec={' '.join(_desktop_quote(a) for a in command)}\n"
            "X-GNOME-Autostart-enabled=true\n"
            "Terminal=false\n")


def launch_agent(command):
    return plistlib.dumps({"Label": LABEL, "ProgramArguments": command,
                           "RunAtLoad": True, "ProcessType": "Interactive"})


def _ps_quote(text):
    return "'" + text.replace("'", "''") + "'"


def _write_windows_shortcut(path, command):
    """Create a .lnk with the Windows Script Host, through PowerShell (no extra packages)."""
    target, args = command[0], subprocess.list2cmdline(command[1:])
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(path)}); "
              f"$s.TargetPath = {_ps_quote(target)}; $s.Arguments = {_ps_quote(args)}; "
              f"$s.WorkingDirectory = {_ps_quote(os.path.dirname(target))}; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, capture_output=True, timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def set_enabled(enabled, command=None, platform=None, **where):
    """Turn start at login on or off. Raises OSError when the file cannot be written or removed."""
    platform = platform or sys.platform
    path = entry_path(platform=platform, **where)
    if not enabled:
        if os.path.isfile(path):
            os.remove(path)
        return
    command = command or launch_command()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if platform == "win32":
        try:
            _write_windows_shortcut(path, command)
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            raise OSError(f"Could not create the Startup shortcut ({e}).") from e
    elif platform == "darwin":
        with open(path, "wb") as f:
            f.write(launch_agent(command))
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(desktop_entry(command))
