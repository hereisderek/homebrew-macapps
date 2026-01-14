# Automated Homebrew Tap & Release Manager

This project automates the distribution of macOS applications via Homebrew. It manages local binary detection, versioning, Cask updates, and GitHub Release creation.

## Features

- **Automated Cask Management**: Detects new binaries and updates existing Casks or creates new ones.
- **Support for Various Formats**: Handles `.dmg`, `.pkg`, and `.zip` files.
- **Smart Repacking**: Automatically finds `.app` bundles inside messy ZIPs or nested DMGs and repacks them into clean, standardized DMGs.
- **Gatekeeper Handling**: Automatically adds a `postflight` stanza to remove Apple Quarantine attributes for unverified/unsigned apps.
- **Versioning**: Auto-detects version from `Info.plist` or filenames.
- **Documentation**: Automatically updates `apps.md` with the latest supported apps.

## Installation & Usage

To install apps from this tap:

1. **Tap the repository:**
   ```bash
   brew tap hereisderek/macapps
   ```

2. **Install an app:**
   ```bash
   brew install --cask <app-name>
   ```
   Example:
   ```bash
   brew install --cask daisy-disk
   ```

Or install directly without tapping first:

```bash
brew install --cask hereisderek/macapps/<app-name>
```

## Prerequisites

- Python 3.6+
- Git
- A GitHub Personal Access Token with `repo` scope.

## Setup

It is recommended to use a Python virtual environment to manage dependencies.

### 1. Create and Activate Virtual Environment

**macOS / Linux:**
```bash
# Create venv
python3 -m venv venv

# Activate venv
source venv/bin/activate
```

### 2. Install Dependencies

With the virtual environment activated, install the required packages:

```bash
pip install -r requirements.txt
```

### 3. Configuration

Create a `.env` file in the root directory (if it doesn't exist) and add your GitHub token:

```bash
GITHUB_TOKEN=your_token_here
# Optional: explicitly set repo if git remote is not detected
# GITHUB_REPOSITORY=username/repo-name
```

## Usage

### 1. Prepare Binaries

Place your application binaries (`.dmg`, `.pkg`, or `.zip`) in the `upload/` directory.
**Naming Convention:** `AppName-Version.ext` (e.g., `MyTool-1.0.2.dmg`).

### 2. Run the Release Manager

Run the script from the root of the repository (ensure your venv is activated):

```bash
./scripts/release_manager.py
```

The script will:
1. **Pre-process**: Repack any ZIPs or nested DMGs into clean `AppName-Version.dmg` files.
2. **Validate**: Ensure files match the required naming convention.
3. **Inspect**: Pause for manual inspection of repacked files (use `--non-interactive` to skip).
4. **Version**: Calculate new repository version.
5. **Update Casks**: Update or create Casks in `Casks/`.
6. **Release**: Commit changes, create a GitHub Release, and upload binaries.
7. **Cleanup**: Move processed files to `uploaded/`.

### Options

- **Force Major Version Bump:**
  ```bash
  ./scripts/release_manager.py --major
  ```
- **Run Non-Interactively:**
  ```bash
  ./scripts/release_manager.py --non-interactive
  ```

## Directory Structure

- `Casks/`: Contains Homebrew Cask definitions (`.rb` files).
- `upload/`: Place new release binaries here.
- `uploaded/`: Processed binaries are moved here.
- `scripts/`: Contains the automation script.
