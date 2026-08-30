import sys
import subprocess
import venv
import os
import json
from pathlib import Path

def check_python_version():
    """Checks if the Python version is 3.11 or higher."""
    print("Checking Python version...")
    if sys.version_info < (3, 11):
        print("Error: Python 3.11 or higher is required.")
        sys.exit(1)
    print("Python version check passed.")

def create_virtual_environment():
    """Creates a virtual environment if it doesn't exist."""
    venv_dir = Path(".venv")
    if not venv_dir.exists():
        print("Creating virtual environment...")
        venv.create(venv_dir, with_pip=True)
        print("Virtual environment created.")
    else:
        print("Virtual environment already exists.")

def get_pip_path():
    """Gets the path to the pip executable in the virtual environment."""
    if os.name == 'nt':
        return Path(".venv") / "Scripts" / "pip.exe"
    else:
        return Path(".venv") / "bin" / "pip"

def install_python_dependencies():
    """Installs Python dependencies from requirements.txt."""
    print("Installing Python dependencies...")
    pip_path = get_pip_path()
    subprocess.check_call([str(pip_path), "install", "-r", "backend/requirements.txt"])
    print("Python dependencies installed.")

def install_frontend_dependencies():
    """Installs frontend dependencies using npm."""
    print("Installing frontend dependencies...")
    frontend_dir = Path("frontend")
    # Use shell=True on Windows to correctly resolve 'npm'
    subprocess.check_call("npm install", shell=True, cwd=frontend_dir)
    print("Frontend dependencies installed.")

def clone_repositories():
    """Clones required Git repositories if they don't exist."""
    print("Cloning external tool repositories...")
    tools_dir = Path("tools")
    tools_dir.mkdir(exist_ok=True)

    dependencies = [
        {
            "name": "LichtFeld Studio",
            "repo": "https://github.com/MrNeRF/LichtFeld-Studio",
            "local_path": "tools/lichtfeld-studio",
        },
        {
            "name": "SuperSplat (local fallback)",
            "repo": "https://github.com/playcanvas/supersplat",
            "local_path": "tools/supersplat",
        }
    ]

    for dep in dependencies:
        local_path = Path(dep["local_path"])
        if not local_path.exists():
            print(f"Cloning {dep['name']}...")
            subprocess.check_call(["git", "clone", dep["repo"], str(local_path)])
            print(f"{dep['name']} cloned.")
        else:
            print(f"{dep['name']} already exists.")

def find_executable(name, search_paths):
    """Finds an executable in common installation directories."""
    for path in search_paths:
        for root, _, files in os.walk(path):
            if name in files:
                return str(Path(root) / name)
    # Check if it's in PATH
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if (Path(p) / name).exists():
            return str(Path(p) / name)
    return None

def create_config_file():
    """Creates a config.json file with auto-detected tool paths."""
    print("Creating config.json...")
    
    # Default config structure from pydantic models
    config = {
        "tools": {
            "rc_exe_path": None,
            "lfs_exe_path": None,
            "ffmpeg_path": "ffmpeg",
            "supersplat_url": "https://superspl.at/editor"
        }
    }

    # Auto-detect RealityScan.exe on Windows
    if os.name == 'nt':
        rc_path = find_executable("RealityScan.exe", ["C:/Program Files/Epic Games"])
        config["tools"]["rc_exe_path"] = rc_path
        if not rc_path:
            print("Warning: RealityScan.exe not found. You can specify the path manually in config.json.")
    
    # Placeholder for LichtFeld Studio (user needs to build it)
    print("Info: Path to LichtFeld-Studio.exe must be set manually in config.json after building.")

    # Auto-detect ffmpeg
    ffmpeg_path = find_executable("ffmpeg.exe" if os.name == 'nt' else "ffmpeg", os.environ.get("PATH", "").split(os.pathsep))
    config["tools"]["ffmpeg_path"] = ffmpeg_path if ffmpeg_path else "ffmpeg"
    if not ffmpeg_path:
        print("Warning: ffmpeg not found in PATH. Please install it or specify the path in config.json.")

    with open("config.json", "w") as f:
        json.dump(config, f, indent=4)
    print("config.json created. Please review and complete the paths.")

def main():
    """Main setup script execution."""
    check_python_version()
    create_virtual_environment()
    install_python_dependencies()
    install_frontend_dependencies()
    clone_repositories()
    create_config_file()
    print("\nSetup complete! To start the application, run: start.bat (Windows) or ./start.sh (Linux/macOS)")

if __name__ == "__main__":
    main()
