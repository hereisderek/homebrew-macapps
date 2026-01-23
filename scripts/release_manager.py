#!/usr/bin/env python3
import os
import sys
import shutil
import hashlib
import re
import json
import yaml
import requests
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
APPS_YAML = WORKSPACE_ROOT / "apps.yaml"
ENV_FILE = WORKSPACE_ROOT / ".env"

# Regex for AppName-Version.ext (e.g. MyTool-1.0.2.dmg)
FILENAME_PATTERN = re.compile(r"^(?P<name>[a-zA-Z0-9]+)-(?P<version>\d+\.\d+\.\d+)\.(?P<ext>dmg|pkg|zip)$")

# --- Phase Pre-A: Repacking ---

def get_pkg_info(pkg_path):
    """Extract version and package ID from a .pkg file."""
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            expand_dir = Path(temp_dir) / "expanded"
            # pkgutil --expand
            subprocess.run(["pkgutil", "--expand", str(pkg_path), str(expand_dir)], 
                         check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Look for Distribution or PackageInfo
            dist = expand_dir / "Distribution"
            pkg_info = expand_dir / "PackageInfo"
            
            content = ""
            if dist.exists():
                content = dist.read_text(errors="ignore")
            elif pkg_info.exists():
                content = pkg_info.read_text(errors="ignore")
                
            # Regex for version and id
            v_match = re.search(r'version="([^"]+)"', content)
            id_match = re.search(r'(?:id|pkgid)="([^"]+)"', content)
            
            version = v_match.group(1) if v_match else None
            pkg_id = id_match.group(1) if id_match else None
            
            return version, pkg_id
    except Exception:
        return None, None

def unmount_dmg(mount_point):
    """Safely unmount a DMG, retrying if busy."""
    for i in range(5):
        res = subprocess.run(["hdiutil", "detach", str(mount_point), "-force", "-quiet"], capture_output=True)
        if res.returncode == 0:
            return
        time.sleep(1)
    print(f"Warning: Failed to detach {mount_point}")

def recursive_find_app(search_dir, depth=0):
    """Recursively search for .app bundles, unpacking ZIPs and DMGs as needed."""
    if depth > 3:  # Prevent excessive recursion
        return None
        
    print(f"    [Depth {depth}] Scanning {search_dir.name}...")

    # 1. Check for .app or .pkg directly using rglob
    # We sort by length to find the shallowest/shortest path first
    artifacts = sorted(list(search_dir.rglob("*.app")) + list(search_dir.rglob("*.pkg")), key=lambda p: len(p.parts))
    
    # Filter out pkgs inside .app (e.g. Contents/Resources/install.pkg)
    final_artifacts = []
    for art in artifacts:
        # Filter out AppleDouble files and __MACOSX garbage
        if art.name.startswith("._") or "__MACOSX" in str(art):
            continue
            
        if ".app/" in str(art) and art.suffix == ".pkg":
            continue
        # Also ignore dotfiles
        if "/." in str(art):
            continue
        final_artifacts.append(art)
        
    if final_artifacts:
        print(f"    Found artifact: {final_artifacts[0].name}")
        return final_artifacts[0]

    # 2. Look for archives to unpack (ZIP)
    # Use rglob to find nested archives
    zips = list(search_dir.rglob("*.zip"))
    for zip_file in zips:
        # Avoid processing items inside an already found app or hidden folders
        if ".app/" in str(zip_file) or "/." in str(zip_file): continue
        
        extract_dir = zip_file.parent / f"ext_zip_{zip_file.stem}"
        if extract_dir.exists(): continue 
        extract_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"    Unzipping {zip_file.name}...")
        try:
            # -n: never overwrite, -q: quiet
            subprocess.run(["unzip", "-n", "-q", str(zip_file), "-d", str(extract_dir)], check=True)
            found = recursive_find_app(extract_dir, depth + 1)
            if found: return found
        except Exception as e:
            print(f"    Warning: Failed to unzip {zip_file.name}: {e}")

    # 3. Look for archives to unpack (DMG)
    dmgs = list(search_dir.rglob("*.dmg"))
    for dmg_file in dmgs:
        if ".app/" in str(dmg_file) or "/." in str(dmg_file): continue
        
        extract_dir = dmg_file.parent / f"ext_dmg_{dmg_file.stem}"
        if extract_dir.exists(): continue
        extract_dir.mkdir(parents=True, exist_ok=True)
        
        mount_point = dmg_file.parent / f"mnt_{dmg_file.stem}"
        mount_point.mkdir(exist_ok=True)
        
        print(f"    Mounting {dmg_file.name}...")
        try:
            # Mount
            subprocess.run([
                "hdiutil", "attach", str(dmg_file), 
                "-mountpoint", str(mount_point), 
                "-nobrowse", "-quiet", "-noverify", "-noautoopen"
            ], check=True, timeout=30) # Add timeout to prevent hangs
            
            # Copy contents
            print(f"    Copying contents from {dmg_file.name}...")
            for item in mount_point.iterdir():
                if item.name.startswith("."): continue
                # Skip symlinks to /Applications to avoid copying system apps
                if item.is_symlink() and str(item.readlink()) == "/Applications": continue
                
                dest = extract_dir / item.name
                if dest.exists(): continue
                
                try:
                    if item.is_dir():
                        shutil.copytree(item, dest, symlinks=True)
                    else:
                        shutil.copy2(item, dest)
                except Exception as cp_err:
                    print(f"    Warning: Failed to copy {item.name}: {cp_err}")
            
            unmount_dmg(mount_point)
            
            # Recurse
            found = recursive_find_app(extract_dir, depth + 1)
            if found: return found
            
        except subprocess.TimeoutExpired:
             print(f"    Error: Timeout mounting {dmg_file.name}")
             unmount_dmg(mount_point)
        except Exception as e:
            print(f"    Failed to process DMG {dmg_file.name}: {e}")
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
        
        if file_path.suffix.lower() == ".zip":
            subprocess.run(["unzip", "-q", str(work_file), "-d", str(work_extract_dir)], check=True)
        elif file_path.suffix.lower() == ".dmg":
            # treat as DMG found inside
            # Just move it to the extract dir so recursive finder picks it up
            shutil.move(work_file, work_extract_dir / file_path.name)
        else:
            return None
            
        found_artifact = recursive_find_app(work_extract_dir)
        
        if not found_artifact:
            return None
            
        print(f"  Found artifact: {found_artifact.name}")
        
        # Handle .pkg
        if found_artifact.suffix == ".pkg":
            version, pkg_id = get_pkg_info(found_artifact)
            app_name = found_artifact.stem # Default to filename if we can't get name from metadata easily
            
            if not version:
                print("  Warning: Could not determine version from pkg metadata. Asking user or using filename if compliant.")
                # Fallback: try to parse version from filename?
                # or just fail.
                # Let's try to infer from original filename if possible?
                # For now return None or proceed with manual version?
                # Let's fallback to "0.0.0" and let user rename it? No that breaks logic.
                pass 
                
            if not version:
                 # Try to extract version from filename if it looks like Name-1.2.3.pkg
                 m = re.search(r'-(\d+\.\d+\.\d+)', found_artifact.name)
                 if m:
                     version = m.group(1)
                     app_name = found_artifact.name[:m.start()]
            
            if not version:
                # Try original filename
                m = re.search(r'-(\d+\.\d+\.\d+)', file_path.name)
                if m:
                     version = m.group(1)
                     # App Name is start of string
                     # But file_path is messy e.g. "DisplayBuddy_3.1.0_xclient.info.zip"
                     # We can try to guess
                     pass

            if not version:
                print("  Error: Could not determine version for .pkg.")
                return None
                
            safe_name = re.sub(r'[^a-zA-Z0-9]', '', app_name)
            new_filename = f"{safe_name}-{version}.pkg"
            new_file_path = UPLOAD_DIR / new_filename
            
            print(f"  Repacking (renaming) to {new_filename}...")
            shutil.copy2(found_artifact, new_file_path)
            return new_file_path

        # Handle .app
        # Parse Info.plist
        info_plist = found_artifact / "Contents" / "Info.plist"
        if not info_plist.exists():
            print(f"  Error: Info.plist not found at {info_plist}")
            return None
            
        app_name = None
        version = None
        
        # Method 1: plistlib
        try:
            with open(info_plist, "rb") as f:
                plist = plistlib.load(f)
                app_name = plist.get("CFBundleName") or plist.get("CFBundleDisplayName")
                version = plist.get("CFBundleShortVersionString") or plist.get("CFBundleVersion")
                
                # if not app_name or not version:
                #      print(f"  Debug: plistlib loaded. Name={app_name}, Version={version}")
        except Exception as e:
            print(f"  Warning: plistlib failed: {e}")

        # Method 2: defaults read (fallback)
        if not app_name or not version:
            print(f"  Missing metadata (Name={app_name}, Version={version}). Trying 'defaults read'...")
            try:
                # Ensure path is absolute and properly formatted
                plist_path = str(info_plist.resolve())
                
                if not app_name:
                     res_name = subprocess.run(["defaults", "read", plist_path, "CFBundleName"], capture_output=True, text=True)
                     if res_name.returncode == 0: 
                        app_name = res_name.stdout.strip()
                     else:
                        print(f"    defaults read CFBundleName failed: {res_name.stderr.strip()}")
                         
                if not version:
                     res_ver = subprocess.run(["defaults", "read", plist_path, "CFBundleShortVersionString"], capture_output=True, text=True)
                     if res_ver.returncode == 0: 
                        version = res_ver.stdout.strip()
                     else:
                        print(f"    defaults read CFBundleShortVersionString failed: {res_ver.stderr.strip()}")
            except Exception as e:
                print(f"  Warning: defaults read failed: {e}")

        # Fallback Name from filename
        if not app_name:
             print("  Warning: CFBundleName missing, using App filename.")
             app_name = found_artifact.stem

        if not app_name or not version:
            print(f"  Error: Could not determine Name ({app_name}) or Version ({version}) from Info.plist")
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
            "-srcfolder", str(found_artifact),
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
        if file_path.suffix.lower() in ['.zip', '.dmg']:
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
            print(f"Warning: Invalid filename '{file_path.name}'. Skipping.")
            print("  Must match format 'AppName-Version.ext' (e.g., MyTool-1.0.2.dmg)")
            continue
        
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

