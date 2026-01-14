# Project: Automated Homebrew Tap & Release Manager

## **1. Project Overview**
This repository acts as a custom Homebrew Tap. The goal is to automate the distribution of macOS applications (binaries) via Homebrew. A Python script (`scripts/release_manager.py`) manages the lifecycle of these apps: from local binary detection and "smart repacking" to GitHub Release creation and Cask updates.

## **2. Directory Structure**
```text
.
├── Casks/                  # Directory containing Homebrew Formulae/Casks (.rb files)
├── upload/                 # Staging folder: Place new .dmg/.zip/.pkg files here
├── uploaded/               # Archive folder: Processed files are moved here
├── scripts/
│   └── release_manager.py  # Main automation script
├── .env                    # Stores GITHUB_TOKEN (ignored by git)
├── state.json              # Tracks repository version history
└── apps.md                 # Auto-generated list of supported apps
```

## **3. Automation Script Workflow**

The script (`scripts/release_manager.py`) executes the following phases:

### **Phase 1: Pre-processing & Smart Repacking**
- **Scan `upload/`**: Looks for any `.zip` or `.dmg` files.
- **Recursive Search**: If a file doesn't match the standard naming convention, the script extracts it and recursively searches for nested `.app` or `.pkg` files (handling nested DMGs/ZIPs).
- **Metadata Extraction**:
  - Reads `Info.plist` (using `plistlib` or `defaults read` fallback) to determine `AppName` and `Version`.
  - For `.pkg` files, extracts version and package ID using `pkgutil`.
- **Repacking**:
  - Creates a clean, standard `.dmg` (e.g., `AppName-Version.dmg`) containing the found app.
  - Or renames the `.pkg` to `AppName-Version.pkg`.
  - Deletes the original "messy" archive.
- **Manual Inspection**: Pauses to allow the user to inspect the repacked files (skipped if `--non-interactive` is used).

### **Phase 2: Validation**
- **Strict Naming**: Ensures all files in `upload/` now match `AppName-Version.ext`.
- **Warning**: Skips invalid files with a warning instead of aborting.

### **Phase 3: Versioning Strategy**
- **Repository Version**: Determines the next semantic version for the *repository release* (not the app version).
- **Logic**:
  - `Major`: If `--major` flag is passed.
  - `Minor`: If a new app (new Cask) is detected.
  - `Patch`: If only existing apps are updated.

### **Phase 4: Cask Processing & Verification**
- **Checksum**: Calculates SHA256 for each file.
- **App Verification**:
  - Checks if the app is signed/notarized using `spctl`.
  - If **UNVERIFIED**: Automatically injects a `postflight` stanza into the Cask to remove the `com.apple.quarantine` attribute.
- **Cask Update/Creation**:
  - Updates existing `.rb` files in `Casks/` (version, sha256, url).
  - Creates new `.rb` files for new apps using a template (supports `app` and `pkg` stanzas).

### **Phase 5: Documentation & State**
- **Update State**: Updates `state.json` with the new version and history.
- **Generate Docs**: Re-generates `apps.md` with a table of all available apps and installation commands.

### **Phase 6: Release & Cleanup**
- **Git Operations**:
  - Commits changes (`Casks/`, `state.json`, `apps.md`).
  - Pushes to the current branch.
- **GitHub Release**:
  - Creates a new Release (tag `vX.Y.Z`) on GitHub.
  - Uploads all binaries from `upload/`.
- **Cleanup**: Moves processed files from `upload/` to `uploaded/`.

## **4. CLI Usage**

```bash
# Standard run (interactive repacking inspection)
python3 scripts/release_manager.py

# Run without manual inspection pause
python3 scripts/release_manager.py --non-interactive

# Force a major repository version bump
python3 scripts/release_manager.py --major
```

## **5. Technical Details**
- **Dependencies**: `PyGithub`, `semantic_version`, `python-dotenv`.
- **System Tools**: Uses `hdiutil`, `unzip`, `pkgutil`, `defaults`, `spctl`, `git`.
- **Resiliency**: Handles encoding errors, missing plist keys, and messy archive structures.
