"""Bind an operator-observed agy version to immutable transcript bytes.

This does not authenticate the transcript or discover its historical producer version.
"""
import argparse
import hashlib
import json
from pathlib import Path

from evidence import ANTIGRAVITY_CLI_VERSION, LOG_ADAPTERS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--cli-version", choices=[ANTIGRAVITY_CLI_VERSION], required=True,
                        help="Version observed when this log was produced, not merely the current installed version")
    args = parser.parse_args()
    manifest = {"format": LOG_ADAPTERS["antigravity"], "cli_version": args.cli_version,
                "sha256": hashlib.sha256(args.log.read_bytes()).hexdigest()}
    sidecar = Path(str(args.log) + ".version.json")
    # Never silently repin changed bytes: a new observation needs an explicit new sidecar.
    with sidecar.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(manifest, indent=2) + "\n")
    print(sidecar)


if __name__ == "__main__":
    main()
