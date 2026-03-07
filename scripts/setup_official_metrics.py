import os
import requests
import tarfile
import zipfile
import subprocess
import sys
import hashlib
import stat
from pathlib import Path

# Official Metric Tool URLs
METEOR_JAR_URL = "https://www.cs.cmu.edu/~alavie/METEOR/download/meteor-1.5.tar.gz"
# Pinned source archive for pyrouge (contains ROUGE-1.5.5 scripts under tools/)
ROUGE_ZIP_URL = "https://codeload.github.com/andersjo/pyrouge/zip/3b6c415204dbc2c8360a01d92533441f4aae95eb"
ROUGE_ZIP_SHA256 = "be8639eba36d171e5d2b1eb865fc01b406022c98fbe32c4304ddd869e6018463"
# CodeBLEU from Microsoft CodeXGLUE
CODEBLEU_REPO = "https://github.com/microsoft/CodeXGLUE.git"

TOOLS_DIR = os.path.abspath("tools")

def download_file(url, dest, expected_sha256=None):
    print(f"Downloading {url}...")
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    digest = hashlib.sha256()
    with open(dest, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            digest.update(chunk)
    if expected_sha256:
        actual = digest.hexdigest()
        if actual.lower() != expected_sha256.lower():
            raise ValueError(f"Checksum mismatch for {dest}. Expected {expected_sha256}, got {actual}")

def _is_within_directory(base_dir, target_path):
    base = Path(base_dir).resolve()
    target = Path(target_path).resolve()
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False

def safe_extract_tar(tar, path):
    for member in tar.getmembers():
        member_path = Path(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError(f"Blocked unsafe tar path: {member.name}")

        target_path = os.path.join(path, member.name)
        if not _is_within_directory(path, target_path):
            raise ValueError(f"Blocked unsafe tar path: {member.name}")

        if member.issym() or member.islnk():
            raise ValueError(f"Blocked tar link entry: {member.name}")
        if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
            raise ValueError(f"Blocked special tar entry: {member.name}")

    for member in tar.getmembers():
        tar.extract(member, path=path, filter="data")

def safe_extract_zip(zip_ref, path):
    for member in zip_ref.infolist():
        member_path = Path(member.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError(f"Blocked unsafe zip path: {member.filename}")

        target_path = os.path.join(path, member.filename)
        if not _is_within_directory(path, target_path):
            raise ValueError(f"Blocked unsafe zip path: {member.filename}")

        member_mode = (member.external_attr >> 16) & 0o170000
        if member_mode == stat.S_IFLNK:
            raise ValueError(f"Blocked zip link entry: {member.filename}")

    for member in zip_ref.infolist():
        zip_ref.extract(member, path)

def setup_meteor():
    print("\n--- Setting up METEOR ---")
    dest_tar = os.path.join(TOOLS_DIR, "meteor-1.5.tar.gz")
    download_file(METEOR_JAR_URL, dest_tar)
    
    if os.path.exists(dest_tar):
        print("Extracting METEOR...")
        with tarfile.open(dest_tar, "r:gz") as tar:
            safe_extract_tar(tar, TOOLS_DIR)
        print("METEOR setup complete.")

def setup_rouge():
    print("\n--- Setting up ROUGE ---")
    dest_zip = os.path.join(TOOLS_DIR, "pyrouge-ROUGE-1.5.5.zip")
    download_file(ROUGE_ZIP_URL, dest_zip, expected_sha256=ROUGE_ZIP_SHA256)
    
    if os.path.exists(dest_zip):
        print("Extracting ROUGE...")
        with zipfile.ZipFile(dest_zip, 'r') as zip_ref:
            safe_extract_zip(zip_ref, TOOLS_DIR)
        print("ROUGE setup complete.")

def setup_codebleu():
    print("\n--- Setting up CodeBLEU ---")
    codebleu_dir = os.path.join(TOOLS_DIR, "CodeXGLUE")
    if not os.path.exists(codebleu_dir):
        print("Cloning CodeXGLUE for CodeBLEU...")
        subprocess.run(["git", "clone", CODEBLEU_REPO, codebleu_dir, "--depth", "1"], check=True)
    print("CodeBLEU scripts localized.")

def main():
    if not os.path.exists(TOOLS_DIR):
        os.makedirs(TOOLS_DIR)
        
    try:
        setup_meteor()
    except Exception as e:
        print(f"METEOR setup failed: {e}")
        
    try:
        setup_rouge()
    except Exception as e:
        print(f"ROUGE setup failed: {e}")
        
    try:
        setup_codebleu()
    except Exception as e:
        print(f"CodeBLEU setup failed: {e}")

    print("\n✅ Official metrics setup attempt complete.")

if __name__ == "__main__":
    main()
