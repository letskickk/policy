from argparse import ArgumentParser
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.database import init_db
from backend.nesdc_polls import sync_reform_party_polls


def main() -> None:
    parser = ArgumentParser(description="Sync NESDC reform party polling documents into SSOT.")
    parser.add_argument("--since", default="2024-02-01", help="Only import polls registered on or after this date.")
    parser.add_argument("--max-pages", type=int, default=30, help="Maximum result pages to scan per search term.")
    parser.add_argument(
        "--search-term",
        action="append",
        dest="search_terms",
        help="Search term to use on NESDC. Can be passed multiple times.",
    )
    args = parser.parse_args()

    init_db()
    result = sync_reform_party_polls(
        actor_id=None,
        since=args.since,
        search_terms=args.search_terms,
        max_pages_per_term=args.max_pages,
    )
    print(result)


if __name__ == "__main__":
    main()
