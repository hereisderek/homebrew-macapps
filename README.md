# Automated Homebrew Tap & Release Manager

This project automates the distribution of macOS applications via Homebrew. It manages local binary detection, versioning, Cask updates, and GitHub Release creation.

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
1. Validate files in `upload/`.
2. Calculate new repository version.
3. Update or create Casks in `Casks/`.
4. Commit and push changes to GitHub.
5. Create a GitHub Release and upload binaries.
6. Move processed files to `uploaded/`.

### Options

- **Force Major Version Bump:**
  ```bash
  ./scripts/release_manager.py --major
  ```

## Directory Structure

- `Casks/`: Contains Homebrew Cask definitions (`.rb` files).
- `upload/`: Place new release binaries here.
- `uploaded/`: Processed binaries are moved here.
- `scripts/`: Contains the automation script.
