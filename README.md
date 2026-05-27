# Install Shortcuts

Windows helper for creating Start Menu shortcuts for Cure Interactive utility scripts that live beside this repository in a shared tools folder.

## Requirements

- Python 3.10+
- Windows

## Run

```bash
python install_shortcuts.py
```

The script scans one directory above its own folder for Python files that define `APP_TITLE = "..."`. For each matching app, it creates a `.lnk` shortcut under the current user's Start Menu programs directory and uses an `icon.ico` file beside the app script when one exists.

This tool is most useful when multiple standalone Cure utility repositories are checked out as siblings, for example under a common `_github` or tools directory.

## Files

- `install_shortcuts.py`: main script
- `icon.ico`: shortcut icon
