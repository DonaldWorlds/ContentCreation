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

    # Permanently delete an account row (use only if you mistyped and want
    # the id reused; otherwise prefer `deactivate` to preserve history)
    python -m zerino.cli.add_account remove --id <account-db-id>
"""
from __future__ import annotations

import argparse

from zerino.db.repositories.accounts_repository import (
    AccountHasPostsError,
    add_account,
    deactivate_account,
    delete_account,
    list_all_accounts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage registered social accounts")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # add
    p_add = sub.add_parser("add", help="Register a new account")
    p_add.add_argument("--platform", required=True,
                       choices=["tiktok", "youtube_shorts", "facebook_reels", "twitter"],
                       help="Platform name")
    p_add.add_argument("--handle", required=True, help="Account handle or display name")
    p_add.add_argument("--zernio-account-id", required=True,
                       help="24-char account id from Zernio")
    p_add.add_argument("--profile-id", default=None, help="Zernio profile id (optional)")

    # list
    sub.add_parser("list", help="List all registered accounts")

    # deactivate
    p_deact = sub.add_parser("deactivate", help="Deactivate an account (keep row)")
    p_deact.add_argument("--id", type=int, required=True, help="Account DB id")

    # remove (hard delete)
    p_rm = sub.add_parser("remove", help="Permanently delete an account row")
    p_rm.add_argument("--id", type=int, required=True, help="Account DB id")
    p_rm.add_argument(
        "--force", action="store_true",
        help="Also delete every post that references this account "
             "(use only if the account row is genuinely wrong; for normal "
             "'stop using this account' use `deactivate` instead).",
    )

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

    elif args.cmd == "remove":
        try:
            n = delete_account(args.id, force=args.force)
        except AccountHasPostsError as e:
            print(f"Cannot remove account id={e.account_id} — {e.post_count} post(s) reference it.")
            print("Choose one:")
            print(f"  python -m zerino.cli.add_account deactivate --id {e.account_id}")
            print(f"      (keeps the account + posts in the DB, just stops using it)")
            print(f"  python -m zerino.cli.add_account remove --id {e.account_id} --force")
            print(f"      (deletes the account AND all {e.post_count} post(s) — destructive)")
            return
        if n:
            print(f"Account id={args.id} permanently deleted.")
        else:
            print(f"No account with id={args.id} found.")


if __name__ == "__main__":
    main()
