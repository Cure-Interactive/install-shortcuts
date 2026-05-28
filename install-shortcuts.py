#!/usr/bin/env python3
# =============================================================================
# [🪟 Windows Installer] [🔗 Start Menu Shortcuts] Cure Interactive
# =============================================================================
# Scans the project (one directory above this script's folder) for Python GUI apps
# that contain: APP_TITLE = "..."
#
# For each match:
# - Creates a .lnk shortcut in:
#   C:\Users\<user>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Cure Interactive
#   (resolved via %APPDATA% for the current user)
# - Shortcut name is the extracted title, with trailing " - Cure Interactive" removed.
# - Uses sibling icon.ico next to each app script.
#
# Linux/macOS: NOT supported (warn + exit).
#
# Usage:
#   python install_shortcut.py
#
# Notes:
# - Uses PowerShell + WScript.Shell COM to create .lnk (no extra pip deps).
# - Prefers pythonw.exe (if present) so GUI apps don’t spawn a console.
# =============================================================================

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

APP_TITLE = "Install Shortcuts - Cure Interactive"

# =============================================================================
# Configuration
# =============================================================================

CURE_SUFFIX = " - Cure Interactive"

START_MENU_REL = Path("Microsoft") / "Windows" / "Start Menu" / "Programs" / "Cure Interactive" / "Script"

APP_TITLE_REGEX = re.compile(
  r"""(?m)^\s*APP_TITLE\s*=\s*(?P<q>['"])(?P<title>.*?)(?P=q)\s*$""",
)

INVALID_FILENAME_CHARS = re.compile(r"""[<>:"/\\|?*\x00-\x1F]""")


@dataclass(frozen=True)
class ShortcutSpec:
  name: str
  script_path: Path
  icon_path: Path
  working_dir: Path


# =============================================================================
# Public API
# =============================================================================

def main(argv: list[str]) -> int:
  """
  Entry point.

  @param {list[str]} argv
    Raw argv list, including program name.

  @returns {int}
    Process exit code (0 = success, nonzero = failure).
  """
  if os.name != "nt":
    print("[WARN] This shortcut installer only supports Windows.")
    print(f"[WARN] Detected os.name={os.name!r}. Linux/macOS are not supported.")
    return 2

  this_dir = Path(__file__).resolve().parent
  project_root = (this_dir / "..").resolve()

  start_menu_dir = resolve_start_menu_dir()
  start_menu_dir.mkdir(parents=True, exist_ok=True)

  # No python exe needed; shortcut targets the .py directly
  # python_exe = choose_python_executable(sys.executable)

  # Build specs from scan (do not add this installer script)
  specs: list[ShortcutSpec] = []

  scanned = list(scan_project_for_title_apps(project_root))
  specs.extend(scanned)

  # De-dupe by shortcut name (last one wins)
  specs = dedupe_specs_by_name(specs)

  print(f"[INFO] Project root: {project_root}")
  print(f"[INFO] Start Menu dir: {start_menu_dir}")
  # No python exe needed; shortcut targets the .py directly
  # print(f"[INFO] Python target: {python_exe}")
  print(f"[INFO] Found {len(scanned)} app(s) with self.title(...). Writing {len(specs)} shortcut(s)...")

  wrote = 0
  for spec in specs:
    lnk_path = start_menu_dir / (sanitize_filename(spec.name) + ".lnk")

    if not spec.script_path.is_file():
      print(f"[WARN] Skip (script missing): {spec.script_path}")
      continue

    icon_loc = spec.icon_path
    if not icon_loc.is_file():
      print(f"[WARN] Icon missing for '{spec.name}': {icon_loc} (shortcut will still be created)")

    try:
      create_windows_shortcut_via_powershell(
        shortcut_path=lnk_path,
        # No python exe needed; shortcut targets the .py directly
        # target_path=python_exe,
        target_path=str(spec.script_path),
        arguments=str(spec.script_path),
        working_dir=spec.working_dir,
        icon_path=icon_loc if icon_loc.is_file() else None,
      )
      wrote += 1
      print(f"[OK] {lnk_path.name} -> {spec.script_path}")
    except Exception as e:
      print(f"[ERROR] Failed to create shortcut for '{spec.name}': {e}")

  print(f"[DONE] Shortcuts written: {wrote}/{len(specs)}")
  return 0 if wrote > 0 else 1


# =============================================================================
# Scanning
# =============================================================================

def scan_project_for_title_apps(project_root: Path) -> Iterable[ShortcutSpec]:
  """
  Walk the project root recursively and find .py files containing APP_TITLE = "...".

  Constraints:
  - This script lives in install_shortcut/ so it scans *above* that folder.
  - Each app is expected to have a sibling icon.ico next to its .py file.

  @param {Path} project_root
    Project root directory.

  @yields {ShortcutSpec}
    Shortcut specs for each discovered app.
  """
  for path in walk_py_files(project_root):
    # Skip common noise dirs
    if any(part in {".git", "__pycache__", ".venv", "venv", "node_modules"} for part in path.parts):
      continue

    try:
      text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
      continue

    title = extract_title(text)
    if not title:
      continue

    title_clean = strip_cure_suffix(title)
    icon = path.parent / "icon.ico"

    yield ShortcutSpec(
      name=title_clean,
      script_path=path,
      icon_path=icon,
      working_dir=path.parent,
    )


