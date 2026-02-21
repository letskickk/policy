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

  function parseScoresFromText(text) {
    const lines = String(text || '').split(/\r?\n/).map(function(s) { return s.trim(); });
    const pickNum = function(line) {
      if (!line) return null;
      const m = line.match(/(\d+(?:\.\d+)?)/);
      return m ? Number(m[1]) : null;
    };
    const findByKeyword = function() {
      const keywords = Array.prototype.slice.call(arguments);
      const line = lines.find(function(l) { return keywords.every(function(k) { return l.indexOf(k) !== -1; }); });
      return pickNum(line);
    };
    return {
      totalScore: findByKeyword('결과', '종합', '점수') ?? findByKeyword('종합', '점수'),
      platformFitScore: findByKeyword('정강정책', '부합도'),
      designScore: findByKeyword('정책', '설계', '완성도'),
      feasibilityScore: findByKeyword('실행', '가능성'),
      specificityScore: findByKeyword('구체성'),
      messageScore: findByKeyword('메시지', '경쟁력')
    };
  }

  function weightedTotalFromAxes(scores) {
    const axes = [
      { v: scores.platformFitScore, w: 0.30 },
      { v: scores.designScore, w: 0.25 },
      { v: scores.feasibilityScore, w: 0.20 },
      { v: scores.specificityScore, w: 0.15 },
      { v: scores.messageScore, w: 0.10 }
    ].filter(function(x) { return x.v !== null && x.v !== undefined; });
    if (!axes.length) return null;
    const wSum = axes.reduce(function(a, b) { return a + b.w; }, 0);
    const vSum = axes.reduce(function(a, b) { return a + (b.v * b.w); }, 0);
    return Math.round((vSum / wSum) * 10) / 10;
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
    if (totalScore == null) totalScore = weightedTotalFromAxes(scores);
    const signal = totalScore != null ? (totalScore >= 80 ? 'green' : (totalScore >= 60 ? 'yellow' : 'red')) : 'red';
    const signalLabel = signal === 'green' ? '양호' : (signal === 'yellow' ? '보완 권고' : '보완 필요');

    let html = '';
    if (totalScore != null) {
      html += '<div class="score-board"><div class="score">총점: ' + totalScore.toFixed(1) + '점</div><span class="badge ' + signal + '">' + signalLabel + '</span></div>';
    }
    const hasScores = [scores.platformFitScore, scores.designScore, scores.feasibilityScore, scores.specificityScore, scores.messageScore].some(function(v) { return v != null; });
    if (hasScores) {
      const items = [
        { label: '정강정책 부합도', value: scores.platformFitScore },
        { label: '정책 설계 완성도', value: scores.designScore },
        { label: '실행 가능성', value: scores.feasibilityScore },
        { label: '구체성', value: scores.specificityScore },
        { label: '메시지 경쟁력', value: scores.messageScore }
      ];
      const cards = items.filter(function(item) { return item.value != null; }).map(function(item) {
        const cls = item.value >= 80 ? 'good' : (item.value >= 60 ? 'mid' : 'low');
        return '<div class="score-card ' + cls + '"><span class="score-card-label">' + escapeHtml(item.label) + '</span><span class="score-card-value">' + item.value.toFixed(0) + '</span></div>';
      });
      if (cards.length) html += '<div class="score-cards-row">' + cards.join('') + '</div>';
    }

    const sections = splitSections(text);
    if (sections.length > 1 || (sections[0] && sections[0].title !== '요약')) {
      html += '<div class="section-cards">';
      sections.forEach(function(sec, idx) {
        const bodyLines = (sec.body || []).join('\n').split('\n');
        let bodyHtml = '';
        for (var i = 0; i < bodyLines.length; i++) {
          var line = bodyLines[i];
          var trimmed = line.trim();
          if (!trimmed) continue;
          var isItem = /^[-·•]/.test(trimmed);
          bodyHtml += '<div class="section-line' + (isItem ? ' item' : '') + '">' + escapeHtml(line) + '</div>';
        }
        bodyHtml = bodyHtml || '-';
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
