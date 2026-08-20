"""Robust downloader for the official Antigravity CLI binary with resume & retry support."""

import hashlib
import os
import sys
import time
import urllib.request

URL = "https://storage.googleapis.com/antigravity-public/antigravity-cli/1.1.16-6607970839166976/windows-x64/cli_windows_x64.exe"
EXPECTED_SHA512 = "eeada12986101fdd06130393e82be6ce9bb835493de934d12835db8a1bb20ccb9cdcfc43553ec67e71f43484da9821d4bc710ce117a43fac0439ccf5714de701"

TARGET_DIR = os.path.expandvars(r"%LOCALAPPDATA%\agy\bin")
TARGET_EXE = os.path.join(TARGET_DIR, "agy.exe")
TEMP_DOWNLOAD = TARGET_EXE + ".download"


def download():
    os.makedirs(TARGET_DIR, exist_ok=True)
    print(f"Downloading Antigravity CLI to {TARGET_EXE}...")

    max_retries = 10
    retry_count = 0

    while retry_count < max_retries:
        downloaded = 0
        if os.path.exists(TEMP_DOWNLOAD):
            downloaded = os.path.getsize(TEMP_DOWNLOAD)

        req = urllib.request.Request(URL)
        if downloaded > 0:
            req.add_header("Range", f"bytes={downloaded}-")
            print(f"Resuming download from byte {downloaded} ({downloaded / (1024*1024):.1f} MB)...")

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                total_length = response.headers.get("content-length")
                total_size = int(total_length) + downloaded if total_length else None

                mode = "ab" if downloaded > 0 else "wb"
                with open(TEMP_DOWNLOAD, mode) as out_file:
                    chunk_size = 1024 * 1024  # 1 MB chunks
                    last_print = time.time()
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        out_file.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_print > 3.0:
                            last_print = now
                            pct = f"({(downloaded / total_size) * 100:.1f}%)" if total_size else ""
                            print(f"Progress: {downloaded / (1024*1024):.1f} MB {pct}")

            print(f"Download complete: {downloaded / (1024*1024):.1f} MB downloaded.")
            break
        except Exception as exc:
            retry_count += 1
            print(f"Download interrupted ({exc}). Retrying in 3s ({retry_count}/{max_retries})...")
            time.sleep(3)

    if not os.path.exists(TEMP_DOWNLOAD):
        print("Error: Download failed.")
        sys.exit(1)

    print("Verifying SHA512 checksum...")
    sha512 = hashlib.sha512()
    with open(TEMP_DOWNLOAD, "rb") as f:
        while chunk := f.read(1024 * 1024):
            sha512.update(chunk)

    digest = sha512.hexdigest().lower()
    if digest != EXPECTED_SHA512.lower():
        print(f"Checksum mismatch!\nExpected: {EXPECTED_SHA512}\nActual:   {digest}")
        sys.exit(1)

    print("Checksum verified successfully!")
    if os.path.exists(TARGET_EXE):
        try:
            os.remove(TARGET_EXE)
        except Exception:
            pass
    os.replace(TEMP_DOWNLOAD, TARGET_EXE)
    print(f"Antigravity CLI installed successfully at: {TARGET_EXE}")


if __name__ == "__main__":
    download()
