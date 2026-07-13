# Windows packaging inventory

The Windows artifact is built with pinned Python 3.12.10 and PyInstaller
6.21.0. The workflow records the exact installed build environment and ships
the following files beside `BananaGuard.exe`:

- `PYTHON-LICENSE.txt` from the exact CPython distribution used to build;
- `PYINSTALLER-COPYING.txt`, including the bootloader exception and runtime
  hook license terms from the exact PyInstaller wheel; and
- `PYTHON-BUILD-INVENTORY.txt` from `pip list --format=freeze`.

These platform notices supplement the project, vendored JavaScript, and font
licenses served by the dashboard. Build tools are inventoried separately and
are not represented as dashboard runtime dependencies.
