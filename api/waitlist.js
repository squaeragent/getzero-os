// Vercel Serverless Function — Waitlist Signup
// Stores email + sends notification to founders

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  const { email } = req.body || {};
  if (!email || !email.includes('@')) {
    return res.status(400).json({ error: 'Valid email required' });
  }

  const RESEND_KEY = process.env.RESEND_API_KEY;
  
  // Send notification to founders
  if (RESEND_KEY) {
    try {
      await fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${RESEND_KEY}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from: 'ZERO OS <intelligence@getzero.dev>',
          to: 'degenie@getzero.dev',
          subject: `[ZERO OS] New waitlist signup: ${email}`,
          text: `New waitlist signup:\n\nEmail: ${email}\nTime: ${new Date().toISOString()}\nSource: getzero.dev\n\n— ZERO OS Waitlist`
        })
      });
    } catch (e) {
      console.error('Resend error:', e);
    }
  }

  return res.status(200).json({ ok: true, ref: 'ZOS-' + Math.random().toString(36).substr(2, 6).toUpperCase() });
}
