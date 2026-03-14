// Vercel Serverless Function — proxies Envy Matrix API
// Caches for 5 minutes to avoid hammering the API
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Cache-Control', 's-maxage=300, stale-while-revalidate=600');

  const API_KEY = process.env.ENVY_API_KEY;
  if (!API_KEY) {
    return res.status(500).json({ error: 'ENVY_API_KEY not configured' });
  }

  const coins = 'BTC,ETH,SOL,DOGE,AVAX,LINK,ARB,NEAR,SUI,INJ';
  const indicators = 'HURST_24H,DFA_24H,LYAPUNOV_24H,XONE_AVG_NET,ROC_3H';

  try {
    const response = await fetch(
      `https://gate.getzero.dev/api/claw/paid/indicators/snapshot?coins=${coins}&indicators=${indicators}`,
      { headers: { 'X-API-Key': API_KEY } }
    );

    if (!response.ok) {
      const text = await response.text();
      return res.status(response.status).json({ error: 'Envy API error', detail: text });
    }

    const data = await response.json();
    
    // Transform to lean format for the canvas
    const matrix = {};
    for (const [coin, indicators] of Object.entries(data.snapshot || {})) {
      const row = {};
      for (const ind of indicators) {
        row[ind.indicatorCode] = ind.value;
      }
      matrix[coin] = row;
    }

    return res.status(200).json({
      live: true,
      timestamp: new Date().toISOString(),
      matrix
    });
  } catch (err) {
    return res.status(500).json({ error: 'Failed to fetch Envy data', detail: err.message });
  }
}
