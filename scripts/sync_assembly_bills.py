import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.assembly_bills import sync_reform_party_bills
from backend.database import init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Reform Party representative bills from Assembly bill system")
    parser.add_argument("--age-from", default="22")
    parser.add_argument("--age-to", default="22")
    args = parser.parse_args()

    init_db()
    result = sync_reform_party_bills(actor_id=None, age_from=args.age_from, age_to=args.age_to)
    print(result)


if __name__ == "__main__":
    main()
