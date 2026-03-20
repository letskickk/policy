from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.database import get_connection, init_db


API_BASE = "https://prod-api.givemoney.kr"
WEB_BASE = "https://givemoney.kr"
PAGE_SIZE = 100
PROFILE_SCAN_MAX_ID = 220
PROFILE_PAGE_MARKER = "\uD6C4\uC6D0\uC13C\uD130"
FALLBACK_REQUIRED_MARKER = "politicianName"
POSITION_PATTERN = (
    "\uAD11\uC5ED\uB2E8\uCCB4\uC7A5|"
    "\uAE30\uCD08\uB2E8\uCCB4\uC7A5|"
    "\uAD11\uC5ED\uC758\uC6D0|"
    "\uAE30\uCD08\uC758\uC6D0|"
    "\uAD6D\uD68C\uC758\uC6D0"
)

REGION_NAME_TO_CODE = {
    "\uC11C\uC6B8\uD2B9\uBCC4\uC2DC": "11",
    "\uBD80\uC0B0\uAD11\uC5ED\uC2DC": "26",
    "\uB300\uAD6C\uAD11\uC5ED\uC2DC": "27",
    "\uC778\uCC9C\uAD11\uC5ED\uC2DC": "28",
    "\uAD11\uC8FC\uAD11\uC5ED\uC2DC": "29",
    "\uB300\uC804\uAD11\uC5ED\uC2DC": "30",
    "\uC6B8\uC0B0\uAD11\uC5ED\uC2DC": "31",
    "\uC138\uC885\uD2B9\uBCC4\uC790\uCE58\uC2DC": "36",
    "\uACBD\uAE30\uB3C4": "41",
    "\uAC15\uC6D0\uD2B9\uBCC4\uC790\uCE58\uB3C4": "42",
    "\uCDA9\uCCAD\uBD81\uB3C4": "43",
    "\uCDA9\uCCAD\uB0A8\uB3C4": "44",
    "\uC804\uBD81\uD2B9\uBCC4\uC790\uCE58\uB3C4": "45",
    "\uC804\uB77C\uB0A8\uB3C4": "46",
    "\uACBD\uC0C1\uBD81\uB3C4": "47",
    "\uACBD\uC0C1\uB0A8\uB3C4": "48",
    "\uC81C\uC8FC\uD2B9\uBCC4\uC790\uCE58\uB3C4": "50",
}

POSITION_TO_ELECTION_TYPE = {
    "\uAD11\uC5ED\uB2E8\uCCB4\uC7A5": "metro_mayor",
    "\uAE30\uCD08\uB2E8\uCCB4\uC7A5": "local_mayor",
    "\uAD11\uC5ED\uC758\uC6D0": "regional_council",
    "\uAE30\uCD08\uC758\uC6D0": "local_council",
    "\uAD6D\uD68C\uC758\uC6D0": "national_assembly",
}


@dataclass
class GivemoneyCandidate:
    external_id: str
    name: str
    region_name: str
    region_code: Optional[str]
    district_name: Optional[str]
    position_name: Optional[str]
    election_type: Optional[str]
    profile_url: str
    photo_url: Optional[str]
    support_url: Optional[str]
    bio: Optional[str]
    raw_payload: dict[str, Any]


def _normalize_text(value: Optional[str]) -> str:
    text = unicodedata.normalize("NFC", (value or "").strip())
    text = re.sub(r"\s+", "", text)
    return text.lower()


def _normalize_district(value: Optional[str]) -> str:
    text = _normalize_text(value)
    text = text.replace("\uC120\uAC70\uAD6C", "")
    return text


def _build_photo_url(image_url: Optional[str]) -> Optional[str]:
    if not image_url:
        return None
    if image_url.startswith("http://") or image_url.startswith("https://"):
        return image_url
    return urllib.parse.urljoin(API_BASE, image_url)


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "policy-local-sync/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "policy-local-sync/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="ignore")


