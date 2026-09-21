from pathlib import Path
import argparse
import sys

from app.validate import validate


def generate_site(site_path):
    """
    Generate exhibits for one site JSON file.
    """

    from pipeline import generate_site as pipeline_generate

    site_path = Path(site_path)

    if not site_path.exists():
        raise FileNotFoundError(
            f"Site input file not found: {site_path}"
        )

    return pipeline_generate(str(site_path))


def batch_sites(sites_dir):
    """
    Generate exhibits for all JSON site files
    in the specified directory.
    """

    sites_dir = Path(sites_dir)

    if not sites_dir.exists():
        raise FileNotFoundError(
            f"Sites directory not found: {sites_dir}"
        )

    if not sites_dir.is_dir():
        raise NotADirectoryError(
            f"Not a directory: {sites_dir}"
        )

    site_files = sorted(
        sites_dir.glob("*.json")
    )

    if not site_files:
        raise FileNotFoundError(
            f"No .json site files found in {sites_dir}"
        )

    results = []

    for site_file in site_files:

        print()
        print("=" * 70)
        print(f"GENERATING {site_file.name}")
        print("=" * 70)

        try:

            result = generate_site(site_file)

            results.append(
                {
                    "site": site_file.name,
                    "status": "PASS",
                    "result": result,
                }
            )

        except Exception as exc:

            print(
                f"FAILED: {site_file.name}: {exc}"
            )

            results.append(
                {
                    "site": site_file.name,
                    "status": "FAIL",
                    "error": str(exc),
                }
            )

    failed = [
        item
        for item in results
        if item["status"] == "FAIL"
    ]

    print()
    print("=" * 70)
    print("BATCH COMPLETE")
    print("=" * 70)

    print(f"Total: {len(results)}")
    print(f"Passed: {len(results) - len(failed)}")
    print(f"Failed: {len(failed)}")

    return results


def main():
    parser = argparse.ArgumentParser(
        prog="exhibits",
        description="Site Exhibit Agent",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    # ========================================================
    # GENERATE
    # ========================================================

    generate_parser = subparsers.add_parser(
        "generate",
        help="Generate exhibits for one site",
    )

    generate_parser.add_argument(
        "site_json",
        help="Path to the site JSON file",
    )

    # ========================================================
    # BATCH
    # ========================================================

    batch_parser = subparsers.add_parser(
        "batch",
        help="Generate exhibits for all sites in a directory",
    )

    batch_parser.add_argument(
        "sites_dir",
        help="Directory containing site JSON files",
    )

    # ========================================================
    # VALIDATE
    # ========================================================

    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate a generated site exhibit",
    )

    validate_parser.add_argument(
        "output_dir",
        help="Generated site output directory",
    )

    args = parser.parse_args()

    try:

        # ----------------------------------------------------
        # GENERATE
        # ----------------------------------------------------

        if args.command == "generate":

            generate_site(
                args.site_json
            )

            return 0

        # ----------------------------------------------------
        # BATCH
        # ----------------------------------------------------

        if args.command == "batch":

            results = batch_sites(
                args.sites_dir
            )

            failed = any(
                item["status"] == "FAIL"
                for item in results
            )

            return 1 if failed else 0

        # ----------------------------------------------------
        # VALIDATE
        # ----------------------------------------------------

        if args.command == "validate":

            report = validate(
                args.output_dir
            )

            return (
                0
                if report["valid"]
                else 1
            )

        return 0

    except Exception as exc:

        print()
        print("=" * 70)
        print("CLI FAILED")
        print("=" * 70)
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())