def determine_version_bump(valid_files, updated_apps_info, current_version_str, force_major=False):
    """Determine the new repository release version."""
    current_ver = Version(current_version_str)
    
    if force_major:
        return current_ver.next_major()

    is_minor_bump = False
    
    # Check local uploads
    for file_info in valid_files:
        cask_token = camel_to_kebab(file_info["name"])
        cask_path = CASKS_DIR / f"{cask_token}.rb"
        if not cask_path.exists():
            is_minor_bump = True
            break

    # Check virtual updates
    if not is_minor_bump:
        for app_info in updated_apps_info:
            if app_info.get("is_new"):
                is_minor_bump = True
                break
            
    if is_minor_bump:
        return current_ver.next_minor()
    
    # If there are any updates at all, it's a patch
    if valid_files or updated_apps_info:
        return current_ver.next_patch()
        
    return current_ver

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

def is_app_verified(artifact_path):
    """
    Check if the app is verified (signed and notarized) using spctl.
    Returns (is_verified, app_name_inside_dmg).
    """
    print(f"Verifying signature for {artifact_path.name}...")
    if artifact_path.suffix.lower() == ".pkg":
        try:
            res = subprocess.run(["spctl", "--assess", "--type", "install", "--verbose", str(artifact_path)], 
                               capture_output=True)
            return (res.returncode == 0, None)
        except Exception:
            return (False, None)
            
    if artifact_path.suffix.lower() == ".dmg":
        is_verified = False
        app_name = None
        mount_point = Path(tempfile.mkdtemp())
        try:
            subprocess.run([
                "hdiutil", "attach", str(artifact_path), 
                "-mountpoint", str(mount_point), 
                "-nobrowse", "-quiet", "-noverify", "-noautoopen"
            ], check=True, timeout=30, stdout=subprocess.DEVNULL)
            
            # Find app
            apps = list(mount_point.glob("*.app"))
            if apps:
                app_path = apps[0]
                app_name = app_path.name # "Macs Fan Control.app"
                
                # spctl check
                res = subprocess.run(["spctl", "--assess", "--type", "execute", "--verbose", str(app_path)], 
                                   capture_output=True)
                if res.returncode == 0:
                    is_verified = True
        except Exception as e:
            print(f"  Error checking signature: {e}")
        finally:
             unmount_dmg(mount_point)
             try:
                mount_point.rmdir()
             except: pass
        
        return (is_verified, app_name)
        
    return (False, None)

