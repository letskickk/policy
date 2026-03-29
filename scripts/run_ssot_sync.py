import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.assembly_bills import sync_reform_party_bills
from backend.pdf_pledges_import import sync_pdf_pledges
from backend.policy_ssot import auto_link_public_commentary
from backend.rallypoint_commentary import sync_commentary, sync_press
from scripts.sync_amaranth_meetings import sync_amaranth_meetings, sync_amaranth_rules


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Run the full SSOT sync pipeline.")
    parser.add_argument("--commentary-limit", type=int, default=1500)
    parser.add_argument("--press-limit", type=int, default=500)
    parser.add_argument("--no-commentary-body", action="store_true")
    parser.add_argument("--age-from", default="22")
    parser.add_argument("--age-to", default="22")
    parser.add_argument("--auto-link", action="store_true")
    parser.add_argument("--min-score", type=int, default=5)
    parser.add_argument("--skip-amaranth-meetings", action="store_true")
    parser.add_argument("--skip-amaranth-rules", action="store_true")
    args = parser.parse_args()

    result = {
        "commentary": sync_commentary(
            actor_id=None,
            limit=max(1, min(args.commentary_limit, 3000)),
            include_body=not args.no_commentary_body,
        ),
        "press": sync_press(
            actor_id=None,
            limit=max(1, min(args.press_limit, 3000)),
            include_body=not args.no_commentary_body,
        ),
        "bills": sync_reform_party_bills(actor_id=None, age_from=args.age_from, age_to=args.age_to),
        "pledges": sync_pdf_pledges(actor_id=None),
    }
    if args.auto_link:
        result["auto_link"] = auto_link_public_commentary(
            actor_id=None,
            limit=max(100, min(args.commentary_limit, 500)),
            min_score=max(1, min(args.min_score, 20)),
        )
    if not args.skip_amaranth_meetings:
        result["amaranth_meetings"] = sync_amaranth_meetings(
            base_url=os.getenv("AMARANTH_BASE_URL", "http://gw.reformparty.kr"),
            headless=os.getenv("AMARANTH_HEADLESS", "0") == "1",
            limit=int(os.getenv("AMARANTH_LIMIT", "20")),
            storage_state=os.getenv("AMARANTH_STORAGE_STATE", str(ROOT / "data" / "amaranth-storage-state.json")),
            dry_run=False,
        )
    if not args.skip_amaranth_rules:
        result["amaranth_rules"] = sync_amaranth_rules(
            base_url=os.getenv("AMARANTH_BASE_URL", "http://gw.reformparty.kr"),
            headless=os.getenv("AMARANTH_HEADLESS", "0") == "1",
            limit=int(os.getenv("AMARANTH_LIMIT", "20")),
            storage_state=os.getenv("AMARANTH_STORAGE_STATE", str(ROOT / "data" / "amaranth-storage-state.json")),
            dry_run=False,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
