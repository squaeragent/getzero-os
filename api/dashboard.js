// Vercel Serverless Function — one batch of 10 coins per invocation
// Client calls this 4 times in parallel: ?batch=0, ?batch=1, ?batch=2, ?batch=3
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Cache-Control', 's-maxage=240, stale-while-revalidate=480');

  const API_KEY = process.env.ENVY_API_KEY;
  if (!API_KEY) return res.status(500).json({ error: 'ENVY_API_KEY not configured' });

  const BATCHES = [
    'AAVE,ADA,APT,ARB,AVAX,BCH,BNB,BTC,CRV,DOGE',
    'DOT,ENA,ETH,FARTCOIN,HYPE,INJ,JUP,kBONK,kPEPE,kSHIB',
    'LDO,LINK,LTC,NEAR,ONDO,OP,PAXG,PUMP,SEI,SOL',
    'SUI,TIA,TON,TRUMP,TRX,UNI,WLD,XPL,XRP,ZEC'
  ];

  const batchIdx = parseInt(req.query?.batch ?? '0', 10);
  if (batchIdx < 0 || batchIdx >= BATCHES.length) {
    return res.status(400).json({ error: 'batch must be 0-3' });
  }

  const coinBatch = BATCHES[batchIdx];
  // Max ~5 indicators per batch — API returns success:false with 9+
  const indicators = 'HURST_24H,DFA_24H,LYAPUNOV_24H,CLOSE_PRICE_15M,RSI_3H30M';

  try {
    const response = await fetch(
      `https://gate.getzero.dev/api/claw/paid/indicators/snapshot?coins=${coinBatch}&indicators=${indicators}`,
      { headers: { 'X-API-Key': API_KEY } }
    );

    const data = await response.json();
    if (!data.snapshot) {
      return res.status(200).json({ batch: batchIdx, coins: {}, error: data.error, _debug: { status: response.status, keys: Object.keys(data) } });
    }

    // Transform snapshot + compute regime
    const coins = {};
    for (const [coin, indList] of Object.entries(data.snapshot)) {
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
      coins[coin] = row;
    }

    return res.status(200).json({
      batch: batchIdx,
      timestamp: new Date().toISOString(),
      coins
    });
  } catch (err) {
    return res.status(200).json({ batch: batchIdx, error: err.message, coins: {}, _errType: err.constructor.name });
  }
}