def get_cask_template(token, name, version, sha256, url, homepage_url, artifact_type="app", pkg_id=None, verified=False):
    stanza = ""
    postflight = ""
    
    if artifact_type == "app":
        stanza = f'app "{name}.app"'
        # Add postflight for apps to remove quarantine if NOT verified
        if not verified:
            postflight = f"""
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-d", "com.apple.quarantine", "#{{staged_path}}/{name}.app"],
                   sudo: true
  end"""
    elif artifact_type == "pkg":
        stanza = f'pkg "{name}-{version}.pkg"'
        if pkg_id:
            stanza += f'\n  uninstall pkgutil: "{pkg_id}"'
        else:
             stanza += f'\n  # uninstall pkgutil: "com.example.{token}"'
             
    return f"""cask "{token}" do
  version "{version}"
  sha256 "{sha256}"

  url "{url}"
  name "{name}"
  desc "{name} App"
  homepage "{homepage_url}"

  {stanza}{postflight}
  
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
        
        # Check verification status and get real app name
        is_verified, real_app_name = is_app_verified(file_info["path"])
        
        # Use real app name if found (remove .app extension for template as it adds it back, 
        # but wait, get_cask_template adds .app. 
        # real_app_name is "Macs Fan Control.app". 
        # So we should strip suffix.
        if real_app_name and real_app_name.endswith(".app"):
             cask_app_name = real_app_name[:-4]
        else:
             cask_app_name = file_info["name"]

        if is_verified:
            print(f"  {file_info['name']} is verified/notarized.")
        else:
            print(f"  {file_info['name']} is NOT verified. Will add quarantine removal.")
        
        # Determine Artifact Type
        artifact_type = "app"
        pkg_id = None
        if file_info["ext"] == "pkg":
            artifact_type = "pkg"
            # Try to get pkg_id again if creating new cask
            if not cask_path.exists():
                 _, pkg_id = get_pkg_info(file_info["path"])

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
            
            # Update app name if real name differs?
            # Existing cask has 'app "OldName.app"'.
            # We should probably update it if we know the real name now.
            if artifact_type == "app" and real_app_name:
                 # Replace app "..." with app "RealName.app"
                 content = re.sub(r'app\s+"[^"]+"', f'app "{real_app_name}"', content)

            # Check for postflight for apps and inject if missing AND not verified
            if artifact_type == "app":
                 if not is_verified and "postflight do" not in content:
                     print(f"Injecting postflight stanza into {cask_token}.rb")
                     # Insert before end (or Zap stanza)
                     postflight_stanza = f"""
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-d", "com.apple.quarantine", "#{{staged_path}}/{real_app_name}"],
                   sudo: true
  end
