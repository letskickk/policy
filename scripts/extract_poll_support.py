"""Extract party support percentages from nesdc poll PDFs into support_lines."""
import json
import os
import re
import sqlite3
import tempfile
from urllib.parse import unquote
from urllib.request import Request, urlopen

import pdfplumber

PARTY_CLEAN = {
    "더불어\n민주당": "더불어민주당",
    "조국\n혁신당": "조국혁신당",
    "개혁\n신당": "개혁신당",
}
CHECK_PARTIES = ["더불어민주당", "국민의힘", "개혁신당"]


KNOWN_PARTIES = ["더불어민주당", "국민의힘", "조국혁신당", "진보당", "개혁신당"]

# Regex: "「정당명」 숫자%" or "「정당명」(숫자%)" or "정당명 숫자%"
TEXT_PATTERN = re.compile(
    r"[「\"]?(" + "|".join(re.escape(p) for p in KNOWN_PARTIES) + r")[」\"]?\s*"
    r"(?:\(?(\d+\.?\d*)%\)?)",
)


def _extract_from_tables(pdf) -> list[str]:
    """Try structured table extraction."""
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if not table or len(table) < 3:
                continue
            header = table[0]
            if not header:
                continue
            header_joined = " ".join(
                str(h or "").replace("\n", "") for h in header
            )
            if not all(p in header_joined for p in CHECK_PARTIES):
                continue
            for row in table[1:]:
                if not row or not row[0]:
                    continue
                if "전체" in str(row[0]):
                    lines = []
                    for ci, col in enumerate(header):
                        if col is None:
                            continue
                        col_clean = PARTY_CLEAN.get(col, col.replace("\n", ""))
                        if col_clean in KNOWN_PARTIES:
                            val = row[ci] if ci < len(row) else None
                            if val and re.match(r"[\d.]+", str(val)):
                                lines.append(f"{col_clean} {val}%")
                    if lines:
                        return lines
    return []


def _extract_from_text(pdf) -> list[str]:
    """Fallback: extract from page text using regex patterns."""
    for page in pdf.pages:
        text = page.extract_text() or ""
        # Pattern 1: "「더불어민주당」 48.9%" style (하남시 etc.)
        matches = TEXT_PATTERN.findall(text)
        if len(matches) >= 3:
            return [f"{party} {pct}%" for party, pct in matches]

        # Pattern 2: "전체 (N) (N) 57.7 24.1 2.3 1.1 3.5" with party header row above
        lines = text.split("\n")
        for li, line in enumerate(lines):
            # Find header line containing party names
            party_in_line = [p for p in KNOWN_PARTIES if p in line.replace(" ", "")]
            if len(party_in_line) >= 3:
                # Next line with "전체" should have values
                for next_line in lines[li + 1 : li + 5]:
                    if "전체" in next_line:
                        nums = re.findall(r"(\d+\.?\d+)", next_line)
                        # Skip first few nums (sample sizes in parentheses)
                        # Find values that look like percentages (< 100)
                        pct_vals = [
                            float(n) for n in nums if 0 < float(n) < 100
                        ]
                        if len(pct_vals) >= len(party_in_line):
                            return [
                                f"{p} {pct_vals[i]}%"
                                for i, p in enumerate(party_in_line)
                                if i < len(pct_vals)
                            ]
                        break
    return []


def extract_support_lines(pdf_path: str) -> list[str]:
    with pdfplumber.open(pdf_path) as pdf:
        result = _extract_from_tables(pdf)
        if result:
            return result
        return _extract_from_text(pdf)


def main():
    db_path = os.environ.get("DATABASE_PATH", "data/policy.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, title, metadata_json FROM policy_documents WHERE doc_type='poll_result' ORDER BY id DESC"
    ).fetchall()

    updated = 0
    for r in rows:
        md = json.loads(r[2]) if r[2] else {}
        atts = md.get("attachments", [])
        pdf_atts = [
            a for a in atts if (a.get("file_name", "") or "").endswith(".pdf")
        ]
        if not pdf_atts:
            print(f"SKIP id={r[0]} (no PDF)")
            continue

        url = unquote(pdf_atts[0].get("download_url", ""))
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urlopen(req, timeout=20).read()
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(data)
                tmp = f.name

            lines = extract_support_lines(tmp)
            os.unlink(tmp)

            if lines:
                md["support_lines"] = lines
                cur.execute(
                    "UPDATE policy_documents SET metadata_json = ? WHERE id = ?",
                    (json.dumps(md, ensure_ascii=False), r[0]),
                )
                updated += 1
                print(f"OK   id={r[0]} | {lines}")
            else:
                print(f"NOPE id={r[0]} | {r[1][:40]}")
        except Exception as e:
            print(f"ERR  id={r[0]} | {e}")

    conn.commit()
    conn.close()
    print(f"\nUpdated: {updated}")


if __name__ == "__main__":
    main()
