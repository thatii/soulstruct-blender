"""Install all scripts into Blender, along with Soulstruct.

The Blender script (`io_flver.py`) will ensure that the mini-Soulstruct module is added to the Blender path. Note that
you will have to restart Blender to see any changes to this mini-module, as `Reload Scripts` in Blender will not
re-import it.
"""
import shutil
import sys
from pathlib import Path

from soulstruct.utilities.files import PACKAGE_PATH

from soulstruct_havok.utilities import PACKAGE_PATH as HAVOK_PACKAGE_PATH


def install(blender_scripts_dir: str | Path, update_soulstruct_module=False, update_third_party_modules=False):
    """`blender_scripts_dir` should be the `scripts` folder in a specific version of Blender inside your AppData.

    For example:
        `install(Path("~/AppData/Roaming/Blender/2.93/scripts").expanduser())`
    """
    blender_scripts_dir = Path(blender_scripts_dir)
    if blender_scripts_dir.name != "scripts":
        raise ValueError(
            f"Expected Blender install directory to be called 'scripts'. Given path: {blender_scripts_dir}"
        )

    if update_soulstruct_module:
        # Full Soulstruct install, now that Blender 3.3 supports Python 3.10.
        print("# Installing Soulstruct module into Blender...")
        shutil.rmtree(blender_scripts_dir / "modules/soulstruct", ignore_errors=True)
        # Removal may not be complete if Blender is open, particularly as `soulstruct.log` may not be deleted.
        shutil.copytree(
            PACKAGE_PATH(),
            blender_scripts_dir / "modules/soulstruct",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("*.pyc", "__pycache__", "oo2core_6_win64.dll"),
        )

        # Copy over `oo2core_6_win64.dll` if it exists and isn't already in destination folder.
        oo2core_dll = PACKAGE_PATH("oo2core_6_win64.dll")
        if oo2core_dll.is_file() and not (blender_scripts_dir / "modules/soulstruct/oo2core_6_win64.dll").is_file():
            shutil.copy(oo2core_dll, blender_scripts_dir / "modules/soulstruct")

        if update_third_party_modules:
            install_site_package("colorama", blender_scripts_dir / "modules/colorama")
            install_site_package("scipy", blender_scripts_dir / "modules/scipy")
            install_site_package("scipy.libs", blender_scripts_dir / "modules/scipy.libs")

        if HAVOK_PACKAGE_PATH is not None:
            print("# Installing Soulstruct-Havok module into Blender...")
            shutil.rmtree(blender_scripts_dir / "modules/soulstruct_havok", ignore_errors=True)
            # Removal may not be complete if Blender is open, particularly as `soulstruct.log` may not be deleted.
            shutil.copytree(HAVOK_PACKAGE_PATH(), blender_scripts_dir / "modules/soulstruct_havok", dirs_exist_ok=True)

    # Install actual Blender scripts.
    this_dir = Path(__file__).parent
    blender_addons_dir = blender_scripts_dir / "addons"
    blender_module_dir = blender_addons_dir / "io_soulstruct"
    blender_module_dir.mkdir(exist_ok=True, parents=True)
    shutil.rmtree(blender_module_dir, ignore_errors=True)
    shutil.copytree(this_dir / "io_soulstruct", blender_module_dir)
    print(f"# Blender addon `io_soulstruct` installed to '{blender_addons_dir}'.")


def install_site_package(dir_name: str, destination_dir: Path):
    exe_path = Path(sys.executable)
    site_packages_dir = exe_path.parent / "Lib/site-packages"
    if not site_packages_dir.is_dir():  # exe could be in `Scripts` subfolder (venv)
        site_packages_dir = exe_path.parent / "../Lib/site-packages"
        if not site_packages_dir.is_dir():
            raise FileNotFoundError(f"Could not find site-packages directory for Python executable: {exe_path}.")
    package_dir = site_packages_dir / dir_name
    if not package_dir.is_dir():
        raise FileNotFoundError(f"Could not find site-package directory: {package_dir}.")
    print(f"# Installing site-package `{dir_name}` into Blender...")
    shutil.copytree(package_dir, destination_dir, dirs_exist_ok=True)


def main(args):
    match args:
        case[blender_scripts_directory, "--updateSoulstruct", "--updateThirdParty"]:
            install(blender_scripts_directory, update_soulstruct_module=True, update_third_party_modules=True)
        case [blender_scripts_directory, "--updateSoulstruct"]:
            install(blender_scripts_directory, update_soulstruct_module=True)
        case [blender_scripts_directory, "--updateThirdParty"]:
            install(blender_scripts_directory, update_third_party_modules=True)
        case [blender_scripts_directory]:
            install(blender_scripts_directory, update_soulstruct_module=False)
        case _:
            print(
                f"INVALID ARGUMENTS: {sys.argv}\n"
                f"Usage: `python install_addon.py [blender_scripts_directory] "
                f"[--updateSoulstruct] [--updateThirdParty]`"
            )


if __name__ == '__main__':
    main(sys.argv[1:])