def _iter_donation_groups(search_term: Optional[str] = None):
    page = 0
    while True:
        params = {
            "page": page,
            "size": PAGE_SIZE,
            "sort": "createdAt,desc",
        }
        if search_term:
            params["searchContent"] = search_term
        url = f"{API_BASE}/api/donation-groups?{urllib.parse.urlencode(params)}"
        payload = _fetch_json(url)
        groups = payload.get("data", {}).get("groups", [])
        yielded = 0
        for group in groups:
            for item in group.get("donationGroups", []) or []:
                yielded += 1
                yield item
        if yielded == 0:
            break
        page += 1


def _candidate_from_profile_page(profile_id: int) -> Optional[GivemoneyCandidate]:
    url = f"{WEB_BASE}/politicians/{profile_id}"
    try:
        html = _fetch_text(url)
    except Exception:
        return None
    if PROFILE_PAGE_MARKER not in html or FALLBACK_REQUIRED_MARKER not in html:
        return None

    def _extract(pattern: str) -> Optional[str]:
        match = re.search(pattern, html)
        if not match:
            return None
        value = match.group(1)
        if "\\u" in value or "\\x" in value:
            try:
                return bytes(value, "utf-8").decode("unicode_escape")
            except Exception:
                pass
        return value

    name = _extract(r'\\"politicianName\\":\\"([^"]+)\\"')
    candidate_details = _extract(r'\\"candidateDetails\\":\\"([^"]+)\\"')
    description = _extract(r'\\"description\\":\\"([^"]*)\\"')
    image_url = _extract(r'\\"imageUrl\\":\\"([^"]+)\\"')
    if not name or not candidate_details:
        return None

    detail_match = re.search(
        rf"^(?P<region>\S+)\s+(?P<district>.+?)\s+(?P<position>{POSITION_PATTERN})\s+\uD6C4\uBCF4\s+(?P<name>.+)$",
        candidate_details,
    )
    if not detail_match:
        return None

    region_name = detail_match.group("region").strip()
    district_name = detail_match.group("district").strip() or None
    position_name = detail_match.group("position").strip()
    parsed_name = detail_match.group("name").strip()
    final_name = name.strip() or parsed_name
    region_code = REGION_NAME_TO_CODE.get(region_name)
    election_type = POSITION_TO_ELECTION_TYPE.get(position_name)
    if not final_name or not region_code or not election_type:
        return None

    profile_url = url
    return GivemoneyCandidate(
        external_id=str(profile_id),
        name=final_name,
        region_name=region_name,
        region_code=region_code,
        district_name=district_name,
        position_name=position_name,
        election_type=election_type,
        profile_url=profile_url,
        photo_url=_build_photo_url(image_url),
        support_url=profile_url,
        bio=(description or "").strip() or None,
        raw_payload={
            "profile_page": True,
            "candidateDetails": candidate_details,
            "description": description,
            "imageUrl": image_url,
            "politicianName": final_name,
        },
    )


def _load_unmatched_candidates(conn, candidate_name: Optional[str]) -> list[dict[str, Any]]:
    sql = """
        SELECT c.id, c.name, c.region_code, c.election_type,
               COALESCE(u.district_name, c.district_name) AS district_name
        FROM candidates c
        LEFT JOIN users u ON u.id = c.user_id
        LEFT JOIN candidate_external_profiles cep ON cep.candidate_id = c.id AND cep.source_key = 'givemoney'
        WHERE cep.id IS NULL
    """
    params: list[object] = []
    if candidate_name:
        sql += " AND c.name = ?"
        params.append(candidate_name)
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def _iter_profile_page_candidates(target_names: set[str], max_id: int = PROFILE_SCAN_MAX_ID):
    remaining = set(target_names)
    for profile_id in range(1, max_id + 1):
        candidate = _candidate_from_profile_page(profile_id)
        if not candidate:
            continue
        if remaining and candidate.name not in remaining:
            continue
        if candidate.name in remaining:
            remaining.discard(candidate.name)
        yield candidate
        if not remaining:
            break


