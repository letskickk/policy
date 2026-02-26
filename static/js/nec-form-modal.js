/**
 * 선관위 제출용 선거공약서 모달
 * showNecFormModal(pledgeText, meta, options) 호출 시 GPT 변환 후 편집 가능한 모달 표시.
 * meta: { candidateName, electionType, regionName, districtName }
 * options: { mode: 'extract' | 'generate', resultText: '' }
 */

const ELECTION_TYPE_LABELS = {
  metro_mayor: '광역단체장선거',
  local_mayor: '기초단체장선거',
  regional_council: '광역의원선거',
  local_council: '기초의원선거',
};

function _necInjectStyles() {
  if (document.getElementById('nec-form-style')) return;
  const style = document.createElement('style');
  style.id = 'nec-form-style';
  style.textContent = `
/* ── 모달 오버레이 ── */
#necFormOverlay {
  position: fixed; inset: 0;
  background: rgba(0,0,0,.55);
  z-index: 9000;
  display: flex; align-items: flex-start; justify-content: center;
  padding: 24px 16px;
  overflow-y: auto;
}
#necFormModal {
  background: #fff;
  border-radius: 8px;
  width: 100%; max-width: 860px;
  box-shadow: 0 8px 32px rgba(0,0,0,.25);
  display: flex; flex-direction: column;
}

/* ── 모달 헤더 (화면 전용) ── */
.nec-modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 24px;
  border-bottom: 1px solid #e5e7eb;
}
.nec-modal-header h2 {
  font-size: 1.1rem; font-weight: 600; margin: 0;
  color: #111827;
}
.nec-modal-header button {
  background: none; border: none; cursor: pointer;
  font-size: 1.4rem; color: #6b7280; padding: 0 4px;
  line-height: 1;
}
.nec-modal-header button:hover { color: #111827; }

/* ── 로딩 ── */
.nec-loading {
  display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 12px;
  padding: 60px 24px; color: #6b7280; font-size: .95rem;
}
.nec-loading-spinner {
  width: 36px; height: 36px;
  border: 3px solid #e5e7eb;
  border-top-color: #2563eb;
  border-radius: 50%;
  animation: necSpin .8s linear infinite;
}
@keyframes necSpin { to { transform: rotate(360deg); } }

/* ── 공약서 본문 ── */
.nec-body {
  padding: 24px 32px;
  font-family: 'Malgun Gothic', '맑은 고딕', sans-serif;
  font-size: .93rem;
  line-height: 1.7;
  color: #111827;
}

/* 화면용 문서 헤더 (스크롤 시 1회만 표시) */
.nec-doc-header {
  text-align: center;
  margin-bottom: 20px;
}
.nec-doc-title {
  font-size: 1.6rem;
  font-weight: 700;
  letter-spacing: .15em;
  margin-bottom: 12px;
}
.nec-doc-meta {
  display: grid;
  grid-template-columns: auto 1fr auto 1fr;
  gap: 6px 12px;
  font-size: .88rem;
  text-align: left;
  max-width: 600px;
  margin: 0 auto;
  border: 1px solid #d1d5db;
  padding: 10px 16px;
  border-radius: 4px;
  background: #f9fafb;
  align-items: center;
}
.nec-meta-label {
  color: #6b7280; white-space: nowrap;
  font-weight: 500;
}
.nec-meta-value[contenteditable] {
  outline: none; border-bottom: 1px dashed #93c5fd;
  min-width: 60px; color: #1e3a5f;
  cursor: text;
}
.nec-meta-value[contenteditable]:focus {
  background: #eff6ff;
}
/* 소속정당은 전체 너비 */
.nec-meta-full {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 12px;
  align-items: center;
}

/* 구분선 */
.nec-divider {
  border: none; border-top: 2px solid #111827;
  margin: 20px 0 0;
}

/* 각 공약 페이지 */
.nec-pledge-page {
  padding: 20px 0;
  border-bottom: 1px solid #d1d5db;
  position: relative;
  overflow: hidden;
}
.nec-pledge-page:last-child { border-bottom: none; }

/* AI 참고용 워터마크 */
.nec-pledge-page::after {
  content: 'AI를 활용하여 작성된 자료이며 참고 용도로만 활용하시기 바랍니다';
  position: absolute;
  top: 50%; left: 50%;
  transform: translate(-50%, -50%) rotate(-35deg);
  font-size: 1.3rem;
  color: rgba(0, 0, 0, 0.06);
  white-space: nowrap;
  pointer-events: none;
  z-index: 0;
  font-weight: 700;
  letter-spacing: 0.05em;
  user-select: none;
}

/* 인쇄용 페이지별 헤더 — 화면에서는 숨김 */
.nec-page-header { display: none; }

.nec-pledge-rank {
  font-size: .82rem; color: #6b7280; margin-bottom: 4px;
}
.nec-pledge-title {
  font-size: 1.15rem; font-weight: 700;
  color: #1e3a5f; margin-bottom: 14px;
  border-bottom: 1px solid #e5e7eb; padding-bottom: 6px;
  outline: none; cursor: text;
}
.nec-pledge-title:focus { background: #eff6ff; }

/* 공약 내용 항목 (‣ bullet, 레이블 없음) */
.nec-content-list {
  list-style: none; padding: 0; margin: 0 0 10px 4px;
}
.nec-content-list li {
  display: flex; align-items: flex-start; gap: 6px;
  margin-bottom: 3px;
}
.nec-content-list li::before {
  content: '‣';
  flex-shrink: 0; color: #374151; margin-top: 1px;
  font-size: 1.05rem;
}

/* 섹션 블록 (목표/이행방법 등 □ 레이블 있음) */
.nec-section { margin-bottom: 10px; }
.nec-section-label {
  font-weight: 700; margin-bottom: 4px;
  display: flex; align-items: baseline; gap: 6px;
}
.nec-section-label::before {
  content: '□';
  font-size: 1rem; flex-shrink: 0;
}
.nec-items-list {
  list-style: none; padding: 0; margin: 0 0 0 22px;
}
.nec-items-list li {
  display: flex; align-items: flex-start; gap: 6px;
  margin-bottom: 3px;
}
.nec-items-list li::before {
  content: '○';
  flex-shrink: 0; color: #374151; margin-top: 1px;
}
.nec-item[contenteditable] {
  flex: 1; outline: none;
  border-bottom: 1px dashed #93c5fd;
  cursor: text; min-width: 40px;
}
.nec-item[contenteditable]:focus { background: #eff6ff; }

/* 추가/삭제 버튼 */
.nec-add-item {
  margin-left: 22px; margin-top: 4px;
  background: none; border: 1px dashed #93c5fd;
  color: #3b82f6; font-size: .78rem;
  padding: 1px 8px; border-radius: 4px;
  cursor: pointer;
}
.nec-add-item:hover { background: #eff6ff; }
.nec-content-add {
  margin-left: 4px;
}
.nec-item-del {
  background: none; border: none;
  color: #d1d5db; font-size: .85rem;
  cursor: pointer; padding: 0 2px; flex-shrink: 0;
  line-height: 1;
}
.nec-item-del:hover { color: #ef4444; }

/* 모달 하단 액션 버튼 */
.nec-modal-footer {
  padding: 16px 24px;
  border-top: 1px solid #e5e7eb;
  display: flex; justify-content: center; gap: 12px;
}
.nec-btn-print {
  background: #1d4ed8; color: #fff;
  border: none; border-radius: 6px;
  padding: 10px 28px; font-size: .95rem;
  font-weight: 600; cursor: pointer;
}
.nec-btn-print:hover { background: #1e40af; }
.nec-btn-close2 {
  background: #f3f4f6; color: #374151;
  border: 1px solid #d1d5db; border-radius: 6px;
  padding: 10px 24px; font-size: .95rem;
  cursor: pointer;
}
.nec-btn-close2:hover { background: #e5e7eb; }

/* ── 인쇄 전용 스타일 ── */
@media print {
  body > *:not(#necFormOverlay) { display: none !important; }
  #necFormOverlay {
    position: static; background: none;
    padding: 0; display: block;
  }
  #necFormModal {
    box-shadow: none; border-radius: 0;
    width: 100%; max-width: 100%;
  }
  .nec-modal-header,
  .nec-modal-footer { display: none !important; }
  .nec-body { padding: 0; font-size: 10pt; }
  /* 화면용 문서 헤더 숨김 (각 페이지 헤더로 대체) */
  .nec-doc-header { display: none !important; }
  .nec-divider { display: none !important; }
  /* 각 공약 페이지별 헤더 표시 */
  .nec-page-header {
    display: block !important;
    text-align: center;
    margin-bottom: 14pt;
    padding-bottom: 8pt;
    border-bottom: 1.5pt solid #000;
  }
  .nec-page-header-title {
    font-size: 18pt;
    font-weight: 700;
    letter-spacing: .15em;
    margin-bottom: 6pt;
  }
  .nec-page-header-meta {
    font-size: 9pt;
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 4pt 16pt;
  }
  .nec-page-header-meta span { white-space: nowrap; }
  .nec-pledge-page {
    page-break-after: always;
    border-bottom: none;
    padding: 0;
  }
  .nec-pledge-page::after {
    color: rgba(0, 0, 0, 0.08) !important;
    font-size: 14pt;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  .nec-pledge-page:last-child { page-break-after: avoid; }
  .nec-add-item, .nec-item-del { display: none !important; }
  [contenteditable] {
    border-bottom: none !important;
    background: none !important;
  }
  @page { margin: 20mm 18mm; size: A4 portrait; }
}
`;
  document.head.appendChild(style);
}

