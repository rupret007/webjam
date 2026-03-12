"""
WebJam Enhanced Installer
One-click installation and configuration for the WebJam music collaboration platform
"""

import ctypes
import os
import sys
import subprocess
import time
import glob
import shutil
import platform
import urllib.request
import urllib.error
import json
from pathlib import Path
from utils.installer_helpers import is_admin, run, find_jamulus as find_jamulus_in_paths, vb_cable_present
# import webbrowser  # Reserved for future use

# ====== CONFIG ======
JAMULUS_SERVER = "172.24.194.9"
JAMULUS_PORT = "22124"
WEBEX_URL = "https://webjam-sbx.webex.com/meet/webjam01"

# Installation timeouts
VBC_MAX_WAIT_SECS = 7 * 60   # 7 minutes for VB-Cable
VBC_POLL_INTERVAL = 5
JAMULUS_MAX_WAIT_SECS = 10 * 60  # 10 minutes for Jamulus
JAMULUS_POLL_INTERVAL = 5

# Installation paths
JAMULUS_CANDIDATES = [
    r"C:\Program Files\Jamulus\Jamulus.exe",
    r"C:\Program Files (x86)\Jamulus\Jamulus.exe",
]

# Upstream download sources
JAMULUS_RELEASES_API = "https://api.github.com/repos/jamulussoftware/jamulus/releases/latest"
WEBEX_MSI_WIN_X64 = "https://binaries.webex.com/WebexOfclDesktop-Win-64-Gold/Webex.msi"
WEBEX_MSI_WIN_ARM64 = "https://binaries.webex.com/WebexOfclDesktop-Win-Arm-64-Gold/Webex.msi"

# Runtime paths
def base_dir() -> Path:
    """Get base directory (works for script and PyInstaller exe)"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent

def work_dir() -> Path:
    """Get working directory for temporary files"""
    p = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "WebJam" / "work"
    p.mkdir(parents=True, exist_ok=True)
    return p

def app_dir() -> Path:
    """Get application installation directory"""
    p = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "WebJam" / "app"
    p.mkdir(parents=True, exist_ok=True)
    return p

HERE = base_dir()
WORK = work_dir()
APP_DIR = app_dir()
APP_EXE_NAME = "WebJam.exe"

# ====== Helpers ======
def print_header(text):
    """Print a formatted header"""
    print("\n" + "="*70)
    print(f"  {text}")
    print("="*70)

def print_step(step, text):
    """Print a step indicator"""
    print(f"\n[{step}/6] {text}")
    print("-" * 70)

def python_command_prefix(prefer_windowed: bool = False) -> list[str] | None:
    """
    Resolve a Python command prefix for subprocess execution.

    Returns a list prefix (for example ["python.exe"] or ["py.exe", "-3"])
    or None when no suitable runtime is available.
    """
    if not getattr(sys, "frozen", False):
        return [sys.executable]

    candidates = ["pythonw.exe", "python.exe", "py.exe"] if prefer_windowed else ["python.exe", "py.exe", "pythonw.exe"]
    for name in candidates:
        resolved = shutil.which(name)
        if not resolved:
            continue
        if Path(resolved).name.lower() == "py.exe":
            return [resolved, "-3"]
        return [resolved]
    return None

def installed_app_executable() -> Path:
    return APP_DIR / APP_EXE_NAME

def bundled_app_executable_source() -> Path | None:
    candidates = [
        HERE / APP_EXE_NAME,
        HERE / "dist" / APP_EXE_NAME,
        Path(sys.executable).resolve().parent / APP_EXE_NAME,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None

def ps_quote(value: str) -> str:
    escaped = value.replace("`", "``").replace('"', '`"')
    return f'"{escaped}"'

def shortcut_target_and_args(app_file: Path) -> tuple[str, str] | None:
    app_exe = installed_app_executable()
    if app_exe.exists():
        return str(app_exe), ""
    prefix = python_command_prefix(prefer_windowed=True)
    if not prefix:
        return None
    target = prefix[0]
    prefix_args = " ".join(f'"{arg}"' for arg in prefix[1:])
    arguments = f"{prefix_args} \"{app_file}\"".strip()
    return target, arguments

def launch_installed_app(app_file: Path) -> bool:
    app_exe = installed_app_executable()
    if app_exe.exists():
        subprocess.Popen([str(app_exe)], cwd=str(APP_DIR))
        return True
    prefix = python_command_prefix(prefer_windowed=False)
    if not prefix:
        print("   Could not find a launch runtime (no bundled executable and no Python in PATH).")
        return False
    subprocess.Popen(prefix + [str(app_file)], cwd=str(APP_DIR))
    return True

def elevate_if_needed():
    """Re-launch with admin privileges if needed"""
    if not is_admin():
        print("\n[WARN] WebJam installer requires administrator privileges.")
        print("   Requesting elevation...")
        params = " ".join([f'"{a}"' for a in sys.argv[1:]])
        relaunch_target = sys.executable
        if getattr(sys, "frozen", False):
            relaunch_args = params
        else:
            relaunch_args = f'"{Path(__file__).resolve()}" {params}'.strip()
        try:
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", relaunch_target, relaunch_args, None, 1
            )
            if int(result) <= 32:
                print("   Elevation request was cancelled or denied.")
                print("   Please run the installer as Administrator.")
                try:
                    input("\nPress Enter to exit...")
                except EOFError:
                    pass
                sys.exit(1)
        except Exception as e:
            print(f"   Failed to elevate: {e}")
            print("   Please run the installer as Administrator.")
            try:
                input("\nPress Enter to exit...")
            except EOFError:
                pass
            sys.exit(1)
        sys.exit(0)

def find_jamulus():
    """Find installed Jamulus executable"""
    return find_jamulus_in_paths(JAMULUS_CANDIDATES)

def copy_resource(rel_src: str | Path, dest_dir: Path) -> Path:
    """Copy a resource file to destination"""
    src = (HERE / rel_src).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dst = dest_dir / src.name
    
    # Reuse if already present with same size
    try:
        if dst.exists() and src.exists() and dst.stat().st_size == src.stat().st_size:
            return dst
    except Exception:
        pass
    
    if src.exists():
        shutil.copy2(src, dst)
    return dst

def download_file(url: str, dest: Path, timeout: int = 60) -> bool:
    """Download URL to destination path."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "WebJamInstaller/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read()
        dest.write_bytes(data)
        return True
    except Exception as e:
        print(f"   ⚠  Download failed from {url}: {e}")
        return False

