// Vercel Serverless Function — Dashboard data
// Calls the proven /api/envy endpoint internally + adds price/RSI
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Cache-Control', 's-maxage=300, stale-while-revalidate=600');

  const API_KEY = process.env.ENVY_API_KEY;
  if (!API_KEY) return res.status(500).json({ error: 'ENVY_API_KEY not configured' });

  const coins = 'BTC,ETH,SOL,DOGE,AVAX,LINK,ARB,NEAR,SUI,INJ';

  try {
    // Single call — exact same as /api/envy
    const response = await fetch(
      `https://gate.getzero.dev/api/claw/paid/indicators/snapshot?coins=${coins}&indicators=HURST_24H,DFA_24H,LYAPUNOV_24H,XONE_AVG_NET,ROC_3H,CLOSE_PRICE_15M,RSI_3H30M`,
      { headers: { 'X-API-Key': API_KEY } }
    );

    if (!response.ok) {
      // Fallback: try with just 5 indicators like envy.js
      const r2 = await fetch(
        `https://gate.getzero.dev/api/claw/paid/indicators/snapshot?coins=${coins}&indicators=HURST_24H,DFA_24H,LYAPUNOV_24H,XONE_AVG_NET,ROC_3H`,
        { headers: { 'X-API-Key': API_KEY } }
      );
      const d2 = await r2.json();
      if (!d2.snapshot) return res.status(200).json({ live: false, error: 'API unavailable', coins: {} });
      return res.status(200).json(buildResponse(d2.snapshot));
    }

    const data = await response.json();
    if (!data.snapshot) {
      // Fallback: try with just 5 indicators
      const r2 = await fetch(
        `https://gate.getzero.dev/api/claw/paid/indicators/snapshot?coins=${coins}&indicators=HURST_24H,DFA_24H,LYAPUNOV_24H,XONE_AVG_NET,ROC_3H`,
        { headers: { 'X-API-Key': API_KEY } }
      );
      const d2 = await r2.json();
      if (!d2.snapshot) return res.status(200).json({ live: false, error: data.error || 'No data', coins: {} });
      return res.status(200).json(buildResponse(d2.snapshot));
    }

    return res.status(200).json(buildResponse(data.snapshot));
  } catch (err) {
    return res.status(500).json({ live: false, error: err.message, coins: {} });
  }
}

function buildResponse(snapshot) {
  const result = {};
  for (const [coin, indList] of Object.entries(snapshot)) {
    if (!Array.isArray(indList)) continue;
    const row = {};
    for (const ind of indList) { row[ind.indicatorCode] = ind.value; }

    const h = row.HURST_24H, d = row.DFA_24H, ly = row.LYAPUNOV_24H;
    let regime = 'unknown', confidence = 0;
    if (h != null && d != null && ly != null) {
      const lyN = Math.min(ly, 2) / 2;
      if (lyN > 0.425) { regime = 'chaotic'; confidence = Math.min(lyN / 0.5, 1); }
      else if (h < 0.45 && d < 0.45) { regime = 'reverting'; confidence = (0.45 - Math.max(h, d)) / 0.15; }
      else if (h > 0.55 && d > 0.55) { regime = 'trending'; confidence = (Math.min(h, d) - 0.55) / 0.15; }
      else if ((h < 0.45 && d > 0.55) || (h > 0.55 && d < 0.45)) { regime = 'shift'; confidence = Math.abs(h - d) / 0.3; }
      else { regime = 'neutral'; confidence = 0.3; }
      confidence = parseFloat(Math.min(Math.max(confidence, 0), 1).toFixed(2));
    }
    row._regime = regime;
    row._confidence = confidence;
    result[coin] = row;
  }

  return {
    live: Object.keys(result).length > 0,
    timestamp: new Date().toISOString(),
    coinCount: Object.keys(result).length,
    coins: result
  };
}
