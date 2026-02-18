# webjam_win_oneclick.py
import ctypes
import os
import sys
import subprocess
import time
import webbrowser
import glob
import shutil
from pathlib import Path
from utils.installer_helpers import is_admin, run, find_jamulus as find_jamulus_in_paths, vb_cable_present

# Legacy one-click launcher retained for compatibility.
# Prefer using webjam_installer.py for current installer behavior.

# ====== CONFIG ======
JAMULUS_SERVER = "172.24.194.9"
JAMULUS_PORT   = "22124"
WEBEX_URL      = "https://webjam-sbx.webex.com/meet/webjam01"

# Polling windows
VBC_MAX_WAIT_SECS = 60       # shorter wait; tweak if needed
VBC_POLL_INTERVAL = 5
JAMULUS_MAX_WAIT_SECS = 6 * 60
JAMULUS_POLL_INTERVAL = 5

# Default Jamulus install locations
JAMULUS_CANDIDATES = [
    r"C:\Program Files\Jamulus\Jamulus.exe",
    r"C:\Program Files (x86)\Jamulus\Jamulus.exe",
]

# ====== runtime paths (works for .py and PyInstaller EXE) ======
def base_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # PyInstaller temp dir containing bundled data
    return Path(__file__).resolve().parent

def work_dir() -> Path:
    p = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "WebJam" / "work"
    p.mkdir(parents=True, exist_ok=True)
    return p

HERE = base_dir()
WORK = work_dir()

def copy_resource(rel_src: str | Path, dest_dir: Path) -> Path:
    src = (HERE / rel_src).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dst = dest_dir / src.name
    # If already present with same size, reuse
    try:
        if dst.exists() and src.exists() and dst.stat().st_size == src.stat().st_size:
            return dst
    except Exception:
        pass
    shutil.copy2(src, dst)
    return dst

# ====== helpers ======
def elevate_if_needed():
    if not is_admin():
        params = " ".join([f'"{a}"' for a in sys.argv[1:]])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{__file__}" {params}', None, 1)
        sys.exit(0)

def find_jamulus():
    return find_jamulus_in_paths(JAMULUS_CANDIDATES)

def wait_until(predicate, max_secs, interval, label):
    waited = 0
    while waited < max_secs:
        if predicate():
            return True
        time.sleep(interval)
        waited += interval
        print(f"  Waiting for {label}… {waited}/{max_secs}s")
    return False

# ====== VB-CABLE install ======
def install_vb_cable():
    print("== VB-CABLE: checking…")
    if vb_cable_present():
        print("VB-CABLE already installed ✓")
        return

    # VB folder may be bundled; copy to WORK
    vb_src_dir = HERE / "VB"
    if not vb_src_dir.exists():
        print("VB folder not found next to the launcher; skipping VB-CABLE install.")
        return

    # Prefer INF via pnputil if present (silent)
    inf_candidates = [
        vb_src_dir / "VBCABLE.inf",
        vb_src_dir / "Driver64" / "VBCABLE.inf",
        vb_src_dir / "Driver" / "VBCABLE.inf",
    ]
    inf = next((c for c in inf_candidates if c.exists()), None)
    if inf:
        inf_copy = copy_resource(inf.relative_to(HERE), WORK)
        print(f"Installing VB-CABLE via pnputil: {inf_copy}")
        run(["pnputil","/add-driver",str(inf_copy),"/install"])
        if wait_until(vb_cable_present, VBC_MAX_WAIT_SECS, VBC_POLL_INTERVAL, "VB-CABLE device"):
            print("VB-CABLE installed ✓")
            return
        print("pnputil did not confirm install; trying EXE…")

    # Fallback: EXE interactive + background poll (no keypress)
    exe_candidates = list(vb_src_dir.glob("VBCABLE_Setup*.exe"))
    if not exe_candidates:
        print("VB-CABLE EXE not found in VB folder.")
        return
    exe_copy = copy_resource(exe_candidates[0].relative_to(HERE), WORK)
    print(f"Launching VB-CABLE installer UI: {exe_copy}")
    try:
        subprocess.Popen([str(exe_copy)], shell=False)
    except Exception as e:
        print(f"Failed to launch VB-CABLE EXE: {e}")
        return

    if wait_until(vb_cable_present, VBC_MAX_WAIT_SECS, VBC_POLL_INTERVAL, "VB-CABLE device"):
        print("VB-CABLE installed ✓")
        time.sleep(2)
    else:
        print("VB-CABLE still not detected. You may need to reboot or approve the driver in Windows.")

# ====== Jamulus install ======
def jamulus_installer_path():
    # Look for a bundled installer named jamulus*_win*.exe
    files = sorted(glob.glob(str(HERE / "jamulus*_win*.exe")))
    return Path(files[0]) if files else None