def _to_candidate(item: dict[str, Any]) -> GivemoneyCandidate:
    candidate_info = item.get("candidateInfo") or {}
    external_id = str(item.get("id"))
    profile_url = f"{WEB_BASE}/politicians/{external_id}"
    return GivemoneyCandidate(
        external_id=external_id,
        name=(candidate_info.get("name") or item.get("name") or "").strip(),
        region_name=(candidate_info.get("electionRegion") or "").strip(),
        region_code=REGION_NAME_TO_CODE.get((candidate_info.get("electionRegion") or "").strip()),
        district_name=(candidate_info.get("electionDistrict") or "").strip() or None,
        position_name=(candidate_info.get("electionPosition") or "").strip() or None,
        election_type=POSITION_TO_ELECTION_TYPE.get((candidate_info.get("electionPosition") or "").strip()),
        profile_url=profile_url,
        photo_url=_build_photo_url(item.get("imageUrl")),
        support_url=profile_url,
        bio=(item.get("description") or "").strip() or None,
        raw_payload=item,
    )


def _district_matches(local_district: Optional[str], external_district: Optional[str]) -> bool:
    left = _normalize_district(local_district)
    right = _normalize_district(external_district)
    if not left and not right:
        return True
    if not left or not right:
        return False
    return left == right or left in right or right in left


def _find_match(conn, external: GivemoneyCandidate, candidate_name: Optional[str] = None):
    if not external.name or not external.region_code or not external.election_type:
        return None

    rows = conn.execute(
        """
        SELECT c.id, c.name, c.region_code,
               COALESCE(u.district_name, c.district_name) AS district_name,
               c.election_type, c.election_level
        FROM candidates c
        LEFT JOIN users u ON u.id = c.user_id
        WHERE c.name = ?
          AND c.region_code = ?
          AND c.election_type = ?
        ORDER BY c.id ASC
        """,
        (external.name, external.region_code, external.election_type),
    ).fetchall()

    if candidate_name and external.name != candidate_name:
        return None

    if len(rows) == 1 and not external.district_name:
        return rows[0]

    exact = [row for row in rows if _district_matches(row["district_name"], external.district_name)]
    if len(exact) == 1:
        return exact[0]
    if len(rows) == 1 and (not external.district_name or not rows[0]["district_name"]):
        return rows[0]
    return None