def fetch_latest_jamulus_installer() -> Path | None:
    """
    Attempt to fetch latest Windows Jamulus installer from GitHub releases.
    Returns local installer path in WORK or None on failure.
    """
    print("   Checking latest Jamulus release...")
    try:
        req = urllib.request.Request(JAMULUS_RELEASES_API, headers={"User-Agent": "WebJamInstaller/1.0"})
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"   ⚠  Could not query Jamulus releases API: {e}")
        return None

    assets = payload.get("assets", [])
    if not isinstance(assets, list):
        return None

    candidate_url = None
    candidate_name = None
    # Prefer a 64-bit Windows setup executable, then any Windows installer .exe.
    for asset in assets:
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        if not url:
            continue
        lowered = name.lower()
        if lowered.endswith(".exe") and "win64" in lowered and ("setup" in lowered or "installer" in lowered):
            candidate_url = url
            candidate_name = name
            break
    if not candidate_url:
        for asset in assets:
            name = str(asset.get("name", ""))
            url = str(asset.get("browser_download_url", ""))
            lowered = name.lower()
            if lowered.endswith(".exe") and "win" in lowered:
                candidate_url = url
                candidate_name = name
                break

    if not candidate_url:
        print("   ⚠  No Windows Jamulus installer found in latest release assets.")
        return None

    local_name = candidate_name or "jamulus_latest_win.exe"
    local_path = WORK / local_name
    if download_file(candidate_url, local_path):
        print(f"   ✓ Downloaded latest Jamulus installer: {local_name}")
        return local_path
    return None

def webex_installed() -> bool:
    """Best-effort check for Webex desktop app presence on Windows."""
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Cisco Spark" / "CiscoCollabHost.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Cisco Spark" / "Webex.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Cisco Spark" / "CiscoCollabHost.exe",
    ]
    return any(p.exists() for p in candidates)

def webex_msi_url_for_host() -> str:
    machine = platform.machine().lower()
    if "arm" in machine or "aarch" in machine:
        return WEBEX_MSI_WIN_ARM64
    return WEBEX_MSI_WIN_X64

