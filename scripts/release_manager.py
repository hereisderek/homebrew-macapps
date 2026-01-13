#!/usr/bin/env python3
import os
import sys
import shutil
import hashlib
import re
import json
import argparse
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from github import Github, Auth
from semantic_version import Version

# --- Configuration & Constants ---
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = WORKSPACE_ROOT / "upload"
UPLOADED_DIR = WORKSPACE_ROOT / "uploaded"
CASKS_DIR = WORKSPACE_ROOT / "Casks"
STATE_FILE = WORKSPACE_ROOT / "state.json"
ENV_FILE = WORKSPACE_ROOT / ".env"

# Regex for AppName-Version.ext (e.g. MyTool-1.0.2.dmg)
FILENAME_PATTERN = re.compile(r"^(?P<name>[a-zA-Z0-9]+)-(?P<version>\d+\.\d+\.\d+)\.(?P<ext>dmg|pkg|zip)$")

# --- Phase A: Validation & Setup ---

def setup_environment():
    """Load environment variables and validate tokens."""
    load_dotenv(ENV_FILE)
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("Error: GITHUB_TOKEN not found in .env or environment variables.")
        sys.exit(1)
    
    # Optional: Get Repository Name from .env or git config
    repo_name = os.getenv("GITHUB_REPOSITORY")
    if not repo_name:
        try:
            remote_url = subprocess.check_output(["git", "config", "--get", "remote.origin.url"]).decode().strip()
            # extract user/repo from git@github.com:user/repo.git or https://github.com/user/repo.git
            match = re.search(r"github\.com[:/](.+?)/(.+?)(\.git)?$", remote_url)
            if match:
                repo_name = f"{match.group(1)}/{match.group(2)}"
        except subprocess.CalledProcessError:
            pass
            
    if not repo_name:
        print("Error: Could not determine GITHUB_REPOSITORY from .env or git remote.")
        sys.exit(1)
        
    return token, repo_name

def scan_upload_folder():
    """Scan upload/ folder and validate filenames."""
    files = [f for f in UPLOAD_DIR.iterdir() if f.is_file() and not f.name.startswith(".")]
    valid_files = []
    
    if not files:
        print("No files found in upload/ folder.")
        sys.exit(0)

    print(f"Found {len(files)} files in upload/...")

    for file_path in files:
        match = FILENAME_PATTERN.match(file_path.name)
        if not match:
            print(f"Error: Invalid filename '{file_path.name}'. Must match format 'AppName-Version.ext' (e.g., MyTool-1.0.2.dmg)")
            sys.exit(1)
        
        valid_files.append({
            "path": file_path,
            "name": match.group("name"),
            "version": match.group("version"),
            "ext": match.group("ext"),
            "filename": file_path.name
        })
        
    return valid_files

# --- Phase B: Versioning Strategy ---

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"version": "0.0.0", "history": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

def determine_version_bump(valid_files, current_version_str, force_major=False):
    """Determine the new repository release version."""
    current_ver = Version(current_version_str)
    
    if force_major:
        return current_ver.next_major()

    is_minor_bump = False
    
    for file_info in valid_files:
        # Check if Cask exists
        cask_token = camel_to_kebab(file_info["name"])
        cask_path = CASKS_DIR / f"{cask_token}.rb"
        
        if not cask_path.exists():
            is_minor_bump = True
            break
            
    if is_minor_bump:
        return current_ver.next_minor()
    else:
        return current_ver.next_patch()

def camel_to_kebab(name):
    """Convert CamelCase to kebab-case (e.g. MyTool -> my-tool)."""
    # Simple regex to handle camel case
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1-\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1-\2', s1).lower()

# --- Phase C: Processing & SHA Calculation ---

def calculate_sha256(file_path):
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def get_cask_template(token, name, version, sha256, url, homepage_url):
    return f"""cask "{token}" do
  version "{version}"
  sha256 "{sha256}"

  url "{url}"
  name "{name}"
  desc "{name} App"
  homepage "{homepage_url}"

  app "{name}.app"
  
  # Zap stanza is optional
  # zap trash: "~/Library/Application Support/{name}"
end
"""

