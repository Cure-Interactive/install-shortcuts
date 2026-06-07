#!/usr/bin/env python3
# =============================================================================
# Windows Start Menu shortcut sync for Cure Interactive tools.
# =============================================================================
# Scans the directory above this script's folder for Python GUI apps that define:
#   APP_TITLE = "..."
#
# For each match, creates or updates a .lnk shortcut in:
#   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Cure Interactive\Script
#
# The tool records managed shortcuts in a manifest beside the .lnk files. Future
# runs remove only shortcuts listed in that manifest when the matching app is no
# longer available, so manually-created shortcuts in the same folder are left
# alone.
# =============================================================================

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

APP_TITLE = "Install Shortcuts - Cure Interactive"

CURE_SUFFIX = " - Cure Interactive"
START_MENU_REL = Path("Microsoft") / "Windows" / "Start Menu" / "Programs" / "Cure Interactive" / "Script"
MANIFEST_FILENAME = "_install-shortcuts-manifest.json"

APP_TITLE_REGEX = re.compile(
  r"""(?m)^\s*APP_TITLE\s*=\s*(?P<q>['"])(?P<title>.*?)(?P=q)\s*$""",
)

INVALID_FILENAME_CHARS = re.compile(r"""[<>:"/\\|?*\x00-\x1F]""")
SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules"}


@dataclass(frozen=True)
class ShortcutSpec:
  name: str
  script_path: Path
  icon_path: Path
  working_dir: Path


def main(argv: list[str]) -> int:
  """
  Sync Start Menu shortcuts for available Cure Interactive apps.
  """
  if os.name != "nt":
    print("[WARN] This shortcut installer only supports Windows.")
    print(f"[WARN] Detected os.name={os.name!r}. Linux/macOS are not supported.")
    return 2

  this_dir = Path(__file__).resolve().parent
  project_root = (this_dir / "..").resolve()

  start_menu_dir = resolve_start_menu_dir()
  start_menu_dir.mkdir(parents=True, exist_ok=True)

  scanned = list(scan_project_for_title_apps(project_root))
  specs = dedupe_specs_by_name(scanned)

  print(f"[INFO] Project root: {project_root}")
  print(f"[INFO] Start Menu dir: {start_menu_dir}")
  print(f"[INFO] Found {len(scanned)} app(s) with APP_TITLE. Syncing {len(specs)} shortcut(s)...")

  desired_shortcuts = {
    shortcut_path_for_spec(start_menu_dir, spec)
    for spec in specs
    if spec.script_path.is_file()
  }
  removed = prune_stale_shortcuts(
    start_menu_dir=start_menu_dir,
    previous_manifest=load_manifest(start_menu_dir),
    desired_shortcuts=desired_shortcuts,
  )

  wrote = 0
  installed: list[dict] = []

  for spec in specs:
    lnk_path = shortcut_path_for_spec(start_menu_dir, spec)

    if not spec.script_path.is_file():
      print(f"[WARN] Skip (script missing): {spec.script_path}")
      continue

    icon_loc = spec.icon_path
    if not icon_loc.is_file():
      print(f"[WARN] Icon missing for '{spec.name}': {icon_loc} (shortcut will still be created)")

    try:
      create_windows_shortcut_via_powershell(
        shortcut_path=lnk_path,
        target_path=str(spec.script_path),
        arguments="",
        working_dir=spec.working_dir,
        icon_path=icon_loc if icon_loc.is_file() else None,
      )
      wrote += 1
      installed.append(manifest_entry(spec, lnk_path, icon_loc))
      print(f"[OK] {lnk_path.name} -> {spec.script_path}")
    except Exception as e:
      print(f"[ERROR] Failed to create shortcut for '{spec.name}': {e}")

  save_manifest(start_menu_dir, project_root=project_root, installed=installed)
  print(f"[DONE] Shortcuts written: {wrote}/{len(specs)}; stale removed: {removed}")
  return 0 if wrote > 0 else 1


def scan_project_for_title_apps(project_root: Path) -> Iterable[ShortcutSpec]:
  """
  Walk the project root and find .py files containing APP_TITLE = "...".
  """
  for path in walk_py_files(project_root):
    if any(part in SKIP_DIR_NAMES for part in path.parts):
      continue

    try:
      text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
      continue

    title = extract_title(text)
    if not title:
      continue

    yield ShortcutSpec(
      name=strip_cure_suffix(title),
      script_path=path,
      icon_path=path.parent / "icon.ico",
      working_dir=path.parent,
    )


def walk_py_files(root: Path) -> Iterable[Path]:
  yield from root.rglob("*.py")


