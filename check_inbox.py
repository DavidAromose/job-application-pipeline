"""Check Gmail for replies to your job applications (read-only, opt-in).

    python check_inbox.py            # show what came in + proposed sheet updates (dry run)
    python check_inbox.py --days 30  # look further back
    python check_inbox.py --emails   # also list every matched email with its preview
    python check_inbox.py --apply    # write the proposed updates into the sheet

Requires INBOX_ACCESS=true in .env and a one-time `python authorize.py`.
Never sends, deletes or changes any email.
"""
from __future__ import annotations

import argparse
import re
import sys

from pipeline.inbox import apply_to_sheet, find_replies, propose_changes

ORDER = {"REJECTED": 0, "CLOSED": 1, "INTERVIEW": 2, "NEXT_STEP": 3, "RECEIPT": 4, "OTHER": 5}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--apply", action="store_true", help="write proposed updates to the sheet")
    ap.add_argument("--emails", action="store_true", help="list each matched email + preview")
    args = ap.parse_args()

    try:
        replies = find_replies(args.days)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read the inbox: {exc}")
        return 1

    print(f"{len(replies)} application-related email(s) in the last {args.days} days\n")
    if args.emails:
        for r in sorted(replies, key=lambda r: (ORDER.get(r.kind, 9), r.date)):
            dom = re.sub(r".*@", "@", r.sender).strip("> ")
            print(f"[{r.kind}] {r.date} {(r.employer or '(no sheet match)')[:30]:30} {dom[:28]:28} {r.subject[:60]}")
            print(f"      {r.snippet[:190]}")
        print()

    todo = [r for r in replies if r.row and r.kind in ("INTERVIEW", "NEXT_STEP")]
    if todo:
        print("ACTION NEEDED - these emails are waiting on you (assessments / next steps):")
        for r in sorted(todo, key=lambda r: r.date):
            print(f"  {r.date} {r.employer[:28]:28} {r.subject[:66]}")
            print(f"      {r.snippet[:170]}")
        print()

    unmatched = [r for r in replies if not r.row and r.kind in ("REJECTED", "CLOSED", "INTERVIEW", "NEXT_STEP")]
    if unmatched:
        print("Replies that did not match any active sheet row (check by hand):")
        for r in unmatched:
            dom = re.sub(r".*@", "@", r.sender).strip("> ")
            print(f"  [{r.kind}] {r.date} {dom[:26]:26} {r.subject[:70]}")
        print()

    proposals = propose_changes(replies)
    if not proposals:
        print("No sheet changes proposed.")
        return 0
    print("Proposed sheet updates:")
    for p in proposals:
        print(f"  row {p['row']:>3}  {p['employer'][:30]:30} {p['from']:>14} -> {p['to']:<10} ({p['evidence']})")
    if args.apply:
        print("\nApplied:")
        for c in apply_to_sheet(proposals):
            print("  ", c)
    else:
        print("\n(dry run - nothing written. Add --apply to record these.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
