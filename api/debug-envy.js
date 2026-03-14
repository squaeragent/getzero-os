// Test what Vercel sees when calling the Envy API
// Ship a version that logs the raw error text

export default async function handler(req, res) {
  const API_KEY = process.env.ENVY_API_KEY;
  const url = 'https://gate.getzero.dev/api/claw/paid/indicators/snapshot?coins=BTC&indicators=HURST_24H';
  
  try {
    const r = await fetch(url, { headers: { 'X-API-Key': API_KEY || 'missing' } });
    const text = await r.text();
    let parsed;
    try { parsed = JSON.parse(text); } catch { parsed = text; }
    return res.status(200).json({ status: r.status, headers: Object.fromEntries(r.headers), body: parsed });
  } catch (err) {
    return res.status(200).json({ error: err.message, type: err.constructor.name });
  }
}
