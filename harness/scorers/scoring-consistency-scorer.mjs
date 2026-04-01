function extractScores(output) {
  const axes = [
    '정강정책 정합성',
    '정책 설계 완성도',
    '실현 가능성',
    '구체성',
    '전달력',
  ];

  const values = [];
  for (const axis of axes) {
    const match = output.match(new RegExp(`${axis}\\s*[\\(（]?\\s*(\\d{1,2})`, 'u'));
    if (!match) {
      return { ok: false, reason: `Missing axis score: ${axis}` };
    }
    values.push(Number(match[1]));
  }

  const totalMatch =
    output.match(/총점\s*[:：]?\s*(\d{1,3})/u) ||
    output.match(/종합\s*점수\s*[:：]?\s*(\d{1,3})/u) ||
    output.match(/종합점수\s*[:：]?\s*(\d{1,3})/u) ||
    output.match(/결과:\s*종합 점수\((\d{1,3})점\)/u);

  if (!totalMatch) {
    return { ok: false, reason: 'Missing total score' };
  }

  return {
    ok: true,
    axisScores: values,
    axisSum: values.reduce((sum, value) => sum + value, 0),
    reportedTotal: Number(totalMatch[1]),
  };
}

export default function scoringConsistencyScorer(output) {
  const parsed = extractScores(String(output || ''));
  if (!parsed.ok) {
    return { pass: false, score: 0, reason: parsed.reason };
  }

  const delta = Math.abs(parsed.axisSum - parsed.reportedTotal);
  return delta <= 3
    ? {
        pass: true,
        score: 1,
        reason: `Axis sum ${parsed.axisSum}, total ${parsed.reportedTotal}, delta ${delta}`,
      }
    : {
        pass: false,
        score: 0,
        reason: `Axis sum ${parsed.axisSum}, total ${parsed.reportedTotal}, delta ${delta}`,
      };
}
