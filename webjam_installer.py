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
from pathlib import Path
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

# ====== Helpers ======
def print_header(text):
    """Print a formatted header"""
    print("\n" + "="*70)
    print(f"  {text}")
    print("="*70)

def print_step(step, text):
    """Print a step indicator"""
    print(f"\n[{step}/5] {text}")
    print("-" * 70)

def run(cmd, check=False, shell=False):
    """Run a command and return result"""
    return subprocess.run(cmd, check=check, shell=shell, capture_output=True, text=True)

def is_admin():
    """Check if running with admin privileges"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

def elevate_if_needed():
    """Re-launch with admin privileges if needed"""
    if not is_admin():
        print("\n⚠️  WebJam installer requires administrator privileges.")
        print("   Requesting elevation...")
        params = " ".join([f'"{a}"' for a in sys.argv[1:]])
        try:
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, f'"{__file__}" {params}', None, 1
            )
        except Exception as e:
            print(f"   Failed to elevate: {e}")
            print("   Please run the installer as Administrator.")
            input("\nPress Enter to exit...")
        sys.exit(0)

def find_jamulus():
    """Find installed Jamulus executable"""
    for p in JAMULUS_CANDIDATES:
        if Path(p).exists():
            return p
    return None

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

# ====== VB-Cable Installation ======
def vb_cable_present():
    """Check if VB-Cable is installed"""
    ps = r'''
    $reg = "HKLM:\SYSTEM\CurrentControlSet\Enum\SWD\MMDEVAPI"
    if (Test-Path $reg) {
      $all = Get-ChildItem $reg -Recurse -ErrorAction SilentlyContinue | 
             Get-ItemProperty -ErrorAction SilentlyContinue
      if ($all.FriendlyName -match "VB-Audio Virtual Cable|CABLE Input|CABLE Output") { 
        exit 0 
      } else { 
        exit 1 
      }
    } else { 
      exit 1 
    }
    '''
    result = run(["powershell", "-NoP", "-NonI", "-Command", ps])
    return result.returncode == 0

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
        print("   ⚠️  VB folder not found. Skipping VB-Cable installation.")
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
        print("   ⚠️  VB-Cable installer not found.")
        return False
    
    exe_copy = copy_resource(exe_candidates[0].relative_to(HERE), WORK)
    print(f"   Launching VB-Cable installer (you may need to click 'Install Driver')...")
    
    try:
        subprocess.Popen([str(exe_copy)], shell=False)
    except Exception as e:
        print(f"   ❌ Failed to launch installer: {e}")
        return False
    
    print(f"   Waiting up to {VBC_MAX_WAIT_SECS//60} minutes for installation...")
    
    if wait_until(vb_cable_present, VBC_MAX_WAIT_SECS, VBC_POLL_INTERVAL, "VB-Cable"):
        print("   ✓ VB-Cable installed successfully")
        time.sleep(2)
        return True
    else:
        print("   ⚠️  VB-Cable not detected after waiting.")
        print("   You may need to reboot or manually complete the installation.")
        return False

# ====== Jamulus Installation ======
def jamulus_installer_path():
    """Find Jamulus installer"""
    files = sorted(glob.glob(str(HERE / "jamulus*_win*.exe")))
    return Path(files[0]) if files else None

def install_jamulus():
    """Install Jamulus client"""
    print("\n🎵 Checking Jamulus (Low-Latency Audio)...")
    
    if find_jamulus():
        print("   ✓ Jamulus already installed")
        return True
    
    installer = jamulus_installer_path()
    if not installer or not installer.exists():
        print("   ⚠️  Jamulus installer not found (jamulus*_win*.exe)")
        print("   You can download Jamulus from: https://jamulus.io")
        return False
    
    inst_copy = copy_resource(installer.name, WORK)
    print(f"   Installing Jamulus from {installer.name}...")
    
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
    
    print("   ⚠️  Jamulus not detected after waiting.")
    print("   Please complete the installation manually and run this installer again.")
    return False

# ====== Audio Configuration ======
def configure_audio_devices():
    """Configure default audio devices"""
    print("\n🎛️  Configuring Audio Devices...")
    
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
    print("\n🐍 Installing Python Dependencies...")
    
    requirements_file = HERE / "requirements.txt"
    if not requirements_file.exists():
        print("   ⚠️  requirements.txt not found, skipping Python dependencies")
        return False
    
    try:
        print("   Installing customtkinter for modern UI...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "customtkinter"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            print("   ✓ Python dependencies installed")
            return True
        else:
            print("   ⚠️  Could not install dependencies (not critical)")
            return False
    except Exception as e:
        print(f"   ⚠️  Error installing dependencies: {e}")
        return False

# ====== Application Installation ======
def install_webjam_app():
    """Install WebJam application files"""
    print("\n📦 Installing WebJam Application...")
    
    # Copy application files
    app_files = [
        "webjam_app_enhanced.py",
        "jamulus_controller.py",
        "requirements.txt",
        "README.md"
    ]
    
    for filename in app_files:
        src = HERE / filename
        if src.exists():
            dst = APP_DIR / filename
            shutil.copy2(src, dst)
            print(f"   ✓ Copied {filename}")
    
    print(f"   ✓ Application installed to {APP_DIR}")
    return True

# ====== Shortcut Creation ======
def create_desktop_shortcut():
    """Create desktop shortcut to launch WebJam"""
    print("\n🔗 Creating Desktop Shortcut...")
    
    desktop = Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))
    lnk_path = desktop / "WebJam.lnk"
    
    # Target the installed Python app
    target = sys.executable
    app_file = APP_DIR / "webjam_app_enhanced.py"
    
    ps = f'''
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut("{lnk_path}")
    $Shortcut.TargetPath = "{target}"
    $Shortcut.Arguments = '"{app_file}"'
    $Shortcut.WorkingDirectory = "{APP_DIR}"
    $Shortcut.IconLocation = "{target},0"
    $Shortcut.Description = "WebJam Music Collaboration Platform"
    $Shortcut.Save()
    '''
    
    run(["powershell", "-NoP", "-NonI", "-Command", ps])
    print(f"   ✓ Shortcut created: {lnk_path}")

def create_start_menu_shortcut():
    """Create Start Menu shortcut"""
    print("   Creating Start Menu shortcut...")
    
    start_menu = Path(os.environ.get("APPDATA")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    lnk_path = start_menu / "WebJam.lnk"
    
    target = sys.executable
    app_file = APP_DIR / "webjam_app_enhanced.py"
    
    ps = f'''
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut("{lnk_path}")
    $Shortcut.TargetPath = "{target}"
    $Shortcut.Arguments = '"{app_file}"'
    $Shortcut.WorkingDirectory = "{APP_DIR}"
    $Shortcut.IconLocation = "{target},0"
    $Shortcut.Description = "WebJam Music Collaboration Platform"
    $Shortcut.Save()
    '''
    
    run(["powershell", "-NoP", "-NonI", "-Command", ps])
    print(f"   ✓ Start Menu shortcut created")

# ====== Main Installation ======
def main():
    """Main installation process"""
    print_header("WebJam Enhanced Installer")
    print("\n🎵 Welcome to WebJam - Music Collaboration Platform")
    print("\nThis installer will set up:")
    print("  • VB-Cable (Virtual Audio Device)")
    print("  • Jamulus (Low-Latency Audio Client)")
    print("  • WebJam GUI Application")
    print("  • Audio device configuration")
    print("  • Desktop shortcuts")
    
    print("\n⚠️  Administrator privileges required for driver installation")
    
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
        print("\n⚠️  Cannot continue without Jamulus. Please install it manually.")
        input("\nPress Enter to exit...")
        return
    
    print_step(3, "Configuring Audio Devices")
    configure_audio_devices()
    
    print_step(4, "Installing WebJam Application")
    install_webjam_app()
    install_python_dependencies()
    
    print_step(5, "Creating Shortcuts")
    create_desktop_shortcut()
    create_start_menu_shortcut()
    
    # Completion
    print_header("Installation Complete!")
    
    if success:
        print("\n✅ WebJam has been successfully installed!")
    else:
        print("\n⚠️  Installation completed with some warnings.")
        print("   Please review the messages above.")
    
    print("\n📝 Next Steps:")
    print("   1. Restart your computer (recommended if VB-Cable was just installed)")
    print("   2. Launch WebJam from your Desktop or Start Menu")
    print("   3. Click 'Launch Jamulus' to connect to the audio server")
    print("   4. Click 'Launch Webex' to join the video meeting")
    print("   5. Use the virtual mixer to control audio levels")
    
    print(f"\n🎵 Jamulus Server: {JAMULUS_SERVER}:{JAMULUS_PORT}")
    print(f"📹 Webex Meeting: {WEBEX_URL}")
    
    print(f"\n📁 Installation Location: {APP_DIR}")
    
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
        try:
            subprocess.Popen([sys.executable, str(app_file)])
            print("   ✓ WebJam launched!")
        except Exception as e:
            print(f"   ❌ Failed to launch: {e}")
            print(f"   You can launch it manually from: {app_file}")
    
    print("\n" + "="*70)
    print("Thank you for installing WebJam!")
    print("For support: https://github.com/yourusername/webjam")
    print("="*70)
    
    input("\nPress Enter to exit installer...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInstallation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Installation error: {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)

