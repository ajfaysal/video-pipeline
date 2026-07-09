"""
lofiloop.uploader
------------------
API-key-less delivery of the (potentially huge) rendered file.

Tries several free, no-signup hosts in order and returns the first working
direct download link:
    1. GoFile.io    - dynamic best-server, no size cap in practice, no key.
    2. transfer.sh  - simple PUT, large files OK.
    3. 0x0.st       - tiny fallback for smaller files.

All backends are best-effort; a clear UploadError is raised only if *every*
backend fails.
"""

from __future__ import annotations

import os

import requests

from lofiloop import UploadError


def _upload_gofile(path: str, timeout: int = 3600) -> str | None:
    try:
        # Ask GoFile for the best upload server.
        server = "store1"
        try:
            r = requests.get("https://api.gofile.io/servers", timeout=30)
            if r.ok:
                data = r.json()
                servers = (data.get("data") or {}).get("servers") or []
                if servers:
                    server = servers[0].get("name", server)
        except Exception:
            # Legacy endpoint fallback.
            try:
                r = requests.get("https://api.gofile.io/getServer", timeout=30)
                if r.ok:
                    server = (r.json().get("data") or {}).get("server", server)
            except Exception:
                pass

        url = f"https://{server}.gofile.io/uploadFile"
        print(f"[lofiloop] Uploading to GoFile via {url} ...")
        with open(path, "rb") as f:
            resp = requests.post(url, files={"file": (os.path.basename(path), f)}, timeout=timeout)
        if resp.ok:
            j = resp.json()
            if j.get("status") == "ok":
                dl = (j.get("data") or {}).get("downloadPage")
                if dl:
                    return dl
        print(f"[lofiloop] GoFile responded: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[lofiloop] GoFile upload failed: {e}")
    return None


def _upload_transfersh(path: str, timeout: int = 3600) -> str | None:
    for base in ("https://transfer.sh", "https://temp.sh"):
        try:
            name = os.path.basename(path)
            url = f"{base}/{name}"
            print(f"[lofiloop] Uploading to {base} ...")
            with open(path, "rb") as f:
                resp = requests.put(url, data=f, timeout=timeout,
                                    headers={"Max-Downloads": "9999", "Max-Days": "14"})
            if resp.ok and resp.text.strip().startswith("http"):
                return resp.text.strip()
            print(f"[lofiloop] {base} responded: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            print(f"[lofiloop] {base} upload failed: {e}")
    return None


def _upload_0x0(path: str, timeout: int = 1800) -> str | None:
    try:
        print("[lofiloop] Uploading to 0x0.st ...")
        with open(path, "rb") as f:
            resp = requests.post(
                "https://0x0.st",
                files={"file": (os.path.basename(path), f)},
                headers={"User-Agent": "LofiLoop/1.0"},
                timeout=timeout,
            )
        if resp.ok and resp.text.strip().startswith("http"):
            return resp.text.strip()
        print(f"[lofiloop] 0x0.st responded: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[lofiloop] 0x0.st upload failed: {e}")
    return None


def upload_file(path: str) -> dict:
    """
    Upload `path` to the first working free host.

    Returns {"url": <link>, "host": <backend>}. Raises UploadError if all fail.
    """
    if not os.path.isfile(path):
        raise UploadError(f"File to upload not found: {path}")

    size_mb = os.path.getsize(path) / 1024 / 1024
    print(f"[lofiloop] Uploading {os.path.basename(path)} ({size_mb:.1f} MB)...")

    backends = [("gofile", _upload_gofile), ("transfer.sh", _upload_transfersh)]
    if size_mb <= 512:  # 0x0.st has a ~512MB cap
        backends.append(("0x0.st", _upload_0x0))

    for name, fn in backends:
        link = fn(path)
        if link:
            print(f"[lofiloop] Uploaded via {name}: {link}")
            return {"url": link, "host": name}

    raise UploadError("All upload backends failed (GoFile, transfer.sh, 0x0.st).")


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2:
        print(upload_file(sys.argv[1]))
    else:
        print("usage: python -m lofiloop.uploader <file>")
