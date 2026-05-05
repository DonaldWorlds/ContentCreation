"""
Register and manage Zernio-connected social accounts in the local DB.

Before posting, you must register at least one account per platform.
Get your zernio_account_id from the Zernio dashboard or by running:
    python -m zerino.publishing.zernio.accounts  (prints connected accounts)

Usage:
    # Register an account
    python -m zerino.cli.add_account add \\
        --platform tiktok \\
        --handle @myhandle \\
        --zernio-account-id <24-char-id>

    # List all registered accounts
    python -m zerino.cli.add_account list

    # Deactivate an account (keeps row, stops it from receiving new posts)
    python -m zerino.cli.add_account deactivate --id <account-db-id>
"""
from __future__ import annotations

import argparse

from zerino.db.repositories.accounts_repository import (
    add_account,
    deactivate_account,
    list_all_accounts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage registered social accounts")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # add
    p_add = sub.add_parser("add", help="Register a new account")
    p_add.add_argument("--platform", required=True,
                       choices=["tiktok", "youtube_shorts", "instagram_reels", "twitter", "pinterest"],
                       help="Platform name")
    p_add.add_argument("--handle", required=True, help="Account handle or display name")
    p_add.add_argument("--zernio-account-id", required=True,
                       help="24-char account id from Zernio")
    p_add.add_argument("--profile-id", default=None, help="Zernio profile id (optional)")

    # list
    sub.add_parser("list", help="List all registered accounts")

    # deactivate
    p_deact = sub.add_parser("deactivate", help="Deactivate an account")
    p_deact.add_argument("--id", type=int, required=True, help="Account DB id")

    args = parser.parse_args()

    if args.cmd == "add":
        row_id = add_account(
            platform=args.platform,
            handle=args.handle,
            zernio_account_id=args.zernio_account_id,
            profile_id=args.profile_id,
        )
        print(f"Registered: id={row_id} platform={args.platform} handle={args.handle}")

    elif args.cmd == "list":
        rows = list_all_accounts()
        if not rows:
            print("No accounts registered. Use: python -m zerino.cli.add_account add ...")
            return
        print(f"{'ID':<4}  {'PLATFORM':<18}  {'HANDLE':<20}  {'ZERNIO ID':<26}  STATUS")
        print("-" * 78)
        for r in rows:
            status = "active" if r["active"] else "inactive"
            print(
                f"{r['id']:<4}  {r['platform']:<18}  {r['handle']:<20}"
                f"  {r['zernio_account_id']:<26}  {status}"
            )

    elif args.cmd == "deactivate":
        deactivate_account(args.id)
        print(f"Account id={args.id} deactivated.")


if __name__ == "__main__":
    main()