function _necBuildSection(label, items, sectionKey) {
  const sec = document.createElement('div');
  sec.className = 'nec-section';

  const labelEl = document.createElement('div');
  labelEl.className = 'nec-section-label';
  labelEl.textContent = label;
  sec.appendChild(labelEl);

  const ul = document.createElement('ul');
  ul.className = 'nec-items-list';
  ul.dataset.section = sectionKey;

  (items || []).forEach(text => {
    ul.appendChild(_necMakeItem(text, '○'));
  });

  sec.appendChild(ul);

  const addBtn = document.createElement('button');
  addBtn.className = 'nec-add-item';
  addBtn.textContent = '+ 항목 추가';
  addBtn.addEventListener('click', () => {
    ul.appendChild(_necMakeItem('', '○'));
    const last = ul.lastElementChild.querySelector('.nec-item');
    if (last) last.focus();
  });
  sec.appendChild(addBtn);

  return sec;
}

/** 공약 내용 섹션 — ‣ bullet, 레이블 없음 (실제 선거공약서 양식) */
function _necBuildContentSection(items) {
  const wrap = document.createElement('div');

  const ul = document.createElement('ul');
  ul.className = 'nec-content-list';
  ul.dataset.section = '내용';

  (items || []).forEach(text => {
    ul.appendChild(_necMakeItem(text, '‣'));
  });

  wrap.appendChild(ul);

  const addBtn = document.createElement('button');
  addBtn.className = 'nec-add-item nec-content-add';
  addBtn.textContent = '+ 항목 추가';
  addBtn.addEventListener('click', () => {
    ul.appendChild(_necMakeItem('', '‣'));
    const last = ul.lastElementChild.querySelector('.nec-item');
    if (last) last.focus();
  });
  wrap.appendChild(addBtn);

  return wrap;
}

