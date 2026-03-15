from argparse import ArgumentParser
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.database import init_db
from backend.rallypoint_commentary import sync_commentary


def main() -> None:
    parser = ArgumentParser(description="Sync Reform Party commentary from RallyPoint into SSOT.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of list items to fetch.")
    parser.add_argument(
        "--no-body",
        action="store_true",
        help="Skip detail-page body extraction and import only list metadata.",
    )
    args = parser.parse_args()

    init_db()
    result = sync_commentary(actor_id=None, limit=args.limit, include_body=not args.no_body)
    print(result)


if __name__ == "__main__":
    main()
