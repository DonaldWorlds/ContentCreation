"""
Manage the post-caption pool.

The auto-flow (`clip_to_posts`) picks a random caption from this pool for
every clip it queues. Set up the pool ONCE, the system rotates it forever.

Usage:
    # Add a caption (with optional hashtags)
    python -m zerino.cli.captions add "Wait for it 👀" --hashtags "#warzone #cod #fyp"

    # Bump weight for captions you want chosen more often
    python -m zerino.cli.captions add "Banger play 🔥" --hashtags "#cod #viral" --weight 3

    # See what's in the pool
    python -m zerino.cli.captions list

    # Stop using one but keep it in the pool
    python -m zerino.cli.captions deactivate --id 4

    # Permanently delete
    python -m zerino.cli.captions remove --id 4
"""
from __future__ import annotations

import argparse

from zerino.db.repositories.captions_repository import (
    add_caption,
    deactivate_caption,
    delete_caption,
    list_captions,
    reactivate_caption,
)


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("Pool is empty. Add one with:")
        print('    python -m zerino.cli.captions add "Your text 🔥" --hashtags "#cod"')
        return

    for r in rows:
        status = "active" if r["active"] else "inactive"
        tags = r["hashtags"] or "(none)"
        print(f"  id={r['id']:<3}  weight={r['weight']:<2}  [{status}]")
        print(f"      text: {r['text']}")
        print(f"      tags: {tags}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the post-caption pool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Add a caption to the pool")
    p_add.add_argument("text", help="Post text/caption")
    p_add.add_argument("--hashtags", default=None,
                       help='Hashtag string, e.g. "#warzone #cod #gaming"')
    p_add.add_argument("--weight", type=int, default=1,
                       help="Pick weight (higher = chosen more often). Default 1.")

    sub.add_parser("list", help="List all captions in the pool")

    p_deact = sub.add_parser("deactivate", help="Stop using a caption (keep in pool)")
    p_deact.add_argument("--id", type=int, required=True)

    p_react = sub.add_parser("reactivate", help="Re-enable a deactivated caption")
    p_react.add_argument("--id", type=int, required=True)

    p_del = sub.add_parser("remove", help="Permanently delete a caption")
    p_del.add_argument("--id", type=int, required=True)

    args = parser.parse_args()

    if args.cmd == "add":
        cid = add_caption(args.text, args.hashtags, args.weight)
        print(f"Added: id={cid}")

    elif args.cmd == "list":
        _print_table(list_captions())

    elif args.cmd == "deactivate":
        deactivate_caption(args.id)
        print(f"Caption id={args.id} deactivated")

    elif args.cmd == "reactivate":
        reactivate_caption(args.id)
        print(f"Caption id={args.id} reactivated")

    elif args.cmd == "remove":
        delete_caption(args.id)
        print(f"Caption id={args.id} deleted")


if __name__ == "__main__":
    main()
