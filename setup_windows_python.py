"""Enable the python3 command used by the marketplace on per-user Windows Python installs."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def working_python(command):
    try:
        result = subprocess.run(
            [str(command), "-I", "-c", "import sys; sys.exit(sys.version_info < (3, 10))"],
            capture_output=True, timeout=10, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def setup():
    current = shutil.which("python3.exe")
    if current and working_python(current):
        return f"Python 3 is available at {current}"

    python = Path(sys.executable).resolve()
    if (sys.prefix != sys.base_prefix or python.name.lower() != "python.exe"
            or not python.is_relative_to(Path.home().resolve()) or sys.version_info < (3, 10)):
        raise ValueError("Run this with py -3 from a per-user Python 3.10+ installation.")
    alias = python.with_name("python3.exe")
    if os.path.lexists(alias):
        raise ValueError(f"An existing {alias} needs attention. No file was replaced.")

    # The sibling name lets CPython find the same DLL, libraries and installed modules.
    # Prefer a relative symlink so in-place interpreter servicing follows python.exe.
    try:
        alias.symlink_to(python.name)
    except OSError as exc:
        if getattr(exc, "winerror", None) != 1314:  # Creating symlinks requires a privilege.
            raise
        os.link(python, alias)
    try:
        selected = shutil.which("python3.exe")
        if not selected or Path(selected).resolve() != alias.resolve() or not working_python(selected):
            raise ValueError(f"Place {python.parent} before WindowsApps on your user PATH, then retry.")
    except BaseException:
        alias.unlink()
        raise
    return f"Enabled {alias}. Both plugins can now use their shipped launch command."


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.exit("This setup is for Windows.")
    try:
        print(setup())
    except (ValueError, OSError) as error:
        sys.exit(str(error))