function _necMakeItem(text, bullet) {
  const li = document.createElement('li');
  const span = document.createElement('span');
  span.className = 'nec-item';
  span.contentEditable = 'true';
  span.textContent = text;
  const del = document.createElement('button');
  del.className = 'nec-item-del';
  del.textContent = '×';
  del.title = '삭제';
  del.addEventListener('click', () => li.remove());
  li.appendChild(span);
  li.appendChild(del);
  return li;
}

/** 인쇄용 페이지별 헤더 빌드 (화면에서는 숨김) */
function _necBuildPageHeader(meta, electionLabel, location) {
  const ph = document.createElement('div');
  ph.className = 'nec-page-header';

  const t = document.createElement('div');
  t.className = 'nec-page-header-title';
  t.textContent = '선  거  공  약  서';
  ph.appendChild(t);

  const m = document.createElement('div');
  m.className = 'nec-page-header-meta';
  const fields = [
    ['선거명', electionLabel],
    ['선거구명', location],
    ['후보자명', meta.candidateName || ''],
    ['기호', meta.candidateNumber || ''],
    ['소속정당명', '개혁신당'],
  ];
  fields.forEach(([k, v]) => {
    const s = document.createElement('span');
    s.textContent = `${k}: ${v}`;
    m.appendChild(s);
  });
  ph.appendChild(m);

  return ph;
}

