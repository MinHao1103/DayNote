"""產生 DayNote 可攜版壓縮包：內附精簡 Python，新電腦解壓縮即可執行，不需安裝 Python。

用法（在已安裝 Python 3.10 以上、含 tcl/tk 的電腦，於 DayNote 資料夾執行）：
    python tools/build_portable.py
產出：dist/DayNote-portable.zip

壓縮包內容：
    DayNote/
      StartDayNote.bat, SetupAutostart.bat, README.md
      app/                 程式
      data/                只有 config.example.json（個人資料一律不放）
      docs/                README 截圖
      runtime/             精簡過的 Python（取自執行本腳本的 Python）

作者：DayNote
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # DayNote 資料夾
DIST = os.path.join(ROOT, "dist")
APP_FILES = ("StartDayNote.bat", "SetupAutostart.bat", "README.md", os.path.join("data", "config.example.json"))
APP_DIRS = ("app", "docs")
PERSONAL_FILES = ("config.json", "token.bin", "state.json", "daynote.pid")  # data 資料夾內，一律不打包

# Python 安裝目錄中 DayNote 用不到的部分（測試、IDLE、pip、第三方套件、文件、開發用檔案）
SKIP = {os.path.normcase(os.path.normpath(p)) for p in (
    "Lib/test", "Lib/idlelib", "Lib/site-packages", "Lib/ensurepip", "Lib/lib2to3", "Lib/turtledemo",
    "Lib/tkinter/test", "Doc", "include", "libs", "Scripts", "Tools", "tcl/tix8.4.3")}


def check_python():
    """確認打包用的 Python 符合需求：3.10 以上、含 tkinter。"""
    if sys.version_info < (3, 10):
        sys.exit(f"需要 Python 3.10 以上，目前是 {sys.version.split()[0]}")
    try:
        import tkinter  # noqa: F401  僅檢查是否可用
    except ImportError:
        sys.exit("這個 Python 沒有 tkinter（安裝時請勾選 tcl/tk）")
    if not os.path.exists(os.path.join(sys.base_prefix, "pythonw.exe")):
        sys.exit(f"找不到 pythonw.exe：{sys.base_prefix}（請用 python.org 的 Windows 安裝版 Python 執行）")


def copy_runtime(target):
    base = sys.base_prefix

    def ignore(folder, names):
        rel = os.path.relpath(folder, base)
        return [n for n in names
                if n == "__pycache__" or os.path.normcase(os.path.normpath(os.path.join(rel, n))) in SKIP]

    shutil.copytree(base, target, ignore=ignore)
    os.makedirs(os.path.join(target, "Lib", "site-packages"), exist_ok=True)


def verify_runtime(runtime):
    """用內附的 Python、在隔離模式下確認 DayNote 需要的模組都能載入。"""
    code = "import tkinter, ssl, ctypes, winsound, json, http.server; print('ok')"
    out = subprocess.run([os.path.join(runtime, "python.exe"), "-I", "-c", code], capture_output=True, text=True)
    if out.stdout.strip() != "ok":
        sys.exit(f"內附 Python 檢查失敗：{out.stderr.strip()[-500:]}")


def main():
    check_python()
    stage = os.path.join(DIST, "DayNote")
    shutil.rmtree(DIST, ignore_errors=True)
    os.makedirs(stage)
    for name in APP_FILES:
        os.makedirs(os.path.join(stage, os.path.dirname(name)), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, name), os.path.join(stage, name))
    for name in APP_DIRS:
        shutil.copytree(os.path.join(ROOT, name), os.path.join(stage, name),
                        ignore=shutil.ignore_patterns("__pycache__"))
    runtime = os.path.join(stage, "runtime")
    print(f"複製 Python {sys.version.split()[0]}：{sys.base_prefix}")
    copy_runtime(runtime)
    verify_runtime(runtime)

    leaked = [n for n in PERSONAL_FILES
              if os.path.exists(os.path.join(stage, "data", n)) or os.path.exists(os.path.join(stage, n))]
    if leaked:
        sys.exit(f"壓縮包含有個人檔案，已中止：{leaked}")

    zip_path = os.path.join(DIST, "DayNote-portable.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, _, files in os.walk(stage):
            for f in files:
                full = os.path.join(folder, f)
                zf.write(full, os.path.relpath(full, DIST))
    print(f"完成：{zip_path}（{os.path.getsize(zip_path) / 1024 / 1024:.1f} MB）")


if __name__ == "__main__":
    main()
