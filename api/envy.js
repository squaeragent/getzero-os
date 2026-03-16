// Vercel Serverless Function — proxies Envy Matrix API
// Accepts optional ?coins= and ?indicators= query params
// Caches for 60 seconds for live feel
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Cache-Control', 's-maxage=60, stale-while-revalidate=120');

  const API_KEY = process.env.ENVY_API_KEY;
  if (!API_KEY) {
    return res.status(500).json({ error: 'ENVY_API_KEY not configured' });
  }

  const coins = req.query.coins || 'BTC,ETH,SOL,DOGE,AVAX,LINK,ARB,NEAR,SUI,INJ';
  const indicators = req.query.indicators || 'HURST_24H,DFA_24H,LYAPUNOV_24H,XONE_AVG_NET,ROC_3H';

  // Use gate.getzero.dev (CNAME → nvprotocol.com → 167.172.7.178)
  const BASE = 'https://gate.getzero.dev';
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    const response = await fetch(
      `${BASE}/api/claw/paid/indicators/snapshot?coins=${coins}&indicators=${indicators}`,
      { headers: { 'X-API-Key': API_KEY }, signal: controller.signal }
    );
    clearTimeout(timeout);

    if (!response.ok) {
      const text = await response.text();
      return res.status(response.status).json({ error: 'Envy API error', detail: text });
    }

    const data = await response.json();

    // Build lean matrix format for homepage canvas
    const matrix = {};
    for (const [coin, indList] of Object.entries(data.snapshot || {})) {
      const row = {};
      if (Array.isArray(indList)) {
        for (const ind of indList) {
          row[ind.indicatorCode] = ind.value;
        }
      }
      matrix[coin] = row;
    }

    return res.status(200).json({
      live: true,
      timestamp: new Date().toISOString(),
      matrix,           // lean format for homepage
      snapshot: data.snapshot || {}  // raw format for portfolio
    });
  } catch (err) {
    return res.status(500).json({ error: 'Failed to fetch Envy data', detail: err.message });
  }
}