# ====== VB-Cable Installation ======

def wait_until(predicate, max_secs, interval, label):
    """Wait until predicate returns True or timeout"""
    waited = 0
    while waited < max_secs:
        if predicate():
            return True
        time.sleep(interval)
        waited += interval
        print(f"   Waiting for {label}... {waited}/{max_secs}s", end='\r')
    print()  # New line after waiting
    return False

def install_vb_cable():
    """Install VB-Cable virtual audio device"""
    print("\n🔊 Checking VB-Cable (Virtual Audio Device)...")
    
    if vb_cable_present():
        print("   ✓ VB-Cable already installed")
        return True
    
    vb_src_dir = HERE / "VB"
    if not vb_src_dir.exists():
        print("   ⚠  VB folder not found. Skipping VB-Cable installation.")
        print("   Note: VB-Cable is required for audio routing between Jamulus and Webex.")
        return False
    
    # Try INF installation first (silent)
    inf_candidates = [
        vb_src_dir / "VBCABLE.inf",
        vb_src_dir / "Driver64" / "VBCABLE.inf",
        vb_src_dir / "Driver" / "VBCABLE.inf",
    ]
    inf = next((c for c in inf_candidates if c.exists()), None)
    
    if inf:
        inf_copy = copy_resource(inf.relative_to(HERE), WORK)
        print(f"   Installing VB-Cable driver via pnputil...")
        run(["pnputil", "/add-driver", str(inf_copy), "/install"])
        
        if wait_until(vb_cable_present, 30, 2, "VB-Cable device"):
            print("   ✓ VB-Cable installed successfully")
            return True
        print("   INF install did not complete, trying EXE installer...")
    
    # Fallback to EXE installer (interactive)
    exe_candidates = list(vb_src_dir.glob("VBCABLE_Setup*.exe"))
    if not exe_candidates:
        print("   ⚠  VB-Cable installer not found.")
        return False
    
    exe_copy = copy_resource(exe_candidates[0].relative_to(HERE), WORK)
    print(f"   Launching VB-Cable installer (you may need to click 'Install Driver')...")
    
    try:
        subprocess.Popen([str(exe_copy)], shell=False)
    except Exception as e:
        print(f"   Error: Failed to launch installer: {e}")
        return False
    
    print(f"   Waiting up to {VBC_MAX_WAIT_SECS//60} minutes for installation...")
    
    if wait_until(vb_cable_present, VBC_MAX_WAIT_SECS, VBC_POLL_INTERVAL, "VB-Cable"):
        print("   ✓ VB-Cable installed successfully")
        time.sleep(2)
        return True
    else:
        print("   ⚠  VB-Cable not detected after waiting.")
        print("   You may need to reboot or manually complete the installation.")
        return False

# ====== Jamulus Installation ======
def jamulus_installer_path():
    """Find bundled Jamulus installer"""
    files = sorted(glob.glob(str(HERE / "jamulus*_win*.exe")))
    return Path(files[0]) if files else None

def install_jamulus():
    """Install Jamulus client"""
    print("\n🎵 Checking Jamulus (Low-Latency Audio)...")
    
    if find_jamulus():
        print("   ✓ Jamulus already installed")
        return True

    installer = fetch_latest_jamulus_installer()
    if not installer:
        bundled = jamulus_installer_path()
        if bundled and bundled.exists():
            installer = copy_resource(bundled.name, WORK)
            print(f"   Using bundled Jamulus installer: {bundled.name}")
        else:
            print("   ⚠  No online or bundled Jamulus installer available.")
            print("   You can download Jamulus from: https://jamulus.io")
            return False

    inst_copy = installer
    print(f"   Installing Jamulus from {inst_copy.name}...")
    
    # Try silent installation
    for switch in ["/S", "/silent", "/verysilent"]:
        try:
            subprocess.run([str(inst_copy), switch], check=False)
            
            if wait_until(lambda: find_jamulus() is not None, 60, 2, "Jamulus"):
                print("   ✓ Jamulus installed successfully")
                return True
        except Exception:
            pass
    
    # Fallback to interactive installation
    print("   Launching Jamulus installer (please complete the installation)...")
    try:
        subprocess.Popen([str(inst_copy)], shell=False)
    except Exception:
        pass
    
    if wait_until(lambda: find_jamulus() is not None, 
                  JAMULUS_MAX_WAIT_SECS, JAMULUS_POLL_INTERVAL, "Jamulus"):
        print("   ✓ Jamulus installed successfully")
        return True
    
    print("   ⚠  Jamulus not detected after waiting.")
    print("   Please complete the installation manually and run this installer again.")
    return False

