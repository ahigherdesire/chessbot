"""Download what "play as human" needs: the lc0 engine (Windows CPU build) and the Maia
networks, which predict the moves human players of 1100-1900 make.

  python get_maia.py

Files go to engines/lc0 and engines/maia (about 35 MB). Run it again to repair a
missing file; files already there are kept.
"""
import io
import os
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
LC0_URL = "https://github.com/LeelaChessZero/lc0/releases/download/v0.32.1/lc0-v0.32.1-windows-cpu-dnnl.zip"
MAIA_URL = "https://raw.githubusercontent.com/CSSLab/maia-chess/master/maia_weights/maia-{}.pb.gz"
MAIA_RATINGS = range(1100, 2000, 100)


def fetch(url):
    print(f"  {url.rsplit('/', 1)[-1]} ...", end=" ", flush=True)
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    print(f"{len(data) / 1e6:.1f} MB")
    return data


def main():
    lc0_dir = os.path.join(HERE, "engines", "lc0")
    maia_dir = os.path.join(HERE, "engines", "maia")
    os.makedirs(maia_dir, exist_ok=True)
    if not os.path.exists(os.path.join(lc0_dir, "lc0.exe")):
        if sys.platform != "win32":
            print("lc0: this downloads the Windows build. Elsewhere, install lc0 yourself "
                  "(https://lczero.org) and put it on PATH.")
        else:
            print("lc0:")
            zipfile.ZipFile(io.BytesIO(fetch(LC0_URL))).extractall(lc0_dir)
    print("Maia networks:")
    for rating in MAIA_RATINGS:
        path = os.path.join(maia_dir, f"maia-{rating}.pb.gz")
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(fetch(MAIA_URL.format(rating)))
    print("Done.")


if __name__ == "__main__":
    main()