def walk_py_files(root: Path) -> Iterable[Path]:
  """
  Recursively yield all .py files under root.

  @param {Path} root
    Root directory to walk.

  @returns {Iterable[Path]}
    Generator of file paths.
  """
  yield from root.rglob("*.py")


def extract_title(file_text: str) -> Optional[str]:
  """
  Extract the first recognized title.

  Supported format:
  - APP_TITLE = "..."

  @param {str} file_text
    File contents.

  @returns {Optional[str]}
    Title string if found.
  """
  m = APP_TITLE_REGEX.search(file_text)
  if not m:
    return None
  t = (m.group("title") or "").strip()
  return t or None


def strip_cure_suffix(title: str) -> str:
  """
  Remove trailing " - Cure Interactive" from a window title.

  @param {str} title
    Original title.

  @returns {str}
    Clean title for shortcut naming.
  """
  if title.endswith(CURE_SUFFIX):
    return title[: -len(CURE_SUFFIX)].rstrip()
  return title


def dedupe_specs_by_name(specs: list[ShortcutSpec]) -> list[ShortcutSpec]:
  """
  De-duplicate shortcut specs by name (case-insensitive). Last one wins.

  @param {list[ShortcutSpec]} specs
    Input list.

  @returns {list[ShortcutSpec]}
    De-duped list in original order (keeping the last occurrence).
  """
  seen = {}
  for i, s in enumerate(specs):
    seen[s.name.lower()] = i
  keep_idx = set(seen.values())
  return [s for i, s in enumerate(specs) if i in keep_idx]


# =============================================================================
# Shortcut creation (PowerShell / WScript.Shell)
# =============================================================================

def resolve_start_menu_dir() -> Path:
  """
  Resolve the per-user Start Menu Programs\\Cure Interactive directory.

  Uses %APPDATA% so it matches the active user.

  @returns {Path}
    Destination directory for shortcuts.
  """
  appdata = os.environ.get("APPDATA")
  if not appdata:
    raise RuntimeError("APPDATA environment variable is missing; cannot resolve Start Menu path.")
  return Path(appdata) / START_MENU_REL


# No python exe needed; shortcut targets the .py directly
# def choose_python_executable(python_exe: str) -> str:
#   """
#   Prefer pythonw.exe when available to avoid a console window for GUI apps.

#   @param {str} python_exe
#     Current interpreter path (sys.executable).

#   @returns {str}
#     Path to pythonw.exe if found next to python.exe, else python_exe.
#   """
#   p = Path(python_exe)
#   if p.name.lower() == "python.exe":
#     pythonw = p.with_name("pythonw.exe")
#     if pythonw.is_file():
#       return str(pythonw)
#   # If it’s already pythonw.exe or something else, keep it.
#   return python_exe


def sanitize_filename(name: str) -> str:
  """
  Produce a Windows-safe filename stem.

  @param {str} name
    Desired name.

  @returns {str}
    Sanitized stem (no extension).
  """
  s = INVALID_FILENAME_CHARS.sub("-", name).strip().strip(".")
  # Prevent empty
  return s if s else "Cure Interactive App"


def ps_escape_single_quoted(value: str) -> str:
  """
  Escape a string for use inside a PowerShell single-quoted string.

  PowerShell single-quote escaping is done by doubling single quotes.

  @param {str} value
    Raw value.

  @returns {str}
    Escaped value.
  """
  return value.replace("'", "''")


def create_windows_shortcut_via_powershell(
  *,
  shortcut_path: Path,
  target_path: str,
  arguments: str,
  working_dir: Path,
  icon_path: Optional[Path],
) -> None:
  """
  Create or overwrite a .lnk shortcut using PowerShell + WScript.Shell.

  @param {Path} shortcut_path
    Full path to the .lnk file to create.
  @param {str} target_path
    Executable path (e.g., pythonw.exe).
  @param {str} arguments
    Arguments passed to target (we pass the .py script path).
  @param {Path} working_dir
    Working directory for the shortcut.
  @param {Optional[Path]} icon_path
    .ico file path; if None, icon is not set.

  @returns {None}
  """
  sp = ps_escape_single_quoted(str(shortcut_path))
  tp = ps_escape_single_quoted(str(target_path))
  arg = ps_escape_single_quoted(str(arguments))
  wd = ps_escape_single_quoted(str(working_dir))

  # IconLocation format: "C:\path\icon.ico,0"
  icon_line = ""
  if icon_path:
    ic = ps_escape_single_quoted(str(icon_path))
    icon_line = f"$s.IconLocation = '{ic},0';"

  ps = (
    "$w = New-Object -ComObject WScript.Shell;"
    f"$s = $w.CreateShortcut('{sp}');"
    f"$s.TargetPath = '{tp}';"
    f"$s.Arguments = '\"{arg}\"';"
    f"$s.WorkingDirectory = '{wd}';"
    + icon_line +
    "$s.Save();"
  )

  # Use -Command with a compact one-liner to avoid temp files.
  subprocess.run(
    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
    check=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
    text=True,
  )


# =============================================================================
# Bootstrap
# =============================================================================

if __name__ == "__main__":
  raise SystemExit(main(sys.argv))
