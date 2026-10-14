"""Build the Windows app: dist/ChessBot/ with ChessBot.exe (the control panel),
chessbot-cli.exe (the command-line tools), the engines and the piece templates.
Then zip it to dist/ChessBot.zip.

  pip install pyinstaller
  python get_maia.py          # lc0 + Maia (optional: without them human mode uses the fallback model)
  python build_exe.py

Stockfish is copied from wherever engine.find_stockfish() finds it (winget, PATH,
STOCKFISH_PATH or engines/). Python is not needed on the computer that runs the app.
"""
import glob
import os
import shutil
import stat
import subprocess
import sys

import engine

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
APP = os.path.join(DIST, "ChessBot")
BUILD = os.path.join(HERE, "build")

SPEC = r'''
a = Analysis([r"{here}\bot_ui.py"], pathex=[r"{here}"])
b = Analysis([r"{here}\cli.py"], pathex=[r"{here}"])
gui = EXE(PYZ(a.pure), a.scripts, [], exclude_binaries=True, name="ChessBot", console=False)
cli = EXE(PYZ(b.pure), b.scripts, [], exclude_binaries=True, name="chessbot-cli", console=True)
COLLECT(gui, a.binaries, a.datas, cli, b.binaries, b.datas, name="ChessBot")
'''


NOTICE = """Third-party programs in engines/ (each is a separate program, unmodified, under
the GNU General Public License v3; their licence texts are in their folders):

  Stockfish   engines/stockfish   https://github.com/official-stockfish/Stockfish
  lc0         engines/lc0         https://github.com/LeelaChessZero/lc0  (v0.32.1)
  Maia        engines/maia        https://github.com/CSSLab/maia-chess

Their source code is at those addresses.
"""


def copy_engines():
    eng = os.path.join(APP, "engines")
    sf = engine.find_stockfish()
    if sf:
        os.makedirs(os.path.join(eng, "stockfish"), exist_ok=True)
        shutil.copy2(sf, os.path.join(eng, "stockfish", "stockfish.exe"))
        for doc in ("Copying.txt", "AUTHORS"):   # the licence travels with the binary
            if os.path.isfile(os.path.join(os.path.dirname(sf), doc)):
                shutil.copy2(os.path.join(os.path.dirname(sf), doc), os.path.join(eng, "stockfish"))
        print("stockfish:", sf)
    else:
        print("Stockfish not found: the app will use its built-in engine (much weaker)")
    lc0 = os.path.join(HERE, "engines", "lc0")
    if os.path.isfile(os.path.join(lc0, "lc0.exe")):
        # everything but lc0's own default network (Maia brings its own)
        shutil.copytree(lc0, os.path.join(eng, "lc0"), ignore=shutil.ignore_patterns("*.pb.gz"))
        shutil.copytree(os.path.join(HERE, "engines", "maia"), os.path.join(eng, "maia"))
        shutil.copy2(os.path.join(lc0, "COPYING"), os.path.join(eng, "maia", "COPYING"))   # Maia is GPL-3.0 too
        print("lc0 + Maia:", len(glob.glob(os.path.join(eng, "maia", "*.pb.gz"))), "networks")
    else:
        print("lc0 / Maia not found (python get_maia.py): human mode will use the fallback model")


def _writable(func, path, exc):
    os.chmod(path, stat.S_IWRITE)   # Windows will not delete read-only folders
    func(path)


def main():
    if os.path.exists(APP):
        shutil.rmtree(APP, onexc=_writable)
    spec = os.path.join(BUILD, "ChessBot.spec")
    os.makedirs(BUILD, exist_ok=True)
    with open(spec, "w", encoding="utf-8") as f:
        f.write(SPEC.format(here=HERE))
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", DIST,
                    "--workpath", BUILD, spec], check=True)
    # next to the .exe, not inside it: calibrate writes new templates here
    shutil.copytree(os.path.join(HERE, "templates"), os.path.join(APP, "templates"))
    copy_engines()
    shutil.copy2(os.path.join(HERE, "README.md"), APP)
    with open(os.path.join(APP, "THIRD-PARTY.txt"), "w", encoding="utf-8") as f:
        f.write(NOTICE)
    print("zipping ...")
    zipped = shutil.make_archive(APP, "zip", DIST, "ChessBot")
    print(f"done: {APP}\\ChessBot.exe  ({os.path.getsize(zipped) / 2 ** 20:.0f} MB zipped: {zipped})")


if __name__ == "__main__":
    main()
