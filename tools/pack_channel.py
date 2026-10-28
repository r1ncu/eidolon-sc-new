"""把 channels/ 打成一个能单独交付的插件包。

    python tools/pack_channel.py                 # 用下面写死的版本号
    python tools/pack_channel.py --version 1.0.1

产物：

    _pkg/qq-channel-<版本>/
    _pkg/qq-channel-<版本>.tar.gz
    _pkg/qq-channel-<版本>.zip
    _pkg/qq-channel-<版本>.SHA256SUMS

包里只有通道本身、文档、模板和一个自检脚本。**不含**：角色数据（characters/）、
`.env`、数据库、表情、NapCat、日志。最后会扫一遍，出现任何一个角色名就失败。
"""

import argparse
import hashlib
import re
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "qq-channel"
CHANNELS = ROOT / "channels"

VERSION = "1.0.0"
COMPATIBLE = "scbot-new >= 1.4.3"

# 掉进去就说明把某个人的数据带出来了。`制作人` 不算：它是一个通用词。
FORBIDDEN = (
    "fuyuko",
    "冬優子",
    "冬优子",
    "黛",
    "Straylight",
    "あさひ",
    "Mayuzumi",
    "sk-",
    "192.168.",
    "CT100",
    "CT101",
    "eidolon_key",
)

TEXT_SUFFIXES = {".py", ".md", ".toml", ".example", ".txt", ".yaml", ".yml"}


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 QQ 通道插件")
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--out", default=str(ROOT.parent / "_pkg"))
    options = parser.parse_args()

    target = Path(options.out) / f"qq-channel-{options.version}"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    _channels(target)
    _docs(target)
    _manifest(target, options.version)

    bad = _scan(target)
    if bad:
        print("\n包里出现了不该有的东西：")
        for path, word in bad:
            print(f"  {path.relative_to(target)}: {word}")
        print("\n删掉它们再打包。")
        return 1

    archives = _archives(target, options.version)
    _sums(archives)
    print(f"\nOK  {target}")
    for path in archives:
        print(f"    {path.name}  {path.stat().st_size / 1024:.0f} KB")
    return 0


def _channels(target: Path) -> None:
    """The code, and only the code."""
    folder = target / "channels"
    folder.mkdir()
    for path in sorted(CHANNELS.glob("*.py")):
        shutil.copy2(path, folder / path.name)
    print(f"channels/  {len(list(folder.glob('*.py')))} 个模块")


def _docs(target: Path) -> None:
    shutil.copy2(DOCS / "README.md", target / "README.md")
    for path in sorted(DOCS.glob("0*.md")):
        (target / "docs").mkdir(exist_ok=True)
        shutil.copy2(path, target / "docs" / path.name)
    templates = target / "templates"
    shutil.copytree(DOCS / "templates", templates)
    (target / "tools").mkdir()
    shutil.copy2(DOCS / "tools" / "check.py", target / "tools" / "check.py")
    print(f"docs/      {len(list((target / 'docs').glob('*.md')))} 篇")
    print(f"templates/ {len(list(templates.rglob('*.*')))} 个文件")


def _manifest(target: Path, version: str) -> None:
    lines = "".join(
        f"{path.relative_to(target).as_posix()}\t{path.stat().st_size}\n"
        for path in sorted(target.rglob("*"))
        if path.is_file()
    )
    (target / "MANIFEST.txt").write_text(lines, encoding="utf-8")
    (target / "VERSION").write_text(
        f"qq-channel {version}\ncompatible: {COMPATIBLE}\n", encoding="utf-8"
    )
    code = sum(
        len(path.read_text(encoding="utf-8").splitlines())
        for path in (target / "channels").glob("*.py")
    )
    files = sum(1 for path in target.rglob("*") if path.is_file())
    print(f"合计       {files} 个文件 / {code} 行 Python")


def _scan(target: Path) -> list[tuple[Path, str]]:
    """Any character name, any key, any private address: a packaging bug, not a warning."""
    hits: list[tuple[Path, str]] = []
    for path in sorted(target.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        folded = text.casefold()
        for word in FORBIDDEN:
            if word.casefold() in folded:
                hits.append((path, word))
    return hits


def _archives(target: Path, version: str) -> list[Path]:
    stem = f"qq-channel-{version}"
    tar_path = target.parent / f"{stem}.tar.gz"
    zip_path = target.parent / f"{stem}.zip"

    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(target, arcname=stem)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                zf.write(path, f"{stem}/{path.relative_to(target).as_posix()}")
    return [tar_path, zip_path]


def _sums(paths: list[Path]) -> None:
    lines = []
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}\n")
    (paths[0].parent / f"{paths[0].name.replace('.tar.gz', '')}.SHA256SUMS").write_text(
        "".join(lines), encoding="utf-8"
    )


if __name__ == "__main__":
    sys.exit(main())
