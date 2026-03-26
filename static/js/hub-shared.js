/* hub-shared.js — hub.html + hub-archive.html 공통 유틸 */

const safe = (v) => String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const number = (v) => Number(v || 0).toLocaleString('ko-KR');

function summarizeText(value, fallback = '') {
  const txt = String(value || '').replace(/\s+/g, ' ').trim();
  if (!txt) return fallback;
  return txt.length > 140 ? txt.slice(0, 140).trim() + '…' : txt;
}

function firstMeaningfulText(item) {
  return summarizeText(
    item?.official_summary || item?.summary || item?.brief?.summary || item?.relevance_note || item?.body || '',
    ''
  );
}

function hubUrl(base, tab, key) {
  const url = new URL(base, location.origin);
  url.searchParams.set('tab', tab);
  url.searchParams.set('key', key);
  return url.pathname + url.search;
}

const DOC_TYPE_LABELS = {
  bill: "법안",
  statement: "논평",
  press_release: "보도자료",
  briefing: "브리핑",
  pledge: "공약",
  policy: "정책 문서",
  meeting_note: "회의록",
  party_rule: "규정",
  poll_result: "여론조사",
  research: "연구 자료",
  other: "문서",
};

const STATUS_LABELS = {
  approved: "확정",
  review: "검토 중",
  draft: "초안",
  active: "공개 중",
  archived: "보관",
  superseded: "대체됨",
};

function docTypeLabel(value) { return DOC_TYPE_LABELS[value] || "문서"; }
function statusLabel(value) { return STATUS_LABELS[value] || value || ""; }