def install_jamulus_if_needed():
    print("== Jamulus: checking…")
    if find_jamulus():
        print("Jamulus already installed ✓")
        return True

    src = jamulus_installer_path()
    if not src or not src.exists():
        print("Jamulus installer not found next to the launcher (jamulus*_win*.exe).")
        return False

    inst_copy = copy_resource(src.name, WORK)
    print(f"Installing Jamulus from {inst_copy.name} …")

    # Try common silent switches
    for sw in ["/S", "/silent", "/verysilent"]:
        try:
            subprocess.run([str(inst_copy), sw], check=False)
            waited = 0
            while waited < JAMULUS_MAX_WAIT_SECS:
                if find_jamulus():
                    print("Jamulus installed ✓")
                    return True
                time.sleep(JAMULUS_POLL_INTERVAL)
                waited += JAMULUS_POLL_INTERVAL
        except Exception:
            pass

    print("Silent install may not be supported; launching Jamulus installer UI and waiting…")
    try:
        subprocess.Popen([str(inst_copy)], shell=False)
    except Exception:
        pass

    if wait_until(lambda: find_jamulus() is not None,
                  JAMULUS_MAX_WAIT_SECS, JAMULUS_POLL_INTERVAL, "Jamulus install"):
        print("Jamulus installed ✓")
        return True

    print("Jamulus not detected after waiting. Please finish the installer, then re-run.")
    return False

# ====== Default audio (best effort) ======
def set_default_devices_best_effort():
    ps = r'''
    $ErrorActionPreference = "SilentlyContinue"
    if (-not (Get-Module -ListAvailable -Name AudioDeviceCmdlets)) {
      try { Install-Module -Name AudioDeviceCmdlets -Scope CurrentUser -Force -AllowClobber -ErrorAction Stop } catch {}
    }
    Import-Module AudioDeviceCmdlets -ErrorAction SilentlyContinue
    $play = Get-AudioDevice -List | Where-Object { $_.Name -like "*CABLE Input*" } | Select-Object -First 1
    $rec  = Get-AudioDevice -List | Where-Object { $_.Name -like "*CABLE Output*" } | Select-Object -First 1
    if ($play) { try { Set-DefaultAudioDevice -Playback -Name $play.Name; Set-DefaultAudioDevice -Playback -Communications -Name $play.Name } catch {} }
    if ($rec)  { try { Set-DefaultAudioDevice -Recording -Name $rec.Name; Set-DefaultAudioDevice -Recording -Communications -Name $rec.Name } catch {} }
    '''
    print("Setting default devices to VB-CABLE (best effort)…")
    run(["powershell","-NoP","-NonI","-ExecutionPolicy","Bypass","-Command", ps])

# ====== Shortcut ======
def create_desktop_shortcut_to_self():
    desktop = Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))
    lnk_path = desktop / "WebJam Launch.lnk"

    if getattr(sys, 'frozen', False):
        target = str(Path(sys.executable).resolve())  # the EXE itself
        args   = ""
        workdir = str(Path(sys.executable).resolve().parent)
    else:
        target = sys.executable
        args   = f'"{Path(__file__).resolve()}"'
        workdir = str(Path(__file__).resolve().parent)

    ps = f'''
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut("{lnk_path}")
    $Shortcut.TargetPath = "{target}"
    $Shortcut.Arguments  = {args if args else '""'}
    $Shortcut.IconLocation = "{target},0"
    $Shortcut.WorkingDirectory = "{workdir}"
    $Shortcut.Save()
    '''
    run(["powershell","-NoP","-NonI","-Command", ps])
    print(f"Desktop shortcut created: {lnk_path}")

# ====== Launch ======
def launch_everything():
    jamulus = find_jamulus()
    if not jamulus:
        print("Jamulus not found; cannot launch.")
        return
    server = f"{JAMULUS_SERVER}:{JAMULUS_PORT}"
    print(f"Launching Jamulus → {server}")
    try:
        subprocess.Popen([jamulus, "--connect", server])
    except Exception as e:
        print(f"Failed to start Jamulus: {e}")

    time.sleep(2)
    print(f"Opening Webex PMR → {WEBEX_URL}")
    webbrowser.open(WEBEX_URL)

# ====== Main ======
def main():
    print("WebJam one-click starting…")
    elevate_if_needed()

    install_vb_cable()
    if not install_jamulus_if_needed():
        return

    set_default_devices_best_effort()
    create_desktop_shortcut_to_self()
    launch_everything()

    print("Done. If VB-CABLE was just installed, a reboot may be required for apps to see the devices.")

if __name__ == "__main__":
    main()