def sync_profiles(*, dry_run: bool, search_term: Optional[str], candidate_name: Optional[str], limit: Optional[int], verbose: bool) -> dict[str, Any]:
    init_db()
    conn = get_connection()
    scanned = 0
    matched = 0
    upserted = 0
    unmatched: list[dict[str, Any]] = []
    preview: list[dict[str, Any]] = []
    matched_candidate_ids: set[int] = set()

    try:
        for item in _iter_donation_groups(search_term=search_term):
            external = _to_candidate(item)
            if candidate_name and external.name != candidate_name:
                continue
            scanned += 1
            row = _find_match(conn, external, candidate_name=candidate_name)
            if row is None:
                unmatched.append(
                    {
                        "name": external.name,
                        "region_name": external.region_name,
                        "district_name": external.district_name,
                        "position_name": external.position_name,
                        "external_id": external.external_id,
                    }
                )
                if verbose:
                    print(f"[miss] {external.name} | {external.region_name} | {external.district_name or '-'} | {external.position_name or '-'}")
            else:
                matched += 1
                matched_candidate_ids.add(int(row["id"]))
                payload_json = json.dumps(external.raw_payload, ensure_ascii=False)
                record = {
                    "candidate_id": int(row["id"]),
                    "candidate_name": row["name"],
                    "external_id": external.external_id,
                    "profile_url": external.profile_url,
                    "photo_url": external.photo_url,
                    "support_url": external.support_url,
                }
                preview.append(record)
                if verbose:
                    print(f"[match] #{row['id']} {row['name']} <= givemoney:{external.external_id}")
                if not dry_run:
                    conn.execute(
                        """
                        INSERT INTO candidate_external_profiles (
                            candidate_id, source_key, external_id, external_profile_url,
                            external_photo_url, external_support_url, external_bio,
                            matched_name, matched_region, matched_district, matched_position,
                            raw_payload_json, last_synced_at
                        )
                        VALUES (?, 'givemoney', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        ON CONFLICT(candidate_id, source_key) DO UPDATE SET
                            external_id = excluded.external_id,
                            external_profile_url = excluded.external_profile_url,
                            external_photo_url = excluded.external_photo_url,
                            external_support_url = excluded.external_support_url,
                            external_bio = excluded.external_bio,
                            matched_name = excluded.matched_name,
                            matched_region = excluded.matched_region,
                            matched_district = excluded.matched_district,
                            matched_position = excluded.matched_position,
                            raw_payload_json = excluded.raw_payload_json,
                            last_synced_at = excluded.last_synced_at
                        """,
                        (
                            int(row["id"]),
                            external.external_id,
                            external.profile_url,
                            external.photo_url,
                            external.support_url,
                            external.bio,
                            external.name,
                            external.region_name,
                            external.district_name,
                            external.position_name,
                            payload_json,
                        ),
                    )
                    upserted += 1
            if limit is not None and scanned >= limit:
                break

        fallback_targets = _load_unmatched_candidates(conn, candidate_name)
        fallback_target_names = {row["name"] for row in fallback_targets if row["id"] not in matched_candidate_ids}
        if fallback_target_names:
            for external in _iter_profile_page_candidates(fallback_target_names):
                row = _find_match(conn, external, candidate_name=candidate_name)
                if row is None or int(row["id"]) in matched_candidate_ids:
                    continue
                matched += 1
                matched_candidate_ids.add(int(row["id"]))
                payload_json = json.dumps(external.raw_payload, ensure_ascii=False)
                record = {
                    "candidate_id": int(row["id"]),
                    "candidate_name": row["name"],
                    "external_id": external.external_id,
                    "profile_url": external.profile_url,
                    "photo_url": external.photo_url,
                    "support_url": external.support_url,
                }
                preview.append(record)
                if verbose:
                    print(f"[fallback-match] #{row['id']} {row['name']} <= givemoney:{external.external_id}")
                if not dry_run:
                    conn.execute(
                        """
                        INSERT INTO candidate_external_profiles (
                            candidate_id, source_key, external_id, external_profile_url,
                            external_photo_url, external_support_url, external_bio,
                            matched_name, matched_region, matched_district, matched_position,
                            raw_payload_json, last_synced_at
                        )
                        VALUES (?, 'givemoney', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        ON CONFLICT(candidate_id, source_key) DO UPDATE SET
                            external_id = excluded.external_id,
                            external_profile_url = excluded.external_profile_url,
                            external_photo_url = excluded.external_photo_url,
                            external_support_url = excluded.external_support_url,
                            external_bio = excluded.external_bio,
                            matched_name = excluded.matched_name,
                            matched_region = excluded.matched_region,
                            matched_district = excluded.matched_district,
                            matched_position = excluded.matched_position,
                            raw_payload_json = excluded.raw_payload_json,
                            last_synced_at = excluded.last_synced_at
                        """,
                        (
                            int(row["id"]),
                            external.external_id,
                            external.profile_url,
                            external.photo_url,
                            external.support_url,
                            external.bio,
                            external.name,
                            external.region_name,
                            external.district_name,
                            external.position_name,
                            payload_json,
                        ),
                    )

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return {
        "dry_run": dry_run,
        "scanned": scanned,
        "matched": matched,
        "upserted": upserted,
        "preview": preview[:10],
        "unmatched_preview": unmatched[:10],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync givemoney candidate profile metadata into the local database.")
    parser.add_argument("--dry-run", action="store_true", help="Print matches without writing to the database.")
    parser.add_argument("--search-term", help="Optional givemoney searchContent filter.")
    parser.add_argument("--candidate-name", help="Only attempt to match a single candidate name.")
    parser.add_argument("--limit", type=int, help="Stop after scanning this many external candidates.")
    parser.add_argument("--verbose", action="store_true", help="Print per-candidate matching logs.")
    args = parser.parse_args()

    result = sync_profiles(
        dry_run=args.dry_run,
        search_term=args.search_term,
        candidate_name=args.candidate_name,
        limit=args.limit,
        verbose=args.verbose,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
