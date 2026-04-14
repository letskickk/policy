/**
 * 공통: 당 부합 점검 결과 파싱 및 HTML 렌더링.
 * pledge.html, dashboard(기록 상세)에서 동일한 구조로 표시할 때 사용.
 */
(function(global) {
  function escapeHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function normalizeOutputText(text) {
    const s = String(text || '');
    return s.replace(/\r\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  }

  function splitSections(text) {
    const lines = String(text || '').split(/\r?\n/);
    const sections = [];
    let current = null;
    for (const raw of lines) {
      const line = raw || '';
      const m = line.match(/^\s*(?:[#>*-]\s*)*(?:\*\*|__)?\s*(\d+[.)]\s*.+?)(?:\*\*|__)?\s*$/);
      if (m) {
        if (current) sections.push(current);
        current = { title: m[1].trim(), body: [] };
        continue;
      }
      if (!current) current = { title: '요약', body: [] };
      current.body.push(line);
    }
    if (current) sections.push(current);
    return sections.filter(function(sec) { return (sec.body.join('\n').trim() || sec.title); });
  }

  // 평가 축 정의 (총평 카드 파싱용)
  var AXIS_LABELS = ['정강정책 정합성', '정책 설계 완성도', '실현 가능성', '구체성', '전달력'];
  var AXIS_MAX    = { '정강정책 정합성': 20, '정책 설계 완성도': 30, '실현 가능성': 20, '구체성': 15, '전달력': 15 };

  function parseScoresFromText(text) {
    var lines = String(text || '').split(/\r?\n/).map(function(s) { return s.trim(); });
    var pickNum = function(line) {
      if (!line) return null;
      var m = line.match(/(\d+(?:\.\d+)?)/);
      return m ? Number(m[1]) : null;
    };
    var findByKeywords = function(keywords) {
      var line = lines.find(function(l) { return keywords.every(function(k) { return l.indexOf(k) !== -1; }); });
      return pickNum(line);
    };
    return { totalScore: findByKeywords(['결과', '종합', '점수']) || findByKeywords(['종합', '점수']) };
  }

  // 총평 섹션 파싱 → 카드 데이터
  var summaryAxeRe = new RegExp('^(' + AXIS_LABELS.join('|') + ')');

  function pickScore(t) {
    var m1 = t.match(/\((\d+(?:\.\d+)?)점\)/);
    if (m1) return Number(m1[1]);
    var m2 = t.match(/\(\d+-\d+\)\s*:\s*(\d+(?:\.\d+)?)/);
    if (m2) return Number(m2[1]);
    var m3 = t.match(/:\s*(\d+(?:\.\d+)?)(?:\s|$)/);
    if (m3) return Number(m3[1]);
    return null;
  }

  function parseSummaryTable(bodyLines) {
    var rows = [];
    var current = null;
    var mode = null;
    bodyLines.forEach(function(line) {
      var t = line.trim();
      if (!t) return;
      var m = t.match(summaryAxeRe);
      if (m) {
        if (current) rows.push(current);
        var axLabel = AXIS_LABELS.find(function(a) { return t.indexOf(a) === 0; }) || m[1];
        current = { label: axLabel, max: AXIS_MAX[axLabel] || null, score: pickScore(t), strength: '', supplement: '' };
        mode = null;
        return;
      }
      if (!current) return;
      if (/^강점\s*:/.test(t))              { mode = 'strength';   current.strength   = t.replace(/^강점\s*:\s*/, ''); return; }
      if (/^보완\s*(?:핵심\s*)?:/.test(t))  { mode = 'supplement'; current.supplement = t.replace(/^보완\s*(?:핵심\s*)?:\s*/, ''); return; }
      if (/^종합/.test(t)) { mode = null; return; }
      if (mode === 'strength')   { current.strength   += ' ' + t; return; }
      if (mode === 'supplement') { current.supplement += ' ' + t; return; }
    });
    if (current) rows.push(current);
    return rows;
  }

  function buildSummaryTableHtml(sec) {
    var bodyLines = (sec.body || []).join('\n').split('\n');
    var rows = parseSummaryTable(bodyLines);

    // 종합 점수 / 등급 줄 추출
    var totalLine = bodyLines.find(function(l) { return l.indexOf('종합 점수') !== -1; }) || '';
    var gradeLine = bodyLines.find(function(l) { return l.indexOf('종합해석 등급') !== -1 || l.indexOf('종합 등급') !== -1; }) || '';
    var totalM = totalLine.match(/(\d+(?:\.\d+)?)/);
    var gradeM = gradeLine.match(/:\s*(.+)$/);
    var totalScore = totalM ? Number(totalM[1]) : null;
    var grade = gradeM ? gradeM[1].trim() : null;

    if (!rows.length) {
      // 파싱 실패 시 텍스트 폴백
      var fallback = '';
      bodyLines.forEach(function(line) {
        var t = line.trim();
        if (!t) return;
        fallback += '<div class="section-line">' + escapeHtml(line) + '</div>';
      });
      return fallback;
    }

    var html = '<div class="summary-cards">';
    rows.forEach(function(row) {
      var pct = (row.score != null && row.max) ? row.score / row.max : null;
      var cls = pct == null ? '' : (pct >= 0.8 ? 'good' : pct >= 0.6 ? 'mid' : 'low');
      var scoreStr = row.score != null ? (row.score + (row.max ? '/' + row.max : '')) : '-';
      html += '<div class="summary-card">';
      html += '<div class="summary-card-head"><span class="summary-axis">' + escapeHtml(row.label) + '</span><span class="summary-score ' + cls + '">' + escapeHtml(scoreStr) + '</span></div>';
      html += '<div class="summary-card-body">';
      if (row.strength && row.strength !== '-') html += '<div class="summary-row"><span class="summary-row-label strength">강점</span><span class="summary-row-text">' + escapeHtml(row.strength) + '</span></div>';
      if (row.supplement && row.supplement !== '-') html += '<div class="summary-row"><span class="summary-row-label supplement">보완</span><span class="summary-row-text">' + escapeHtml(row.supplement) + '</span></div>';
      html += '</div></div>';
    });
    html += '</div>';

    if (totalScore != null || grade) {
      var sig = totalScore != null ? (totalScore >= 80 ? 'green' : totalScore >= 60 ? 'yellow' : 'red') : '';
      html += '<div class="summary-footer">';
      if (totalScore != null) html += '<span class="summary-total">종합 점수: ' + totalScore + '점</span>';
      if (grade) html += '<span class="summary-grade badge ' + sig + '">' + escapeHtml(grade) + '</span>';
      html += '</div>';
    }
    return html;
  }

  function isVerifyStyleJson(text) {
    const s = String(text || '');
    const head = s.trim().slice(0, 5000);
    if (!head.length) return false;
    return (head.indexOf('fit_score') !== -1 && head.indexOf('rubric') !== -1) || (head.indexOf('"breakdown"') !== -1 && head.indexOf('fit_score') !== -1);
  }

  function buildResultHtml(fullText) {
    if (isVerifyStyleJson(fullText)) {
      return '<div class="analysis-text" style="color:var(--muted, #94a3b8);">이 결과는 이전 형식의 데이터입니다. 점검을 다시 실행해 주세요.</div>';
    }
    const normalized = normalizeOutputText(fullText || '');
    const text = normalized || '';
    const scores = parseScoresFromText(text);
    let totalScore = scores.totalScore;
    const signal = totalScore != null ? (totalScore >= 80 ? 'green' : (totalScore >= 60 ? 'yellow' : 'red')) : 'red';
    const signalLabel = signal === 'green' ? '양호' : (signal === 'yellow' ? '보완 권고' : '보완 필요');

    let html = '';
    if (totalScore != null) {
      html += '<div class="score-board"><div class="score">총점: ' + totalScore.toFixed(1) + '점</div><span class="badge ' + signal + '">' + signalLabel + '</span></div>';
    }

    const sections = splitSections(text);
    if (sections.length > 1 || (sections[0] && sections[0].title !== '요약')) {
      html += '<div class="section-cards">';
      sections.forEach(function(sec, idx) {
        const isSummary = sec.title && (sec.title.indexOf('총평') !== -1);
        let bodyHtml = '';
        if (isSummary) {
          bodyHtml = buildSummaryTableHtml(sec);
        } else {
          const bodyLines = (sec.body || []).join('\n').split('\n');
          for (var i = 0; i < bodyLines.length; i++) {
            var line = bodyLines[i];
            var trimmed = line.trim();
            if (!trimmed) continue;
            var isItem = /^[-·•]/.test(trimmed);
            bodyHtml += '<div class="section-line' + (isItem ? ' item' : '') + '">' + escapeHtml(line) + '</div>';
          }
          bodyHtml = bodyHtml || '-';
        }
        html += '<section class="section-card">' +
          '<div class="section-card-head"><div class="section-card-title">' + escapeHtml(sec.title || '섹션') + '</div><span class="line-tag">' + (idx + 1) + '</span></div>' +
          '<div class="section-card-body">' + bodyHtml + '</div></section>';
      });
      html += '</div>';
    } else {
      html += '<div class="analysis-text">' + escapeHtml(text).replace(/\n{2,}/g, '\n').replace(/\n/g, '<br>') + '</div>';
    }
    return html;
  }

  global.CheckResultRender = {
    escapeHtml: escapeHtml,
    normalizeOutputText: normalizeOutputText,
    splitSections: splitSections,
    parseScoresFromText: parseScoresFromText,
    isVerifyStyleJson: isVerifyStyleJson,
    buildResultHtml: buildResultHtml
  };
})(typeof window !== 'undefined' ? window : this);
