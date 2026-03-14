// Vercel Serverless Function — Full dashboard data for all coins
// Fetches in batches of 10 (API limit), caches 5 minutes
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

  // Key indicators for regime detection + context
  const indicators = 'HURST_24H,HURST_48H,DFA_24H,DFA_48H,LYAPUNOV_24H,LYAPUNOV_48H,RSI_3H30M,ADX_3H30M,ROC_3H,ROC_24H,BB_POS_24H,CLOSE_PRICE_15M,XONE_AVG_NET,XONE_SPREAD,ICHIMOKU_BULL';

  try {
    const batches = await Promise.all(
      allCoins.map(async (coinBatch) => {
        try {
          const response = await fetch(
            `https://gate.getzero.dev/api/claw/paid/indicators/snapshot?coins=${coinBatch}&indicators=${indicators}`,
            { headers: { 'X-API-Key': API_KEY } }
          );
          const data = await response.json();
          if (data.error) {
            console.error('Batch error:', coinBatch, data.error);
            return {};
          }
          return data.snapshot || {};
        } catch (e) {
          console.error('Batch fetch failed:', coinBatch, e.message);
          return {};
        }
      })
    );

    // Merge all batches
    const allData = {};
    for (const batch of batches) {
      for (const [coin, indicators] of Object.entries(batch)) {
        const row = {};
        for (const ind of indicators) {
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
    if (coinCount === 0) {
      // Debug: try single fetch to diagnose
      try {
        const debugResp = await fetch(
          'https://gate.getzero.dev/api/claw/paid/indicators/snapshot?coins=BTC&indicators=HURST_24H',
          { headers: { 'X-API-Key': API_KEY } }
        );
        const debugData = await debugResp.json();
        return res.status(200).json({
          live: false,
          debug: { status: debugResp.status, keys: Object.keys(debugData), hasSnapshot: !!debugData.snapshot },
          timestamp: new Date().toISOString(),
          coinCount: 0,
          coins: {}
        });
      } catch (de) {
        return res.status(200).json({ live: false, debug: { error: de.message }, coinCount: 0, coins: {} });
      }
    }
    return res.status(200).json({
      live: true,
      timestamp: new Date().toISOString(),
      coinCount,
      coins: allData
    });
  } catch (err) {
    return res.status(500).json({ error: 'Failed to fetch data', detail: err.message });
  }
}