# ====== Webex Installation ======
def install_webex():
    """Install Webex desktop app from official Cisco MSI links."""
    print("\n📹 Checking Webex Desktop App...")

    if webex_installed():
        print("   ✓ Webex appears to be already installed")
        return True

    msi_url = webex_msi_url_for_host()
    msi_name = "Webex_latest.msi"
    msi_path = WORK / msi_name

    print(f"   Downloading Webex installer from Cisco binaries...")
    if not download_file(msi_url, msi_path):
        print("   ⚠  Could not download Webex MSI. Skipping Webex installation.")
        return False

    print("   Installing Webex silently...")
    silent_cmd = [
        "msiexec",
        "/i",
        str(msi_path),
        "/qn",
        "ACCEPT_EULA=TRUE",
        "ALLUSERS=1",
    ]
    result = run(silent_cmd)
    if result.returncode == 0:
        print("   ✓ Webex installed successfully")
        return True

    print("   Silent install did not complete. Launching interactive installer...")
    interactive_cmd = ["msiexec", "/i", str(msi_path)]
    try:
        subprocess.Popen(interactive_cmd)
        print("   ✓ Webex installer launched")
        return True
    except Exception as e:
        print(f"   ⚠  Failed to launch interactive Webex installer: {e}")
        return False

# ====== Audio Configuration ======
def configure_audio_devices():
    """Configure default audio devices"""
    print("\n🎛  Configuring Audio Devices...")
    
    ps = r'''
    $ErrorActionPreference = "SilentlyContinue"
    
    # Install AudioDeviceCmdlets if not present
    if (-not (Get-Module -ListAvailable -Name AudioDeviceCmdlets)) {
      Write-Host "   Installing AudioDeviceCmdlets module..."
      try { 
        Install-Module -Name AudioDeviceCmdlets -Scope CurrentUser -Force -AllowClobber -ErrorAction Stop 
      } catch {
        Write-Host "   Could not install AudioDeviceCmdlets"
        exit 1
      }
    }
    
    Import-Module AudioDeviceCmdlets -ErrorAction SilentlyContinue
    
    # Find VB-Cable devices
    $play = Get-AudioDevice -List | Where-Object { $_.Name -like "*CABLE Input*" } | Select-Object -First 1
    $rec  = Get-AudioDevice -List | Where-Object { $_.Name -like "*CABLE Output*" } | Select-Object -First 1
    
    # Set as defaults
    if ($play) {
      try { 
        Set-AudioDevice -ID $play.ID
        Write-Host "   ✓ Set VB-Cable Input as default playback device"
      } catch {}
    }
    
    if ($rec) {
      try { 
        Set-AudioDevice -ID $rec.ID -RecordingDevice
        Write-Host "   ✓ Set VB-Cable Output as default recording device"
      } catch {}
    }
    '''
    
    result = run(["powershell", "-NoP", "-NonI", "-ExecutionPolicy", "Bypass", "-Command", ps])
    print(result.stdout if result.stdout else "   Audio device configuration attempted")

