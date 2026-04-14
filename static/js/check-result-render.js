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

  // 평가 축 정의 (max 포함)
  var AXES = [
    { key: 'platformFitScore', label: '정강정책 정합성', max: 20, keywords: ['정강정책', '정합성'] },
    { key: 'designScore',      label: '정책 설계 완성도', max: 30, keywords: ['정책', '설계', '완성도'] },
    { key: 'feasibilityScore', label: '실현 가능성',      max: 20, keywords: ['실현', '가능성'] },
    { key: 'specificityScore', label: '구체성',            max: 15, keywords: ['구체성'] },
    { key: 'messageScore',     label: '전달력',            max: 15, keywords: ['전달력'] }
  ];

  function parseScoresFromText(text) {
    var lines = String(text || '').split(/\r?\n/).map(function(s) { return s.trim(); });
    var pickNum = function(line) {
      if (!line) return null;
      var m = line.match(/(\d+(?:\.\d+)?)/);
      return m ? Number(m[1]) : null;
    };
    var pickAxisScore = function(line) {
      if (!line) return null;
      var m1 = line.match(/\((\d+(?:\.\d+)?)점\)/);
      if (m1) return Number(m1[1]);
      var m2 = line.match(/\(\d+-\d+\)\s*:\s*(\d+(?:\.\d+)?)/);
      if (m2) return Number(m2[1]);
      var m3 = line.match(/:\s*(\d+(?:\.\d+)?)(?:\s|$)/);
      if (m3) return Number(m3[1]);
      return null;
    };
    var findAxisScore = function(keywords) {
      var candidates = lines.filter(function(l) { return keywords.every(function(k) { return l.indexOf(k) !== -1; }); });
      for (var i = 0; i < candidates.length; i++) {
        var v = pickAxisScore(candidates[i]);
        if (v != null) return v;
      }
      return null;
    };
    var findByKeywords = function(keywords) {
      var line = lines.find(function(l) { return keywords.every(function(k) { return l.indexOf(k) !== -1; }); });
      return pickNum(line);
    };
    var result = { totalScore: findByKeywords(['결과', '종합', '점수']) };
    if (result.totalScore == null) result.totalScore = findByKeywords(['종합', '점수']);
    AXES.forEach(function(ax) {
      result[ax.key] = findAxisScore(ax.keywords);
    });
    return result;
  }

  // 총평 섹션 파싱 → 테이블 데이터
  function parseSummaryTable(bodyLines) {
    var rows = [];
    var current = null;
    var re = new RegExp('(' + AXES.map(function(a) { return a.label; }).join('|') + ')');
    bodyLines.forEach(function(line) {
      var t = line.trim();
      if (!t) return;
      var m = t.match(re);
      if (m) {
        if (current) rows.push(current);
        var ax = AXES.find(function(a) { return t.indexOf(a.label) !== -1; });
        var scoreM = t.match(/(\d+(?:\.\d+)?)\s*[점\/]/);
        current = { label: ax ? ax.label : m[1], max: ax ? ax.max : null, score: scoreM ? Number(scoreM[1]) : null, strength: '', supplement: '' };
        return;
      }
      if (current) {
        if (t.indexOf('강점:') === 0 || t.indexOf('강점 :') === 0) {
          current.strength = t.replace(/^강점\s*:\s*/, '');
        } else if (t.indexOf('보완 핵심:') === 0 || t.indexOf('보완핵심:') === 0 || t.indexOf('보완 핵심 :') === 0) {
          current.supplement = t.replace(/^보완\s*핵심\s*:\s*/, '');
        } else if (current.strength && !t.match(re)) {
          // 이어지는 텍스트
          if (!current.supplement) current.strength += ' ' + t;
        }
      }
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

    // 축별 점수 카드 (정규화된 % 기준으로 색상)
    const hasScores = AXES.some(function(ax) { return scores[ax.key] != null; });
    if (hasScores) {
      const cards = AXES.filter(function(ax) { return scores[ax.key] != null; }).map(function(ax) {
        const v = scores[ax.key];
        const pct = v / ax.max;
        const cls = pct >= 0.8 ? 'good' : pct >= 0.6 ? 'mid' : 'low';
        return '<div class="score-card ' + cls + '"><span class="score-card-label">' + escapeHtml(ax.label) + '</span><span class="score-card-value">' + v + '<small>/' + ax.max + '</small></span></div>';
      });
      if (cards.length) html += '<div class="score-cards-row">' + cards.join('') + '</div>';
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
