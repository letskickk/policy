# Amaranth Sync

Amaranth ONECHAMBER documents can be synced into `policy_documents` as:

- `meeting_note`
- `party_rule`

## Environment

Add these environment variables locally:

```env
AMARANTH_BASE_URL=http://gw.reformparty.kr
AMARANTH_COMPANY_CODE=reformparty
AMARANTH_LOGIN_ID=...
AMARANTH_LOGIN_PASSWORD=...
AMARANTH_HEADLESS=0
AMARANTH_LIMIT=20
AMARANTH_STORAGE_STATE=C:\policy\data\amaranth-storage-state.json
AMARANTH_MEETINGS_FOLDER=최고위원회의
AMARANTH_RULES_FOLDER=당헌당규
AMARANTH_OWNER_NAME=개혁신당
```

## Install

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
```

## Dry Run

Meetings:

```powershell
.venv\Scripts\python.exe scripts\sync_amaranth_meetings.py --kind meetings --dry-run
```

Rules:

```powershell
.venv\Scripts\python.exe scripts\sync_amaranth_meetings.py --kind rules --dry-run
```

## Real Sync

Meetings:

```powershell
.venv\Scripts\python.exe scripts\sync_amaranth_meetings.py --kind meetings
```

Rules:

```powershell
.venv\Scripts\python.exe scripts\sync_amaranth_meetings.py --kind rules
```

## Combined SSOT Sync

```powershell
.venv\Scripts\python.exe scripts\run_ssot_sync.py --amaranth-meetings --amaranth-rules
```

## Notes

- The current collector assumes the path:
  - `ONECHAMBER`
  - `?꾩궗臾몄꽌??
  - `媛쒗쁺?좊떦`
  - target folder from env
- The current implementation is a first pass. It still needs:
  - stable list-row selectors from the actual document list
  - detail-page field extraction tuning
  - duplicate detection based on real document number if available