# ====== Python Dependencies ======
def install_python_dependencies():
    """Install Python dependencies for WebJam GUI"""
    print("\nInstalling Python Dependencies...")
    if installed_app_executable().exists():
        print("   Standalone WebJam executable detected; skipping Python dependency install.")
        return True
    requirements_file = HERE / "requirements.txt"
    if not requirements_file.exists():
        print("   requirements.txt not found, skipping Python dependencies")
        return False
    prefix = python_command_prefix(prefer_windowed=False)
    if not prefix:
        print("   Python runtime not found in PATH; skipping dependency install.")
        return False
    try:
        print("   Installing requirements.txt (this may take a few minutes)...")
        result = subprocess.run(
            prefix + ["-m", "pip", "install", "-q", "-r", str(requirements_file)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("   Python dependencies installed")
            return True
        print("   Full dependency install failed; trying minimal UI dependency...")
        fallback = subprocess.run(
            prefix + ["-m", "pip", "install", "-q", "customtkinter"],
            capture_output=True,
            text=True,
        )
        if fallback.returncode == 0:
            print("   Installed customtkinter fallback")
            return True
        print("   Could not install Python dependencies (not critical)")
        return False
    except Exception as e:
        print(f"   Error installing dependencies: {e}")
        return False
# ====== Application Installation ======
def install_webjam_app():
    """Install WebJam application files"""
    print("\nInstalling WebJam Application...")
    required_files = [
        "webjam_app_enhanced.py",
        "jamulus_controller.py",
        "webex_integration.py",
        "requirements.txt",
    ]
    optional_files = ["README.md"]
    required_dirs = ["core", "ui", "storage", "admin", "api", "utils"]
    success = True

    for filename in required_files + optional_files:
        src = HERE / filename
        if not src.exists():
            if filename in required_files:
                print(f"   Missing required file in installer payload: {filename}")
                success = False
            continue
        try:
            dst = APP_DIR / filename
            shutil.copy2(src, dst)
            print(f"   Copied {filename}")
        except OSError as exc:
            print(f"   Failed to copy {filename}: {exc}")
            success = False

    bundled_app = bundled_app_executable_source()
    if bundled_app:
        try:
            dst_exe = installed_app_executable()
            shutil.copy2(bundled_app, dst_exe)
            print(f"   Copied {APP_EXE_NAME}")
        except OSError as exc:
            print(f"   Failed to copy {APP_EXE_NAME}: {exc}")
            success = False
    else:
        print("   No bundled WebJam.exe found; installer will rely on Python runtime for app launch.")

    for dirname in required_dirs:
        src_dir = HERE / dirname
        if not (src_dir.exists() and src_dir.is_dir()):
            print(f"   Missing required directory in installer payload: {dirname}/")
            success = False
            continue
        try:
            dst_dir = APP_DIR / dirname
            shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
            print(f"   Copied {dirname}/")
        except OSError as exc:
            print(f"   Failed to copy {dirname}/: {exc}")
            success = False
    print(f"   Application installed to {APP_DIR}")
    return success
# ====== Shortcut Creation ======
def create_desktop_shortcut():
    """Create desktop shortcut to launch WebJam"""
    print("\nCreating Desktop Shortcut...")
    desktop = Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))
    lnk_path = desktop / "WebJam.lnk"
    app_file = APP_DIR / "webjam_app_enhanced.py"
    launch_spec = shortcut_target_and_args(app_file)
    if not launch_spec:
        print("   Could not resolve Python runtime for desktop shortcut")
        return False
    target, args = launch_spec
    target_lit = ps_quote(target)
    args_lit = ps_quote(args)
    workdir_lit = ps_quote(str(APP_DIR))
    lnk_lit = ps_quote(str(lnk_path))
    ps = f'''
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut({lnk_lit})
    $Shortcut.TargetPath = {target_lit}
    $Shortcut.Arguments = {args_lit}
    $Shortcut.WorkingDirectory = {workdir_lit}
    $Shortcut.IconLocation = {target_lit} + ",0"
    $Shortcut.Description = "WebJam Music Collaboration Platform"
    $Shortcut.Save()
    '''
    result = run(["powershell", "-NoP", "-NonI", "-Command", ps])
    if result.returncode != 0:
        print("   Failed to create Desktop shortcut")
        return False
    print(f"   Shortcut created: {lnk_path}")
    return True