"""
                     if "# Zap stanza" in content:
                        content = content.replace("# Zap stanza", f"{postflight_stanza}\n  # Zap stanza")
                     else:
                        content = re.sub(r'(\n\s*end)', f"{postflight_stanza}\\1", content)
                 elif is_verified and "postflight do" in content:
                     # Optional: Remove postflight if now verified?
                     pass

            with open(cask_path, "w") as f:
                f.write(content)
                
            updates_log.append(f"**{file_info['name']}**: Updated to v{file_info['version']}")
            
        else:
            # Create new Cask
            print(f"Creating Cask: {cask_token}.rb")
            homepage = f"https://github.com/{repo_name}"
            content = get_cask_template(
                token=cask_token,
                name=cask_app_name,
                version=file_info["version"],
                sha256=file_sha,
                url=download_url,
                homepage_url=homepage,
                artifact_type=artifact_type,
                pkg_id=pkg_id,
                verified=is_verified
            )
            
            with open(cask_path, "w") as f:
                f.write(content)
                
            updates_log.append(f"**{file_info['name']}**: Initial Release (v{file_info['version']})")
            
    return updates_log
            
    return updates_log

def update_apps_md():
    """Regenerate apps.md with the latest list of Casks."""
    print("Updating apps.md...")
    apps_md_path = WORKSPACE_ROOT / "apps.md"
    
    header = """# Supported Applications

