"""Download Thermo RawFileReader 8 into the folder XIC Extractor reads by default."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from xic_extractor.thermo_reader_install import (
    THERMO_LICENSE_URL,
    THERMO_READER_VERSION,
    ThermoReaderInstallError,
    default_thermo_reader_dir,
    install_thermo_reader,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--accept-thermo-license",
        action="store_true",
        help=f"Confirm that you accept the Thermo license: {THERMO_LICENSE_URL}",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=None,
        help=f"Install folder (default: {default_thermo_reader_dir()})",
    )
    args = parser.parse_args(argv)
    try:
        target = install_thermo_reader(
            args.destination, accept_license=args.accept_thermo_license
        )
    except (ThermoReaderInstallError, OSError) as exc:
        print(exc)
        return 1
    print(f"RawFileReader {THERMO_READER_VERSION} is installed in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
