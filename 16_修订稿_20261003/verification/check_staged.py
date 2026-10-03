"""入库前自检：暂存区体积、最大文件、以及不该入库的编译/逐时中间产物。"""
import pathlib
import subprocess

SKIP_MARKERS = ("build/", "dispatch.csv", "dispatch.png", ".batch_history", "__pycache__")


def main() -> None:
    raw = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"], capture_output=True
    ).stdout.decode("utf-8")
    files = [f for f in raw.split("\0") if f]
    total = 0
    big = []
    for name in files:
        path = pathlib.Path(name)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total += size
        if size > 1_000_000:
            big.append((size, name))
    print("暂存 %d 个文件，%.1f MB" % (len(files), total / 1e6))
    print("大于 1 MB 的文件：")
    for size, name in sorted(big, reverse=True)[:12]:
        print("  %7.2f MB  %s" % (size / 1e6, name))
    unwanted = [f for f in files if any(m in f.replace("\\", "/") for m in SKIP_MARKERS)]
    print("不该入库的：", unwanted[:5] if unwanted else "无")


if __name__ == "__main__":
    main()