def process_casks(valid_files, new_repo_version, repo_name):
    """Update or Create Casks."""
    updates_log = []
    
    for file_info in valid_files:
        file_sha = calculate_sha256(file_info["path"])
        cask_token = camel_to_kebab(file_info["name"])
        cask_path = CASKS_DIR / f"{cask_token}.rb"
        
        # Construct the future URL for the file in GitHub Releases
        # https://github.com/<user>/<repo>/releases/download/v<RepoVersion>/<filename>
        download_url = f"https://github.com/{repo_name}/releases/download/v{new_repo_version}/{file_info['filename']}"
        
        if cask_path.exists():
            # Update existing Cask
            print(f"Updating Cask: {cask_token}.rb")
            with open(cask_path, "r") as f:
                content = f.read()
            
            # Simple regex replacements
            # Replace version "..." -> version "<new>"
            content = re.sub(r'version\s+"[^"]+"', f'version "{file_info["version"]}"', content)
            # Replace sha256 "..." -> sha256 "<new>"
            content = re.sub(r'sha256\s+"[^"]+"', f'sha256 "{file_sha}"', content)
            # Replace url "..." -> url "<new>"
            content = re.sub(r'url\s+"[^"]+"', f'url "{download_url}"', content)
            
            with open(cask_path, "w") as f:
                f.write(content)
                
            updates_log.append(f"**{file_info['name']}**: Updated to v{file_info['version']}")
            
        else:
            # Create new Cask
            print(f"Creating Cask: {cask_token}.rb")
            homepage = f"https://github.com/{repo_name}"
            content = get_cask_template(
                token=cask_token,
                name=file_info["name"],
                version=file_info["version"],
                sha256=file_sha,
                url=download_url,
                homepage_url=homepage
            )
            
            with open(cask_path, "w") as f:
                f.write(content)
                
            updates_log.append(f"**{file_info['name']}**: Initial Release (v{file_info['version']})")
            
    return updates_log

# --- Phase D: Git & GitHub Release ---

def git_commit_push(files_to_commit, message):
    try:
        # git add
        subprocess.run(["git", "add"] + files_to_commit, check=True)
        
        # Check if there are changes to commit
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip():
            print("No changes to commit. Skipping git commit.")
        else:
            # git commit
            subprocess.run(["git", "commit", "-m", message], check=True)
            
        # git push
        print("Pushing to main...")
        subprocess.run(["git", "push", "origin", "main"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Git operation failed: {e}")
        sys.exit(1)

def create_github_release_and_upload(token, repo_name, tag, title, body, assets):
    g = Github(auth=Auth.Token(token))
    repo = g.get_repo(repo_name)
    
    print(f"Creating GitHub Release {tag}...")
    release = repo.create_git_release(tag=tag, name=title, message=body, draft=False, prerelease=False)
    
    for asset_path in assets:
        print(f"Uploading {asset_path.name}...")
        release.upload_asset(str(asset_path))

# --- Phase E: Cleanup ---

def cleanup_files(valid_files):
    for file_info in valid_files:
        src = file_info["path"]
        dst = UPLOADED_DIR / src.name
        print(f"Moving {src.name} to uploaded/")
        shutil.move(src, dst)

# --- Main Execution ---

def main():
    parser = argparse.ArgumentParser(description="Automated Homebrew Tap & Release Manager")
    parser.add_argument("--major", action="store_true", help="Force a Major version bump for the repository release")
    args = parser.parse_args()

    # Phase A
    github_token, repo_name = setup_environment()
    valid_files = scan_upload_folder()

    # Phase B
    state = load_state()
    current_repo_version = state.get("version", "0.0.0")
    new_repo_version = determine_version_bump(valid_files, current_repo_version, args.major)
    
    print(f"Current Repo Version: {current_repo_version}")
    print(f"New Repo Version:     {new_repo_version}")
    
    # Phase C
    updates_log = process_casks(valid_files, new_repo_version, repo_name)
    
    # Update State
    state["version"] = str(new_repo_version)
    state["history"].append({
        "version": str(new_repo_version),
        "updates": [f["name"] + " " + f["version"] for f in valid_files]
    })
    save_state(state)

    # Phase D
    # Git
    files_to_commit = [str(CASKS_DIR), str(STATE_FILE)]
    commit_msg = f"Update apps: {', '.join([f['name'] for f in valid_files])} (Bump to v{new_repo_version})"
    git_commit_push(files_to_commit, commit_msg)
    
    # Release
    release_tag = f"v{new_repo_version}"
    release_title = f"Release {release_tag}"
    release_notes = "## Updates\n" + "\n".join([f"* {log}" for log in updates_log])
    
    asset_paths = [f["path"] for f in valid_files]
    create_github_release_and_upload(github_token, repo_name, release_tag, release_title, release_notes, asset_paths)

    # Phase E
    cleanup_files(valid_files)
    print("Release automation complete!")

if __name__ == "__main__":
    main()
