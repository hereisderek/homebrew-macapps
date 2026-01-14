#!/usr/bin/env python3
import os
import sys
import shutil
import hashlib
import re
import json
import argparse
import subprocess
import plistlib
import tempfile
import time
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

# --- Phase Pre-A: Repacking ---

def unmount_dmg(mount_point):
    """Safely unmount a DMG, retrying if busy."""
    for i in range(5):
        res = subprocess.run(["hdiutil", "detach", str(mount_point), "-force", "-quiet"], capture_output=True)
        if res.returncode == 0:
            return
        time.sleep(1)
    print(f"Warning: Failed to detach {mount_point}")

def recursive_find_app(search_dir):
    """Recursively search for .app bundles in a directory, unpacking ZIPs and DMGs as needed."""
    # 1. Check for .app directly
    apps = list(search_dir.glob("*.app"))
    if apps:
        return apps[0]

    # 2. Handle ZIPs
    for zip_file in search_dir.glob("*.zip"):
        extract_dir = search_dir / f"ext_zip_{zip_file.stem}"
        if extract_dir.exists(): continue 
        extract_dir.mkdir()
        
        try:
            subprocess.run(["unzip", "-q", str(zip_file), "-d", str(extract_dir)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            found = recursive_find_app(extract_dir)
            if found: return found
        except Exception:
            pass

    # 3. Handle DMGs
    for dmg_file in search_dir.glob("*.dmg"):
        extract_dir = search_dir / f"ext_dmg_{dmg_file.stem}"
        if extract_dir.exists(): continue
        extract_dir.mkdir()
        
        mount_point = search_dir / f"mnt_{dmg_file.stem}"
        mount_point.mkdir(exist_ok=True)
        
        try:
            # Mount
            subprocess.run(["hdiutil", "attach", str(dmg_file), "-mountpoint", str(mount_point), "-nobrowse", "-quiet", "-noverify"], check=True)
            
            # Copy visible contents to extract_dir to allow recursion (since DMG is read-only)
            for item in mount_point.iterdir():
                if item.name.startswith("."): continue
                dest = extract_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, symlinks=True)
                else:
                    shutil.copy2(item, dest)
            
            unmount_dmg(mount_point)
            
            # Recurse
            found = recursive_find_app(extract_dir)
            if found: return found
            
        except Exception as e:
            print(f"Failed to process DMG {dmg_file}: {e}")
            if mount_point.exists():
                unmount_dmg(mount_point)

    return None

def try_repack(file_path):
    """Attempt to unpack a file, find an app, and repack it into a standard DMG."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        work_file = temp_path / file_path.name
        shutil.copy2(file_path, work_file)
        
        print(f"  Searching for .app in {file_path.name}...")
        # Since work_file is in temp_path, we can just search temp_path after basic extraction if it's an archive
        # Or pass the file to recursive finder if we treat the file itself as the root to explore?
        # recursive_find_app expects a directory.
        
        # Initial extraction of the main file
        work_extract_dir = temp_path / "root_extract"
        work_extract_dir.mkdir()
        
        if file_path.suffix == ".zip":
            subprocess.run(["unzip", "-q", str(work_file), "-d", str(work_extract_dir)], check=True)
        elif file_path.suffix == ".dmg":
            # treat as DMG found inside
            # Just move it to the extract dir so recursive finder picks it up
            shutil.move(work_file, work_extract_dir / file_path.name)
        else:
            return None
            
        found_app = recursive_find_app(work_extract_dir)
        
        if not found_app:
            return None
            
        print(f"  Found app: {found_app.name}")
        
        # Parse Info.plist
        info_plist = found_app / "Contents" / "Info.plist"
        if not info_plist.exists():
            print("  Error: Info.plist not found.")
            return None
            
        with open(info_plist, "rb") as f:
            plist = plistlib.load(f)
            
        app_name = plist.get("CFBundleName")
        version = plist.get("CFBundleShortVersionString")
        
        if not app_name or not version:
            print("  Error: Could not determine Name or Version from Info.plist")
            return None
            
        # Sanitize name
        safe_name = re.sub(r'[^a-zA-Z0-9]', '', app_name)
        new_filename = f"{safe_name}-{version}.dmg"
        new_file_path = UPLOAD_DIR / new_filename
        
        print(f"  Repacking to {new_filename}...")
        
        # Create DMG
        # hdiutil create -volname "AppName" -srcfolder "path/to/App.app" -ov -format UDZO "path/to/output.dmg"
        cmd = [
            "hdiutil", "create",
            "-volname", app_name,
            "-srcfolder", str(found_app),
            "-ov",
            "-format", "UDZO",
            str(new_file_path)
        ]
        
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        return new_file_path

def preprocess_files():
    """Scan upload folder for non-compliant files and try to repack them."""
    print("Preprocessing files in upload/...")
    # List files to avoid modification during iteration issues
    files = list(UPLOAD_DIR.iterdir())
    
    for file_path in files:
        if file_path.name.startswith("."): continue
        if not file_path.is_file(): continue
        
        # If matches correct pattern, skip
        if FILENAME_PATTERN.match(file_path.name):
            continue
            
        # If it's a candidate for repacking (Zip or DMG)
        if file_path.suffix in ['.zip', '.dmg']:
            print(f"Attempting to repack: {file_path.name}")
            try:
                new_dmg = try_repack(file_path)
                if new_dmg:
                    print(f"Successfully repacked to: {new_dmg.name}")
                    # Remove original
                    file_path.unlink()
                else:
                    print(f"Skipping {file_path.name}: Could not extract valid .app")
            except Exception as e:
                print(f"Error repacking {file_path.name}: {e}")

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
            
        # Get current branch name
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"]).decode().strip()
        
        # git push
        print(f"Pushing to {branch}...")
        subprocess.run(["git", "push", "origin", branch], check=True)
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
    
    # Pre-process: Repack any non-compliant archives
    preprocess_files()
    
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
