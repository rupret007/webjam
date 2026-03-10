"""
Build script for WebJam
Creates standalone executable using PyInstaller
"""

import subprocess
import sys
import shutil
from pathlib import Path

def pyinstaller_prefix() -> list[str]:
    """Invoke PyInstaller via the active interpreter."""
    return [sys.executable, "-m", "PyInstaller"]

def check_pyinstaller():
    """Check if PyInstaller is installed."""
    try:
        import PyInstaller  # noqa: F401
        print("PyInstaller is installed")
        return True
    except ImportError:
        print("PyInstaller not found")
        print("\nInstalling PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
        return True

def build_installer():
    """Build the installer executable"""
    print("\n" + "="*70)
    print("Building WebJam Installer")
    print("="*70)
    
    cmd = pyinstaller_prefix() + [
        "--onefile",
        "--name=WebJam_Installer",
        "--icon=NONE",
        "--add-data=VB;VB",
        "--add-data=jamulus_3.11.0_win.exe;.",
        "--add-data=webjam_app_enhanced.py;.",
        "--add-data=jamulus_controller.py;.",
        "--add-data=requirements.txt;.",
        "--add-data=README.md;.",
        "--console",
        "webjam_installer.py"
    ]
    
    print("\nRunning PyInstaller...")
    print(" ".join(cmd))
    
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("\n✅ Installer built successfully!")
        print(f"   Location: dist/WebJam_Installer.exe")
    else:
        print("\n❌ Build failed")
        return False
    
    return True

def build_app():
    """Build the main application executable"""
    print("\n" + "="*70)
    print("Building WebJam Application")
    print("="*70)
    
    cmd = pyinstaller_prefix() + [
        "--onefile",
        "--windowed",  # No console for the GUI app
        "--name=WebJam",
        "--icon=NONE",
        "--add-data=jamulus_controller.py;.",
        "--hidden-import=customtkinter",
        "webjam_app_enhanced.py"
    ]
    
    print("\nRunning PyInstaller...")
    print(" ".join(cmd))
    
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("\n✅ Application built successfully!")
        print(f"   Location: dist/WebJam.exe")
    else:
        print("\n❌ Build failed")
        return False
    
    return True

def create_distribution():
    """Create distribution folder with all necessary files"""
    print("\n" + "="*70)
    print("Creating Distribution Package")
    print("="*70)
    
    dist_dir = Path("dist_package")
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir()
    
    installer_path = Path("dist/WebJam_Installer.exe")
    if not installer_path.exists():
        print(f"❌ Missing build artifact: {installer_path}")
        return False
    shutil.copy(installer_path, dist_dir)
    print(f"✓ Copied WebJam_Installer.exe")
    
    # Copy VB folder
    if Path("VB").exists():
        shutil.copytree("VB", dist_dir / "VB")
        print(f"✓ Copied VB directory")
    
    # Copy Jamulus installer
    jamulus = Path("jamulus_3.11.0_win.exe")
    if jamulus.exists():
        shutil.copy(jamulus, dist_dir)
        print(f"✓ Copied Jamulus installer")
    
    # Copy Python files for source distribution
    python_files = [
        "webjam_app_enhanced.py",
        "webjam_app.py",
        "jamulus_controller.py",
        "webjam_installer.py",
        "requirements.txt",
        "README.md"
    ]
    
    for file in python_files:
        if Path(file).exists():
            shutil.copy(file, dist_dir)
            print(f"✓ Copied {file}")
    
    # Create README for distribution
    readme_content = """
WebJam Music Collaboration Platform
====================================

QUICK START (For End Users):
-----------------------------
1. Run WebJam_Installer.exe as Administrator
2. Follow the installation wizard
3. Launch WebJam from Desktop shortcut
4. Enjoy music collaboration!

FOR DEVELOPERS:
---------------
This package includes both:
- Pre-built installer (WebJam_Installer.exe)
- Source code (Python files)

To run from source:
1. Install Python 3.8+
2. pip install -r requirements.txt
3. python webjam_app_enhanced.py

To build from source:
1. pip install pyinstaller
2. python build_webjam.py

WHAT'S INCLUDED:
----------------
• WebJam_Installer.exe - One-click installer
• VB/ - VB-Cable audio driver files
• jamulus_3.11.0_win.exe - Jamulus audio client
• Python source files
• Documentation

For more information, see README.md
"""
    
    with open(dist_dir / "README_DISTRIBUTION.txt", "w") as f:
        f.write(readme_content)
    print(f"✓ Created README_DISTRIBUTION.txt")
    
    print(f"\n✅ Distribution package created in: {dist_dir}")
    print(f"\nYou can now distribute the entire '{dist_dir}' folder")
    
    return True

def main():
    """Main build process"""
    print("="*70)
    print("WebJam Build System")
    print("="*70)
    print("\nThis will build:")
    print("  1. WebJam Installer (with bundled dependencies)")
    print("  2. WebJam Application (standalone GUI)")
    print("  3. Distribution package")
    
    input("\nPress Enter to start build process...")
    
    # Check PyInstaller
    if not check_pyinstaller():
        return
    
    # Clean previous builds
    print("\n🧹 Cleaning previous builds...")
    for dir_name in ["build", "dist", "dist_package"]:
        dir_path = Path(dir_name)
        if dir_path.exists():
            shutil.rmtree(dir_path)
            print(f"   Removed {dir_name}/")
    
    for spec_file in Path(".").glob("*.spec"):
        spec_file.unlink()
        print(f"   Removed {spec_file}")
    
    # Build installer
    print("\n" + "="*70)
    print("Step 1/3: Building Installer")
    print("="*70)
    
    if not build_installer():
        print("\n❌ Build process failed at installer stage")
        return
    
    # Build application
    print("\n" + "="*70)
    print("Step 2/3: Building Application")
    print("="*70)
    
    if not build_app():
        print("\n❌ Build process failed at application stage")
        return
    
    # Create distribution
    print("\n" + "="*70)
    print("Step 3/3: Creating Distribution Package")
    print("="*70)
    
    if not create_distribution():
        print("\n❌ Build process failed at distribution stage")
        return
    
    # Success!
    print("\n" + "="*70)
    print("BUILD COMPLETE!")
    print("="*70)
    
    print("\n✅ All builds completed successfully!")
    print("\n📦 Distribution package is ready in: dist_package/")
    print("\nYou can now:")
    print("  • Test the installer: dist_package/WebJam_Installer.exe")
    print("  • Share the entire dist_package/ folder with users")
    print("  • Create a ZIP of dist_package/ for easy distribution")
    
    # Offer to create ZIP
    create_zip = input("\nCreate ZIP file for distribution? (y/n): ")
    if create_zip.lower() in ['y', 'yes']:
        print("\nCreating ZIP file...")
        shutil.make_archive("WebJam_Distribution", 'zip', "dist_package")
        print("✓ Created WebJam_Distribution.zip")
    
    print("\n🎉 Build process complete!")
    print("="*70)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nBuild cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Build error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

