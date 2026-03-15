import json
from pathlib import Path

from backend import database
from backend.policy_ssot import get_policy_position, upsert_policy_position


def main() -> None:
    database.init_db()
    seed_path = Path("data/policy_brief_seed.json")
    rows = json.loads(seed_path.read_text(encoding="utf-8"))
    updated = 0
    for row in rows:
        current = get_policy_position(int(row["id"]))
        upsert_policy_position(
            position_id=current["id"],
            title=current["title"],
            category=current["category"],
            summary=current["summary"],
            body=current["body"],
            status=current["status"],
            owner_scope=current["owner_scope"],
            effective_from=current["effective_from"],
            effective_to=current["effective_to"],
            version_label=current["version_label"] or None,
            official_summary=row.get("official_summary"),
            key_points=row.get("key_points"),
            relevance_note=row.get("relevance_note"),
            actor_id=None,
        )
        updated += 1
    print({"updated": updated, "seed_path": str(seed_path)})


if __name__ == "__main__":
    main()