def create_start_menu_shortcut():
    """Create Start Menu shortcut"""
    print("   Creating Start Menu shortcut...")
    start_menu = Path(os.environ.get("APPDATA")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    start_menu.mkdir(parents=True, exist_ok=True)
    lnk_path = start_menu / "WebJam.lnk"
    app_file = APP_DIR / "webjam_app_enhanced.py"
    launch_spec = shortcut_target_and_args(app_file)
    if not launch_spec:
        print("   Could not resolve Python runtime for Start Menu shortcut")
        return False
    target, args = launch_spec
    target_lit = ps_quote(target)
    args_lit = ps_quote(args)
    workdir_lit = ps_quote(str(APP_DIR))
    lnk_lit = ps_quote(str(lnk_path))
    ps = f'''
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut({lnk_lit})
    $Shortcut.TargetPath = {target_lit}
    $Shortcut.Arguments = {args_lit}
    $Shortcut.WorkingDirectory = {workdir_lit}
    $Shortcut.IconLocation = {target_lit} + ",0"
    $Shortcut.Description = "WebJam Music Collaboration Platform"
    $Shortcut.Save()
    '''
    result = run(["powershell", "-NoP", "-NonI", "-Command", ps])
    if result.returncode != 0:
        print("   Failed to create Start Menu shortcut")
        return False
    print("   Start Menu shortcut created")
    return True
# ====== Main Installation ======
def main():
    """Main installation process"""
    print_header("WebJam Enhanced Installer")
    print("\n🎵 Welcome to WebJam - Music Collaboration Platform")
    print("\nThis installer will set up:")
    print("  • VB-Cable (Virtual Audio Device)")
    print("  • Jamulus (Low-Latency Audio Client)")
    print("  • Webex Desktop App (official Cisco MSI)")
    print("  • WebJam GUI Application")
    print("  • Audio device configuration")
    print("  • Desktop shortcuts")
    
    print("\n⚠  Administrator privileges required for driver installation")
    
    input("\nPress Enter to begin installation...")
    
    # Elevate if needed
    elevate_if_needed()
    
    print("\n✓ Running with administrator privileges")
    
    # Installation steps
    success = True
    
    print_step(1, "Installing VB-Cable Virtual Audio Device")
    vb_ok = install_vb_cable()
    success = success and vb_ok
    
    print_step(2, "Installing Jamulus Audio Client")
    jamulus_ok = install_jamulus()
    success = success and jamulus_ok
    
    if not jamulus_ok:
        print("\n⚠  Cannot continue without Jamulus. Please install it manually.")
        input("\nPress Enter to exit...")
        return
    
    print_step(3, "Installing Webex Desktop App")
    webex_ok = install_webex()
    success = success and webex_ok

    print_step(4, "Configuring Audio Devices")
    configure_audio_devices()
    
    print_step(5, "Installing WebJam Application")
    app_ok = install_webjam_app()
    deps_ok = install_python_dependencies()
    success = success and app_ok and deps_ok
    
    print_step(6, "Creating Shortcuts")
    desktop_shortcut_ok = create_desktop_shortcut()
    start_shortcut_ok = create_start_menu_shortcut()
    success = success and desktop_shortcut_ok and start_shortcut_ok
    
    # Completion
    print_header("Installation Complete!")
    
    if success:
        print("\n✅ WebJam has been successfully installed!")
    else:
        print("\n⚠  Installation completed with some warnings.")
        print("   Please review the messages above.")
    
    print("\nNext Steps:")
    print("   1. Restart your computer (recommended if VB-Cable was just installed)")
    print("   2. Launch WebJam from your Desktop or Start Menu")
    print("   3. Click 'Launch Jamulus' to connect to the audio server")
    print("   4. Click 'Launch Webex' to join the video meeting")
    print("   5. Use the virtual mixer to control audio levels")
    
    print(f"\n🎵 Jamulus Server: {JAMULUS_SERVER}:{JAMULUS_PORT}")
    print(f"📹 Webex Meeting: {WEBEX_URL}")
    
    print(f"\nInstallation Location: {APP_DIR}")
    
    print("\n💡 Tips:")
    print("   • Use headphones to prevent audio feedback")
    print("   • Keep video quality at 720p or lower for best performance")
    print("   • Use wired Ethernet for lowest latency")
    print("   • Save your mixer settings for different sessions")
    
    # Offer to launch
    print("\n")
    launch = input("Would you like to launch WebJam now? (y/n): ")
    
    if launch.lower() in ['y', 'yes']:
        print("\n🚀 Launching WebJam...")
        app_file = APP_DIR / "webjam_app_enhanced.py"
        launch_target = installed_app_executable() if installed_app_executable().exists() else app_file
        try:
            if launch_installed_app(app_file):
                print("   WebJam launched!")
            else:
                print("   Could not auto-launch WebJam. Start it manually from the install folder.")
        except Exception as e:
            print(f"   Error: Failed to launch: {e}")
            print(f"   You can launch it manually from: {launch_target}")
    
    print("\n" + "="*70)
    print("Thank you for installing WebJam!")
    print("For support: https://github.com/rupret007/webjam")
    print("="*70)
    
    input("\nPress Enter to exit installer...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInstallation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: Installation error: {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)