function _necBuildPledgePage(item, index, meta, electionLabel, location) {
  const page = document.createElement('div');
  page.className = 'nec-pledge-page';

  // 인쇄 시 각 페이지마다 표시되는 헤더
  page.appendChild(_necBuildPageHeader(meta, electionLabel, location));

  const rank = document.createElement('div');
  rank.className = 'nec-pledge-rank';
  rank.textContent = `공약순위: ${item['순위'] || (index + 1)}`;
  page.appendChild(rank);

  const title = document.createElement('div');
  title.className = 'nec-pledge-title';
  title.contentEditable = 'true';
  title.textContent = item['제목'] || '';
  page.appendChild(title);

  // 공약 내용 — ‣ bullet, 레이블 없음
  if ((item['내용'] || []).length > 0) {
    page.appendChild(_necBuildContentSection(item['내용']));
  }

  if ((item['목표'] || []).length > 0)
    page.appendChild(_necBuildSection('목표', item['목표'], '목표'));
  if ((item['이행방법'] || []).length > 0)
    page.appendChild(_necBuildSection('이행방법', item['이행방법'], '이행방법'));
  if ((item['이행기간'] || []).length > 0)
    page.appendChild(_necBuildSection('이행기간', item['이행기간'], '이행기간'));
  if ((item['재원조달방안'] || []).length > 0)
    page.appendChild(_necBuildSection('재원조달방안 등', item['재원조달방안'], '재원조달방안'));

  return page;
}

function _necBuildModal(items, meta) {
  const overlay = document.createElement('div');
  overlay.id = 'necFormOverlay';

  const modal = document.createElement('div');
  modal.id = 'necFormModal';

  // ── 모달 헤더 ──
  const header = document.createElement('div');
  header.className = 'nec-modal-header';
  const h2 = document.createElement('h2');
  h2.textContent = '선거공약서 미리보기 · 편집';
  const closeBtn = document.createElement('button');
  closeBtn.textContent = '×';
  closeBtn.title = '닫기';
  closeBtn.addEventListener('click', () => overlay.remove());
  header.appendChild(h2);
  header.appendChild(closeBtn);
  modal.appendChild(header);

  // ── 공약서 본문 ──
  const body = document.createElement('div');
  body.className = 'nec-body';

  // 화면용 문서 헤더 (1회 표시)
  const docHeader = document.createElement('div');
  docHeader.className = 'nec-doc-header';
  const docTitle = document.createElement('div');
  docTitle.className = 'nec-doc-title';
  docTitle.textContent = '선  거  공  약  서';
  docHeader.appendChild(docTitle);

  const electionLabel = ELECTION_TYPE_LABELS[meta.electionType] || meta.electionType || '';
  const location = [meta.regionName, meta.districtName].filter(Boolean).join(' ');

  // 메타 그리드 (4열: 레이블 값 레이블 값)
  const metaGrid = document.createElement('div');
  metaGrid.className = 'nec-doc-meta';

  // 2열씩 배치: 선거명/선거구, 후보자명/기호
  const row1 = [
    ['선거명', electionLabel],
    ['선거구명', location],
  ];
  const row2 = [
    ['후보자명', meta.candidateName || ''],
    ['기호', ''],
  ];
  [...row1, ...row2].forEach(([label, val]) => {
    const lEl = document.createElement('span');
    lEl.className = 'nec-meta-label';
    lEl.textContent = label;
    const vEl = document.createElement('span');
    vEl.className = 'nec-meta-value';
    vEl.contentEditable = 'true';
    vEl.textContent = val;
    metaGrid.appendChild(lEl);
    metaGrid.appendChild(vEl);
  });

  // 소속정당 — 전체 너비
  const partyWrap = document.createElement('div');
  partyWrap.className = 'nec-meta-full';
  const partyLabel = document.createElement('span');
  partyLabel.className = 'nec-meta-label';
  partyLabel.textContent = '소속정당';
  const partyVal = document.createElement('span');
  partyVal.className = 'nec-meta-value';
  partyVal.contentEditable = 'true';
  partyVal.textContent = '개혁신당';
  partyWrap.appendChild(partyLabel);
  partyWrap.appendChild(partyVal);
  metaGrid.appendChild(partyWrap);

  docHeader.appendChild(metaGrid);
  body.appendChild(docHeader);

  const divider = document.createElement('hr');
  divider.className = 'nec-divider';
  body.appendChild(divider);

  // 공약 페이지들
  items.forEach((item, idx) => {
    body.appendChild(_necBuildPledgePage(item, idx, meta, electionLabel, location));
  });

  modal.appendChild(body);

  // ── 모달 하단 버튼 ──
  const footer = document.createElement('div');
  footer.className = 'nec-modal-footer';

  const printBtn = document.createElement('button');
  printBtn.className = 'nec-btn-print';
  printBtn.textContent = '🖨 인쇄 / PDF 저장';
  printBtn.addEventListener('click', () => window.print());

  const closeBtn2 = document.createElement('button');
  closeBtn2.className = 'nec-btn-close2';
  closeBtn2.textContent = '닫기';
  closeBtn2.addEventListener('click', () => overlay.remove());

  footer.appendChild(printBtn);
  footer.appendChild(closeBtn2);
  modal.appendChild(footer);

  overlay.appendChild(modal);

  overlay.addEventListener('click', e => {
    if (e.target === overlay) overlay.remove();
  });

  return overlay;
}

