import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.assembly_bills import sync_reform_party_bills
from backend.pdf_pledges_import import sync_pdf_pledges
from backend.policy_ssot import auto_link_public_commentary
from backend.rallypoint_commentary import sync_commentary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full SSOT sync pipeline.")
    parser.add_argument("--commentary-limit", type=int, default=1500)
    parser.add_argument("--no-commentary-body", action="store_true")
    parser.add_argument("--age-from", default="22")
    parser.add_argument("--age-to", default="22")
    parser.add_argument("--auto-link", action="store_true")
    parser.add_argument("--min-score", type=int, default=5)
    args = parser.parse_args()

    result = {
        "commentary": sync_commentary(
            actor_id=None,
            limit=max(1, min(args.commentary_limit, 3000)),
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

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
