"""Signature-threshold calibration helper.

Given one or more KNOWN-SIGNED and KNOWN-UNSIGNED sample questionnaires, this
prints the measured ink density of each consent page and recommends a
``min_ink_density`` value to put in config.yaml.

Usage:
    python tools/calibrate_signature.py --signed a.pdf b.pdf --unsigned c.pdf d.pdf

The recommended threshold is placed midway (geometric mean) between the highest
unsigned density and the lowest signed density, so genuine signatures pass and
blank consent pages are rejected.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running directly from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adhd_intake.config import AppConfig  # noqa: E402
from adhd_intake.validation.signature import SignatureValidator  # noqa: E402


def measure(validator: SignatureValidator, paths: list[Path]) -> list[tuple[Path, float]]:
    out = []
    for p in paths:
        result = validator.validate(p)
        out.append((p, result.ink_density if result.ink_density is not None else -1.0))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibrate signature threshold")
    parser.add_argument("--signed", nargs="+", type=Path, required=True)
    parser.add_argument("--unsigned", nargs="+", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        config = AppConfig.load(args.config)
    except Exception:
        # Fall back to defaults if no config.yaml exists yet.
        from adhd_intake.config import ValidationConfig

        class _Stub:
            validation = ValidationConfig()

        config = _Stub()  # type: ignore[assignment]

    validator = SignatureValidator(config.validation)

    signed = measure(validator, args.signed)
    unsigned = measure(validator, args.unsigned)

    print("\nSIGNED samples (should be ABOVE the threshold):")
    for p, d in signed:
        print(f"  {d:.5f}  {p.name}")
    print("\nUNSIGNED samples (should be BELOW the threshold):")
    for p, d in unsigned:
        print(f"  {d:.5f}  {p.name}")

    min_signed = min(d for _, d in signed)
    max_unsigned = max(d for _, d in unsigned)

    print("\n" + "-" * 50)
    if min_signed <= max_unsigned:
        print(
            "WARNING: signed and unsigned densities overlap "
            f"(lowest signed {min_signed:.5f} <= highest unsigned {max_unsigned:.5f}).\n"
            "Ink density alone cannot separate these samples reliably — review the\n"
            "signature region settings or collect cleaner samples."
        )
        return 1

    recommended = (min_signed * max_unsigned) ** 0.5  # geometric mean
    print(f"Highest unsigned: {max_unsigned:.5f}")
    print(f"Lowest  signed  : {min_signed:.5f}")
    print(f"\n>>> Recommended  min_ink_density: {recommended:.5f}")
    print("    Put this under 'validation:' in config.yaml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
