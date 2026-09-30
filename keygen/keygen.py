"""
keygen.py -- License key generation tool for the arrow licensing system.

Usage:
    python keygen/keygen.py --user "John Doe" --expires 2027-12-31

Outputs a formatted license key like:  ARW-XXXXX-XXXXX-XXXXX-XXXXX
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from license_core import create_license_payload, sign_license_payload, format_license_key


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate an arrow license key")
    parser.add_argument("--user", required=True, help="Licensee name")
    parser.add_argument(
        "--expires", default=None, help="Expiration date YYYY-MM-DD (omit for no expiration)"
    )
    parser.add_argument(
        "--limit", type=int, default=1, help="Hardware activation limit (default: 1)"
    )
    args = parser.parse_args()

    payload = create_license_payload(
        user=args.user,
        issue=datetime.now().strftime("%Y-%m-%d"),
        expires=args.expires,
        hw_limit=args.limit,
    )

    print(f"  User:        {args.user}")
    print(f"  Issue date:  {payload['issue']}")
    print(f"  Expires:     {args.expires or 'never'}")
    print(f"  HW limit:    {args.limit}")
    print()

    inner = sign_license_payload(payload)
    formatted = format_license_key(inner)

    print("=" * 50)
    print("LICENSE KEY:")
    print(formatted)
    print("=" * 50)
    print()
    print("Share this key with the user. They will enter it during installation.")


if __name__ == "__main__":
    main()
