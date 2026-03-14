// Vercel Serverless Function — Full dashboard data for all coins
// Fetches in batches of 10 (API limit), caches 5 minutes
export const config = { maxDuration: 25 }; // Vercel Pro: up to 300s; Hobby: 10s default, request 25

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Cache-Control', 's-maxage=300, stale-while-revalidate=600');

  const API_KEY = process.env.ENVY_API_KEY;
  if (!API_KEY) {
    return res.status(500).json({ error: 'ENVY_API_KEY not configured' });
  }

  // All 40 coins in batches of 10
  const allCoins = [
    'AAVE,ADA,APT,ARB,AVAX,BCH,BNB,BTC,CRV,DOGE',
    'DOT,ENA,ETH,FARTCOIN,HYPE,INJ,JUP,kBONK,kPEPE,kSHIB',
    'LDO,LINK,LTC,NEAR,ONDO,OP,PAXG,PUMP,SEI,SOL',
    'SUI,TIA,TON,TRUMP,TRX,UNI,WLD,XPL,XRP,ZEC'
  ];

  // Key indicators for regime detection + context (trimmed for speed)
  const indicators = 'HURST_24H,DFA_24H,LYAPUNOV_24H,CLOSE_PRICE_15M,RSI_3H30M,ADX_3H30M,ROC_24H,BB_POS_24H,XONE_AVG_NET';

  try {
    // Fetch all 4 batches in parallel (sequential hits Vercel's 10s timeout)
    const errors = [];
    const batchResults = await Promise.all(
      allCoins.map(async (coinBatch) => {
        try {
          const response = await fetch(
            `https://gate.getzero.dev/api/claw/paid/indicators/snapshot?coins=${coinBatch}&indicators=${indicators}`,
            { headers: { 'X-API-Key': API_KEY }, signal: AbortSignal.timeout(8000) }
          );
          const data = await response.json();
          if (data.snapshot) return data.snapshot;
          errors.push({ batch: coinBatch, status: response.status });
          return {};
        } catch (e) {
          errors.push({ batch: coinBatch, error: e.message });
          return {};
        }
      })
    );
    const batches = batchResults;

    // Merge all batches — batch is the snapshot object { BTC: [...], ETH: [...] }
    const allData = {};
    for (const batch of batches) {
      for (const [coin, coinIndicators] of Object.entries(batch)) {
        if (!Array.isArray(coinIndicators)) continue; // skip non-coin keys
        const row = {};
        for (const ind of coinIndicators) {
          row[ind.indicatorCode] = ind.value;
          if (!row._ts && ind.timestamp) row._ts = ind.timestamp;
        }
        // Compute regime
        const h = row.HURST_24H;
        const d = row.DFA_24H;
        const ly = row.LYAPUNOV_24H != null ? Math.min(row.LYAPUNOV_24H, 2) / 2 : null;
        let regime = 'unknown';
        let confidence = 0;
        if (h != null && d != null && ly != null) {
          if (ly > 0.425) { regime = 'chaotic'; confidence = Math.min(ly / 0.5, 1); }
          else if (h < 0.45 && d < 0.45) { regime = 'reverting'; confidence = (0.45 - Math.max(h, d)) / 0.15; }
          else if (h > 0.55 && d > 0.55) { regime = 'trending'; confidence = (Math.min(h, d) - 0.55) / 0.15; }
          else if ((h < 0.45 && d > 0.55) || (h > 0.55 && d < 0.45)) { regime = 'shift'; confidence = Math.abs(h - d) / 0.3; }
          else { regime = 'neutral'; confidence = 0.3; }
          confidence = Math.min(Math.max(confidence, 0), 1);
        }
        row._regime = regime;
        row._confidence = parseFloat(confidence.toFixed(2));
        allData[coin] = row;
      }
    }

    const coinCount = Object.keys(allData).length;
    return res.status(200).json({
      live: coinCount > 0,
      timestamp: new Date().toISOString(),
      coinCount,
      coins: allData,
      ...(errors.length > 0 ? { _errors: errors } : {})
    });
  } catch (err) {
    return res.status(500).json({ error: 'Failed to fetch data', detail: err.message });
  }
}
