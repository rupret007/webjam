import ctypes
import os
import sys
import subprocess
import time
import webbrowser
import glob
from pathlib import Path
from utils.installer_helpers import is_admin, run, find_jamulus as find_jamulus_in_paths, vb_cable_present

# Legacy launcher path retained for compatibility.
# Prefer using webjam_installer.py for current installer behavior.

# ====== CONFIG ======
JAMULUS_SERVER = "172.24.194.9"
JAMULUS_PORT   = "22124"
WEBEX_URL      = "https://webjam-sbx.webex.com/meet/webjam01"

# VB-CABLE polling (for interactive installer)
VBC_MAX_WAIT_SECS = 7 * 60   # 7 minutes
VBC_POLL_INTERVAL = 5        # seconds

# Jamulus polling if installer is interactive
JAMULUS_MAX_WAIT_SECS = 10 * 60
JAMULUS_POLL_INTERVAL = 5

# Common Jamulus install locations
JAMULUS_CANDIDATES = [
    r"C:\Program Files\Jamulus\Jamulus.exe",
    r"C:\Program Files (x86)\Jamulus\Jamulus.exe",
]

HERE = Path(__file__).resolve().parent
VB_DIR = HERE / "VB"
JAMULUS_INSTALLER = next((Path(p) for p in glob.glob(str(HERE / "jamulus*_win*.exe"))), None)

# ====== helpers ======
def elevate_if_needed():
    if not is_admin():
        # Re-launch as admin, pass original args
        params = " ".join([f'"{a}"' for a in sys.argv[1:]])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{__file__}" {params}', None, 1)
        sys.exit(0)

def find_jamulus():
    return find_jamulus_in_paths(JAMULUS_CANDIDATES)

def wait_for_vb_cable(max_wait=VBC_MAX_WAIT_SECS, interval=VBC_POLL_INTERVAL):
    waited = 0
    while waited < max_wait:
        if vb_cable_present():
            return True
        time.sleep(interval)
        waited += interval
        print(f"  Waiting for VB-CABLE… {waited}s/{max_wait}s")
    return False

def install_vb_cable():
    print("== VB-CABLE: checking…")
    if vb_cable_present():
        print("VB-CABLE already installed ✓")
        return

    if not VB_DIR.exists():
        print(f"VB folder not found at {VB_DIR}. Skipping VB-CABLE install.")
        return

    # Prefer INF via pnputil (silent & reliable if INF present)
    inf_candidates = [
        VB_DIR / "VBCABLE.inf",
        VB_DIR / "Driver64" / "VBCABLE.inf",
        VB_DIR / "Driver" / "VBCABLE.inf",
    ]
    inf = next((c for c in inf_candidates if c.exists()), None)

    if inf:
        print(f"Installing VB-CABLE via pnputil from: {inf}")
        run(["pnputil", "/add-driver", str(inf), "/install"])
        if wait_for_vb_cable():
            print("VB-CABLE installed ✓")
            return
        else:
            print("pnputil did not confirm install; trying EXE…")

    # EXE fallback: launch interactive installer immediately and poll
    exe_candidates = list(VB_DIR.glob("VBCABLE_Setup*.exe"))
    if not exe_candidates:
        print("VB-CABLE EXE not found. Place installer in VB folder.")
        return

    exe = str(exe_candidates[0])
    print(f"Launching VB-CABLE installer UI: {exe}")
    try:
        subprocess.Popen([exe], shell=False)
    except Exception as e:
        print(f"Failed to launch VB-CABLE EXE: {e}")
        return

    print(f"Waiting up to {VBC_MAX_WAIT_SECS//60} min for VB-CABLE to be detected...")
    if wait_for_vb_cable():
        print("VB-CABLE installed ✓")
        time.sleep(3)
    else:
        print("VB-CABLE still not detected after waiting. A reboot or manual install may be required.")

def install_jamulus_if_needed():
    print("== Jamulus: checking…")
    if find_jamulus():
        print("Jamulus already installed ✓")
        return True

    if not JAMULUS_INSTALLER or not JAMULUS_INSTALLER.exists():
        print("Jamulus installer not found in this folder. Place jamulus_*.exe here.")
        return False

    print(f"Installing Jamulus from {JAMULUS_INSTALLER.name} …")
    # Try common silent switches
    for sw in ["/S", "/silent", "/verysilent"]:
        try:
            subprocess.run([str(JAMULUS_INSTALLER), sw], check=False)
            # Poll for installed binary
            waited = 0
            while waited < JAMULUS_MAX_WAIT_SECS:
                if find_jamulus():
                    print("Jamulus installed ✓")
                    return True
                time.sleep(JAMULUS_POLL_INTERVAL)
                waited += JAMULUS_POLL_INTERVAL
        except Exception:
            pass

    # Fallback: interactive installer + poll for installed binary
    print("Silent install may not be supported; launching Jamulus installer UI and waiting for install to complete…")
    try:
        subprocess.Popen([str(JAMULUS_INSTALLER)], shell=False)
    except Exception:
        pass
    waited = 0
    while waited < JAMULUS_MAX_WAIT_SECS:
        if find_jamulus():
            print("Jamulus installed ✓")
            return True
        time.sleep(JAMULUS_POLL_INTERVAL)
        waited += JAMULUS_POLL_INTERVAL

    print("Jamulus not detected after waiting. Please install manually and re-run.")
    return False

def set_default_devices_best_effort():
    # Non-blocking best-effort: use AudioDeviceCmdlets if available or install per-user.
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

def create_desktop_shortcut_to_self():
    # Creates a Desktop shortcut named "WebJam Launch" that re-runs this script/exe
    desktop = Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))
    lnk_path = desktop / "WebJam Launch.lnk"

    # target + args: if frozen (PyInstaller EXE), target is exe; else target is python.exe with this script as arg
    if getattr(sys, 'frozen', False):
        target = str(Path(sys.executable).resolve())
        args   = ""
        workdir = str(HERE)
    else:
        target = sys.executable
        args   = f'"{Path(__file__).resolve()}"'
        workdir = str(HERE)

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

    time.sleep(3)
    print(f"Opening Webex PMR → {WEBEX_URL}")
    webbrowser.open(WEBEX_URL)

def main():
    print("WebJam one-click starting…")
    elevate_if_needed()

    # 1) VB-CABLE (audio bridge)
    install_vb_cable()

    # 2) Jamulus
    ok = install_jamulus_if_needed()
    if not ok:
        return

    # 3) Set VB devices as defaults (non-blocking best effort)
    set_default_devices_best_effort()

    # 4) Create desktop shortcut to this launcher/exe
    create_desktop_shortcut_to_self()

    # 5) Launch Jamulus + Webex
    launch_everything()

    print("All set. If VB-CABLE was just installed, a reboot may be required for devices to show up in apps.")

if __name__ == "__main__":
    main()
