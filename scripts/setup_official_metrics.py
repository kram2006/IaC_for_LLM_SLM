import os
import requests
import tarfile
import zipfile
import subprocess
import sys

# Official Metric Tool URLs
METEOR_JAR_URL = "http://www.cs.cmu.edu/~alavie/METEOR/download/meteor-1.5.tar.gz"
# ROUGE 1.5.5 is often difficult to find officially, using a community-maintained stable version
ROUGE_ZIP_URL = "https://github.com/summanlp/evaluation/raw/master/rouge/ROUGE-1.5.5.zip"
# CodeBLEU from Microsoft CodeXGLUE
CODEBLEU_REPO = "https://github.com/microsoft/CodeXGLUE.git"

TOOLS_DIR = os.path.abspath("tools")

def download_file(url, dest):
    print(f"Downloading {url}...")
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(dest, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
    else:
        print(f"Failed to download {url}")

def setup_meteor():
    print("\n--- Setting up METEOR ---")
    dest_tar = os.path.join(TOOLS_DIR, "meteor-1.5.tar.gz")
    download_file(METEOR_JAR_URL, dest_tar)
    
    if os.path.exists(dest_tar):
        print("Extracting METEOR...")
        with tarfile.open(dest_tar, "r:gz") as tar:
            tar.extractall(path=TOOLS_DIR)
        print("METEOR setup complete.")

def setup_rouge():
    print("\n--- Setting up ROUGE ---")
    dest_zip = os.path.join(TOOLS_DIR, "ROUGE-1.5.5.zip")
    download_file(ROUGE_ZIP_URL, dest_zip)
    
    if os.path.exists(dest_zip):
        print("Extracting ROUGE...")
        with zipfile.ZipFile(dest_zip, 'r') as zip_ref:
            zip_ref.extractall(TOOLS_DIR)
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
