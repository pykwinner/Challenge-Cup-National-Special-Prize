"""Retrieve the cited GAMS source separately and require its recorded SHA256."""
import argparse
import hashlib
from pathlib import Path
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data/published_multiobjective_20261002/epscm.pdf"
URL = "https://www.gams.com/modlib/adddocs/epscm.pdf"
EXPECTED_SHA256 = "964c7bef93a278a4bea264a1d6f8fb51faf328644f3034215e871343eea3730a"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-source", type=Path, help="Use an already acquired original PDF.")
    args = parser.parse_args()
    if TARGET.is_file():
        content = TARGET.read_bytes()
        origin = "existing local copy"
    elif args.local_source:
        content = args.local_source.read_bytes()
        origin = "provided local copy"
    else:
        request = urllib.request.Request(URL, headers={"User-Agent": "xiaotiao-source-retrieval/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read(10 * 1024 * 1024 + 1)
        origin = URL
    digest = hashlib.sha256(content).hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit("Source identity mismatch; no file written. Review the publisher's source before proceeding.")
    if not TARGET.is_file():
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        with TARGET.open("xb") as stream:
            stream.write(content)
    print(f"Verified {TARGET.relative_to(ROOT).as_posix()} from {origin}")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