/**
 * 선거공약서 모달 표시
 * @param {string} pledgeText - 공약 원문 텍스트
 * @param {{ candidateName, electionType, regionName, districtName }} meta
 * @param {{ mode: 'extract'|'generate', resultText: string }} options
 */
async function showNecFormModal(pledgeText, meta = {}, options = {}) {
  _necInjectStyles();

  const mode = options.mode || 'extract';
  const resultText = options.resultText || '';

  // 기존 모달 제거
  const existing = document.getElementById('necFormOverlay');
  if (existing) existing.remove();

  // 로딩 모달 표시
  const loadingOverlay = document.createElement('div');
  loadingOverlay.id = 'necFormOverlay';
  const loadingModal = document.createElement('div');
  loadingModal.id = 'necFormModal';

  const loadingHeader = document.createElement('div');
  loadingHeader.className = 'nec-modal-header';
  const lh2 = document.createElement('h2');
  lh2.textContent = mode === 'generate' ? '선거공약서 최종본 생성 중...' : '선거공약서 생성 중...';
  loadingHeader.appendChild(lh2);
  loadingModal.appendChild(loadingHeader);

  const loadingBody = document.createElement('div');
  loadingBody.className = 'nec-loading';
  const spinner = document.createElement('div');
  spinner.className = 'nec-loading-spinner';
  const msg = document.createElement('span');
  msg.textContent = mode === 'generate'
    ? 'GPT가 수정·보완 제안을 반영하여 선거공약서를 완성하고 있습니다...'
    : 'GPT가 공약을 선관위 양식으로 변환하고 있습니다...';
  loadingBody.appendChild(spinner);
  loadingBody.appendChild(msg);
  loadingModal.appendChild(loadingBody);
  loadingOverlay.appendChild(loadingModal);
  document.body.appendChild(loadingOverlay);

  try {
    const res = await fetch('/api/documents/nec-form', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pledge_text: pledgeText,
        candidate_name: meta.candidateName || '',
        election_type: meta.electionType || '',
        region_name: meta.regionName || '',
        district_name: meta.districtName || '',
        mode: mode,
        result_text: resultText,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `서버 오류 (${res.status})`);
    }

    const data = await res.json();
    const items = data.items || [];

    loadingOverlay.remove();
    const modal = _necBuildModal(items, meta);
    document.body.appendChild(modal);

  } catch (e) {
    loadingOverlay.remove();
    alert(`선거공약서 생성 실패: ${e.message}`);
  }
}