def extract_title(file_text: str) -> Optional[str]:
  m = APP_TITLE_REGEX.search(file_text)
  if not m:
    return None
  title = (m.group("title") or "").strip()
  return title or None


def strip_cure_suffix(title: str) -> str:
  if title.endswith(CURE_SUFFIX):
    return title[: -len(CURE_SUFFIX)].rstrip()
  return title


def dedupe_specs_by_name(specs: list[ShortcutSpec]) -> list[ShortcutSpec]:
  """
  De-duplicate shortcut specs by name, keeping the last match.
  """
  seen = {}
  for index, spec in enumerate(specs):
    seen[spec.name.lower()] = index
  keep_indexes = set(seen.values())
  return [spec for index, spec in enumerate(specs) if index in keep_indexes]


def resolve_start_menu_dir() -> Path:
  appdata = os.environ.get("APPDATA")
  if not appdata:
    raise RuntimeError("APPDATA environment variable is missing; cannot resolve Start Menu path.")
  return Path(appdata) / START_MENU_REL


def sanitize_filename(name: str) -> str:
  filename = INVALID_FILENAME_CHARS.sub("-", name).strip().strip(".")
  return filename if filename else "Cure Interactive App"


def shortcut_path_for_spec(start_menu_dir: Path, spec: ShortcutSpec) -> Path:
  return (start_menu_dir / (sanitize_filename(spec.name) + ".lnk")).resolve()


def manifest_path(start_menu_dir: Path) -> Path:
  return start_menu_dir / MANIFEST_FILENAME


def load_manifest(start_menu_dir: Path) -> dict:
  path = manifest_path(start_menu_dir)
  if not path.is_file():
    return {"version": 1, "shortcuts": []}

  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except Exception:
    return {"version": 1, "shortcuts": []}

  if not isinstance(data, dict):
    return {"version": 1, "shortcuts": []}
  if not isinstance(data.get("shortcuts"), list):
    data["shortcuts"] = []
  return data


def save_manifest(start_menu_dir: Path, *, project_root: Path, installed: list[dict]) -> None:
  data = {
    "version": 1,
    "project_root": str(project_root),
    "synced_at": dt.datetime.now().isoformat(timespec="seconds"),
    "shortcuts": installed,
  }
  manifest_path(start_menu_dir).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def manifest_entry(spec: ShortcutSpec, shortcut_path: Path, icon_path: Path) -> dict:
  return {
    "name": spec.name,
    "shortcut_path": str(shortcut_path),
    "script_path": str(spec.script_path.resolve()),
    "icon_path": str(icon_path.resolve()) if icon_path.is_file() else "",
    "working_dir": str(spec.working_dir.resolve()),
  }


def prune_stale_shortcuts(
  *,
  start_menu_dir: Path,
  previous_manifest: dict,
  desired_shortcuts: set[Path],
) -> int:
  """
  Remove shortcuts this tool previously installed but no longer wants.
  """
  start_menu_root = start_menu_dir.resolve()
  removed = 0

  for entry in previous_manifest.get("shortcuts", []):
    if not isinstance(entry, dict):
      continue

    raw_shortcut = str(entry.get("shortcut_path", "") or "")
    if not raw_shortcut:
      continue

    try:
      shortcut_path = Path(raw_shortcut).resolve()
    except Exception:
      continue

    if shortcut_path in desired_shortcuts:
      continue
    if shortcut_path.suffix.lower() != ".lnk":
      continue
    if not is_relative_to(shortcut_path, start_menu_root):
      continue
    if not shortcut_path.exists():
      continue

    shortcut_path.unlink()
    removed += 1
    print(f"[OK] Removed stale shortcut: {shortcut_path.name}")

  return removed


def is_relative_to(path: Path, root: Path) -> bool:
  try:
    path.relative_to(root)
    return True
  except ValueError:
    return False


def ps_escape_single_quoted(value: str) -> str:
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
  """
  shortcut = ps_escape_single_quoted(str(shortcut_path))
  target = ps_escape_single_quoted(str(target_path))
  args = ps_escape_single_quoted(str(arguments))
  workdir = ps_escape_single_quoted(str(working_dir))

  icon_line = ""
  if icon_path:
    icon = ps_escape_single_quoted(str(icon_path))
    icon_line = f"$s.IconLocation = '{icon},0';"

  ps = (
    "$w = New-Object -ComObject WScript.Shell;"
    f"$s = $w.CreateShortcut('{shortcut}');"
    f"$s.TargetPath = '{target}';"
    f"$s.Arguments = '{args}';"
    f"$s.WorkingDirectory = '{workdir}';"
    + icon_line +
    "$s.Save();"
  )

  subprocess.run(
    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
    check=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
    text=True,
  )


if __name__ == "__main__":
  raise SystemExit(main(sys.argv))