This page lists all the applications available in this Homebrew tap.

## Installation

To install any of the apps listed below, first tap the repository:

```bash
brew tap hereisderek/macapps
```

Then install the specific app using its command:

```bash
brew install --cask <app-command>
```

Or install directly without tapping first:

```bash
brew install --cask hereisderek/macapps/<app-command>
```

## Available Apps

| Application | Version | Install Command | Description |
|-------------|---------|-----------------|-------------|
"""
    
    rows = []
    for cask_file in sorted(CASKS_DIR.glob("*.rb")):
        if cask_file.name.startswith("."): continue
        
        with open(cask_file, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            
        # Parse minimal info using regex
        name_match = re.search(r'name "([^"]+)"', content)
        version_match = re.search(r'version "([^"]+)"', content)
        desc_match = re.search(r'desc "([^"]+)"', content)
        
        name = name_match.group(1) if name_match else cask_file.stem
        version = version_match.group(1) if version_match else "?"
        desc = desc_match.group(1) if desc_match else ""
        command = cask_file.stem
        
        row = f"| **{name}** | {version} | `brew install --cask {command}` | {desc} |"
        rows.append(row)
        
    with open(apps_md_path, "w") as f:
        f.write(header + "\n".join(rows) + "\n")

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
    if not UPLOADED_DIR.exists():
        UPLOADED_DIR.mkdir(exist_ok=True)

    for file_info in valid_files:
        src = file_info["path"]
        dst = UPLOADED_DIR / src.name
        print(f"Moving {src.name} to uploaded/")
        shutil.move(src, dst)

# --- Phase F: Virtual Casks (apps.yaml) ---

def calculate_url_sha256(url):
    """Calculate SHA256 of a remote file."""
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        sha256_hash = hashlib.sha256()
        for chunk in response.iter_content(chunk_size=8192):
            sha256_hash.update(chunk)
        return sha256_hash.hexdigest()
    except Exception as e:
        print(f"    Error downloading {url}: {e}")
        return None

def find_hash_in_release(release, binary_name):
    """Try to find SHA256 hash in release assets or body."""
    # 1. Look in assets for checksum files
    for asset in release.get_assets():
        name = asset.name.lower()
        if any(x in name for x in ["checksum", "sha256", "shasum"]):
            if name.endswith((".txt", ".sha256", ".sha256sum", ".sum")):
                print(f"    Possible hash file found: {asset.name}. Checking...")
                try:
                    resp = requests.get(asset.browser_download_url, timeout=10)
                    resp.raise_for_status()
                    content = resp.text
                    # Look for binary_name in content
                    # Format is usually: <hash>  <filename>
                    for line in content.splitlines():
                        if binary_name in line:
                            match = re.search(r'([a-fA-F0-9]{64})', line)
                            if match:
                                found_hash = match.group(1).lower()
                                print(f"    Found hash for {binary_name} in {asset.name}: {found_hash}")
                                return found_hash
                except Exception as e:
                    print(f"    Failed to read hash file {asset.name}: {e}")

    # 2. Look in release body for the hash
    if release.body:
        print(f"    Checking release body for {binary_name} hash...")
        # Look for a 64-char hex string near the binary name or just any 64-char hex string 
        # specifically associated with the binary.
        # This is a bit risky but we can try to find the binary name and then the next hash.
        body = release.body
        if binary_name in body:
            # Find the index of binary_name
            idx = body.find(binary_name)
            # Look for hex string in the next 200 characters
            snippet = body[idx:idx+300]
            match = re.search(r'([a-fA-F0-9]{64})', snippet)
            if match:
                found_hash = match.group(1).lower()
                print(f"    Found hash for {binary_name} in release body: {found_hash}")
                return found_hash
        else:
            # If binary name not directly in body, maybe just look for any hash if there's only one?
            matches = re.findall(r'([a-fA-F0-9]{64})', body)
            if len(matches) == 1:
                print(f"    Found single hash in release body: {matches[0]}")
                return matches[0].lower()

    return None

def update_virtual_casks(github_token, calculate_hash=False):
    """Generate Casks for apps defined in apps.yaml and auto-detect latest version."""
    if not APPS_YAML.exists():
        print(f"Error: {APPS_YAML} not found.")
        return []

    with open(APPS_YAML, "r") as f:
        apps_config = yaml.safe_load(f)

    if not apps_config:
        print("No apps found in apps.yaml")
        return []

    g = Github(auth=Auth.Token(github_token))
    updated_apps_info = []
    yaml_changed = False

    for app_name, config in apps_config.items():
        github_url = config.get("github")
        versions = config.get("versions", [])
        xattr_clear = config.get("xattr_clear", False)
        
        if not github_url:
            print(f"Missing github url for {app_name}")
            continue

        # Extract repo path (owner/repo) from URL
        repo_path = github_url.replace("https://github.com/", "").strip("/")
        print(f"Checking for updates: {app_name} ({repo_path})")
        
        try:
            external_repo = g.get_repo(repo_path)
            latest_release = external_repo.get_latest_release()
            all_releases = list(external_repo.get_releases())
        except Exception as e:
            print(f"Error fetching repo/releases for {repo_path}: {e}")
            continue

        # Check if the latest release is already in our list
        latest_v = latest_release.tag_name.lstrip('v')
        current_v = ""
        if versions:
            first_v = versions[0]
            current_v = first_v.get("version", "").lstrip('v') if isinstance(first_v, dict) else first_v.lstrip('v')

        if latest_v != current_v:
            print(f"  New version detected for {app_name}: {current_v} -> {latest_v}")
            # Add to the beginning of the list
            versions.insert(0, latest_v)
            apps_config[app_name]["versions"] = versions
            yaml_changed = True
        
        # Now process all versions in the YAML to ensure Casks are up to date
        for i, version_info in enumerate(versions):
            if isinstance(version_info, dict):
                version_str = version_info.get("version")
                v_xattr_clear = version_info.get("xattr_clear", xattr_clear)
            else:
                version_str = version_info
                v_xattr_clear = xattr_clear

            if not version_str: continue
            clean_v = version_str.lstrip('v')
            
            # Find matching release
            release = None
            for r in all_releases:
                if clean_v in r.tag_name or (r.name and clean_v in r.name):
                    release = r
                    break
            
            if not release: continue

            # Find best asset
            asset = None
            for a in release.get_assets():
                if a.name.lower().endswith((".dmg", ".pkg", ".zip")):
                    asset = a
                    break
            
            if not asset: continue

            # Check if Cask already exists and matches version
            base_token = camel_to_kebab(app_name)
            is_latest = (i == 0)
            token = base_token if is_latest else f"{base_token}@{clean_v}"
            cask_path = CASKS_DIR / f"{token}.rb"

            is_new_app = not cask_path.exists()
            
            # Even if it exists, we might want to update it if it's the latest and has changed? 
            # For now, if it's the latest and we just detected a bump, we definitely update.
            if is_new_app or (is_latest and latest_v == clean_v and yaml_changed):
                print(f"  Processing Cask for {app_name} v{clean_v}...")
                
                # SHA256 Strategy
                file_sha = None
                if calculate_hash:
                    file_sha = calculate_url_sha256(asset.browser_download_url)
                else:
                    file_sha = find_hash_in_release(release, asset.name)
                    if not file_sha:
                        file_sha = calculate_url_sha256(asset.browser_download_url)
                
                if not file_sha: continue

                artifact_type = "pkg" if asset.name.endswith(".pkg") else "app"
                
                content = get_cask_template(
                    token=token,
                    name=app_name,
                    version=clean_v,
                    sha256=file_sha,
                    url=asset.browser_download_url,
                    homepage_url=github_url,
                    artifact_type=artifact_type,
                    verified=not v_xattr_clear 
                )
                
                with open(cask_path, "w") as f:
                    f.write(content)
                print(f"  Created/Updated Cask: {cask_path.name}")
                
                updated_apps_info.append({
                    "name": app_name,
                    "version": clean_v,
                    "is_new": is_new_app
                })

    if yaml_changed:
        with open(APPS_YAML, "w") as f:
            yaml.dump(apps_config, f, sort_keys=False)
        print(f"Updated {APPS_YAML.name}")

    return updated_apps_info

# --- Main Execution ---

def main():
    parser = argparse.ArgumentParser(description="Automated Homebrew Tap & Release Manager")
    parser.add_argument("--major", action="store_true", help="Force a Major version bump for the repository release")
    parser.add_argument("--non-interactive", action="store_true", help="Skip manual inspection pause")
    parser.add_argument("--update", action="store_true", help="Generate virtual casks from apps.yaml for GitHub apps")
    parser.add_argument("--hash", action="store_true", help="Force local calculation of SHA256 by downloading the file")
    args = parser.parse_args()

    # Phase A
    github_token, repo_name = setup_environment()
    
    updated_apps_info = []
    if args.update:
        print("Running in --update mode. Checking apps.yaml for new releases...")
        updated_apps_info = update_virtual_casks(github_token, calculate_hash=args.hash)

    # Pre-process: Repack any non-compliant archives
    preprocess_files()
    
    # Pause for manual inspection
    valid_files = scan_upload_folder()
    
    if valid_files and not args.non_interactive:
        print("\nRepacking phase complete.")
        print(f"Please inspect the files in '{UPLOAD_DIR}' to ensure they are correct.")
        try:
            input("Press Enter to continue with the release process (or Ctrl+C to abort)...")
        except KeyboardInterrupt:
            print("\nAborted by user.")
            sys.exit(0)
    elif not valid_files and not updated_apps_info:
        print("No local uploads or virtual updates found. Nothing to do.")
        sys.exit(0)

    # Phase B
    state = load_state()
    current_repo_version = state.get("version", "0.0.0")
    new_repo_version = determine_version_bump(valid_files, updated_apps_info, current_repo_version, args.major)
    
    if str(new_repo_version) == current_repo_version and not args.major:
        print("No changes detected. Skipping release.")
        sys.exit(0)

    print(f"Current Repo Version: {current_repo_version}")
    print(f"New Repo Version:     {new_repo_version}")
    
    # Phase C
    updates_log = []
    if valid_files:
        updates_log.extend(process_casks(valid_files, new_repo_version, repo_name))
    
    for app in updated_apps_info:
        status = "Initial Release" if app["is_new"] else "Updated"
        updates_log.append(f"**{app['name']}**: {status} (v{app['version']})")
    
    # Update State
    state["version"] = str(new_repo_version)
    state["history"].append({
        "version": str(new_repo_version),
        "updates": [f["name"] + " " + f["version"] for f in valid_files] + [a["name"] + " " + a["version"] for a in updated_apps_info]
    })
    save_state(state)

    # Generate apps.md
    update_apps_md()

    # Phase D
    # Git
    files_to_commit = [str(CASKS_DIR), str(STATE_FILE), "apps.md", "apps.yaml"]
    all_names = [f['name'] for f in valid_files] + [a['name'] for a in updated_apps_info]
    commit_msg = f"Update apps: {', '.join(all_names)} (Bump to v{new_repo_version})"
    git_commit_push(files_to_commit, commit_msg)
    
    # Release
    if valid_files:
        release_tag = f"v{new_repo_version}"
        release_title = f"Release {release_tag}"
        release_notes = "## Updates\n" + "\n".join([f"* {log}" for log in updates_log])
        
        asset_paths = [f["path"] for f in valid_files]
        create_github_release_and_upload(github_token, repo_name, release_tag, release_title, release_notes, asset_paths)
    else:
        # If ONLY virtual apps updated, we don't need to upload assets to a release, 
        # but we should still create a tag/release for the repo version.
        release_tag = f"v{new_repo_version}"
        release_title = f"Release {release_tag}"
        release_notes = "## Updates (Virtual Casks)\n" + "\n".join([f"* {log}" for log in updates_log])
        create_github_release_and_upload(github_token, repo_name, release_tag, release_title, release_notes, [])

    # Phase E
    cleanup_files(valid_files)
    print("Release automation complete!")

if __name__ == "__main__":
    main()
