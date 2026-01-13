This file provides the necessary context for GitHub Copilot (or any other AI coding assistant) to build your automation suite exactly how you described.

# Project: Automated Homebrew Tap & Release Manager

## **1. Project Overview**
This repository acts as a custom Homebrew Tap (`homebrew-local`). The goal is to automate the distribution of macOS applications (binaries) via Homebrew. We need a Python script (`scripts/release_manager.py`) that manages the lifecycle of these apps: from local binary detection to GitHub Release creation and Cask (.rb) updates.

## **2. Directory Structure**
Ensure the automation respects this structure:
```text
.
├── Casks/                  # Directory containing Homebrew Formulae/Casks (.rb files)
├── upload/                 # Staging folder: User puts new .dmg/.zip files here
├── uploaded/               # Archive folder: Script moves processed files here
├── scripts/
│   └── release_manager.py  # The main automation script to be generated
├── .env                    # Stores GITHUB_TOKEN (ignored by git)
└── state.json              # Tracks app version history (optional, or derive from Casks)


3. Automation Script Logic (scripts/release_manager.py)
The script must implement the following workflow sequentially:

Phase A: Validation & Setup
Environment: Load GITHUB_TOKEN from .env.

Scan upload/: Detect all binary assets (.dmg, .pkg, .zip).

Naming Validation: - Files must follow the format: AppName-Version.ext (e.g., MyTool-1.0.2.dmg).

If a file does not match this regex pattern, the script must abort with a descriptive error.

Phase B: Versioning Strategy
Determine the new global Release Version (SemVer) based on the files found:

Input: Allow a CLI flag --major to force a Major bump.

Logic:

Major Bump: If --major is present → Bump Major (X.0.0).

Minor Bump: If any file in upload/ corresponds to a new app (no existing .rb file in Casks/) → Bump Minor (x.Y.0).

Patch Bump: If all files are updates to existing apps → Bump Patch (x.x.Z).

Phase C: Processing & SHA Calculation
For each valid file in upload/:

Calculate SHA256: Compute the file's SHA256 checksum locally.

Determine Cask File: Map the filename (e.g., MyTool) to a Cask token (kebab-case: my-tool).

Update or Create Cask:

Existing App: Find Casks/my-tool.rb. Use Regex to replace:

version "..." → version "<file_version>"

sha256 "..." → sha256 "<calculated_hash>"

url "..." → Ensure it points to the new GitHub Release tag.

New App: Generate a new .rb file in Casks/ using a standard template.

Template Requirement: It must include the calculated SHA256, the correct url pointing to the repo's release assets, and standard stanzas (name, desc, homepage).

Phase D: Git & GitHub Release
Git Commit: - Stage modified/created Casks and state.json.

Commit message: Update apps: [App List] (Bump to v{ReleaseTag}).

Push to main.

Create GitHub Release:

Tag: v{ReleaseTag}

Title: Release v{ReleaseTag}

Assets: Upload all binaries from upload/.

Release Notes: Must be auto-generated with the following format:

Markdown

## Updates
* **AppName**: v1.0.0 -> v1.0.1
* **NewApp**: Initial Release (v2.0)
Phase E: Cleanup
Move processed files from upload/ to uploaded/.

4. Technical Constraints
Use PyGithub for GitHub API interactions.

Use semantic_version for version parsing.

Use Python's hashlib for SHA256 calculation.

The script must be idempotent (safe to re-run if it fails halfway).