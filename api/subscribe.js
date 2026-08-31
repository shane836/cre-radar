// The only server-side code in this project.
//
// The page is otherwise a static file. This exists because a static page
// cannot subscribe anyone to beehiiv on its own: `/create` needs a per-session
// `visit_token` it cannot mint, and `/subscribe?email=` does not prefill, so a
// hand-rolled field would make the visitor type the address twice. Beehiiv's
// v2 API has no such problem — it just needs a key, which means a server.
//
// So the box posts here, and this creates the subscription on cre-radar's own
// beehiiv publication. Nothing is stored in this repo: beehiiv is the list.
//
// Runs on the Node.js runtime (Fluid Compute, the default). No dependencies:
// `fetch` is built in, and Vercel parses JSON and form-encoded bodies into
// `req.body` before this is called.
//
// Environment, set on the **Vercel project** (the local `.env` is not
// deployed):
//   BEEHIIV_API_KEY         required. beehiiv -> Settings -> API.
//   BEEHIIV_PUBLICATION_ID  required. The cre-radar publication, `pub_...`.
//   RESEND_API_KEY          optional. Adds a copy to the radar alias.
//   SUBSCRIBE_FROM / _TO    optional, with DIGEST_FROM / DIGEST_TO as
//                           fallbacks. Only used for that copy.

// The product's public name. Duplicated from `src/cre_radar/__init__.py`
// because this file is the one piece of the project that is not Python; the
// two must be changed together.
const APP_NAME = 'SoCal CRE Events';

const BEEHIIV_API = 'https://api.beehiiv.com/v2';

// Where a failed signup is sent instead, so a broken function never costs the
// visitor the signup. The cre-radar publication's own hosted page — never the
// firm's, which would put an events reader on the wrong list. Unset means the
// error page carries no link, the same rule `site.py` follows: omit it rather
// than ship a wrong or dead one.
const FALLBACK_URL = process.env.BEEHIIV_SUBSCRIBE_URL || '';

// How the signup is tagged inside beehiiv, so cre-radar traffic stays
// separable from the firm's site even though it is now its own publication.
const UTM = { source: 'radar.masonequitypartners.com', medium: 'cre-radar' };

// Deliberately loose. An address is verified by mail reaching it, not by a
// regex; this only catches a visitor who typed their name into the wrong box.
const LOOKS_LIKE_EMAIL = /^[^\s@]+@[^\s@.]+\.[^\s@]+$/;

const MAX_EMAIL = 254;   // RFC 5321 maximum. Anything longer is not an address.

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/** True when the caller is the page's own fetch rather than a plain form POST.
 *
 * A form POST from a reader without JavaScript navigates to this URL, so it
 * needs a page to land on. The fetch path wants JSON and stays on the listing.
 */
function wantsJson(req) {
  return (req.headers['x-requested-with'] === 'fetch' ||
          String(req.headers.accept || '').includes('application/json'));
}

/** The no-JavaScript landing page. Themed the same two ways the site is. */
function htmlPage(heading, body) {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(heading)} - ${APP_NAME}</title>
<style>
:root{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--line:#e6e6eb;
  --ink:#1b1b20;--dim:#7a7a85;--accent:#1a56b8}
@media (prefers-color-scheme:dark){:root{--bg:#141417;--card:#1c1c21;
  --line:#2c2c33;--ink:#f0f0f2;--dim:#9a9aa4;--accent:#7aa5f0}}
body{background:var(--bg);color:var(--ink);margin:0;padding:60px 16px;
  font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}
main{max-width:440px;margin:0 auto;background:var(--card);border-radius:12px;
  border:1px solid var(--line);padding:26px}
h1{font-size:19px;margin:0 0 10px}
p{margin:0 0 12px;color:var(--dim)}
a{color:var(--accent)}
</style></head><body><main>
<h1>${escapeHtml(heading)}</h1>
${body}
<p><a href="/">Back to the events</a></p>
</main></body></html>`;
}

const FALLBACK_LINK = FALLBACK_URL
  ? `<p><a href="${escapeHtml(FALLBACK_URL)}" rel="noopener">Subscribe here instead</a>.</p>`
  : '';

/** One reply, in whichever of the two shapes the caller asked for. */
function reply(req, res, status, message, { heading, extra = '' } = {}) {
  if (wantsJson(req)) {
    return res.status(status).json({ ok: status < 400, message });
  }
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  return res
    .status(status)
    .send(htmlPage(heading || message, `<p>${escapeHtml(message)}</p>${extra}`));
}

/** A copy to the radar alias, so a signup is visible without opening beehiiv.
 *
 * Best-effort by design: beehiiv already has the subscriber by the time this
 * runs, so a mail failure must not turn a successful signup into an error. It
 * is logged, never surfaced.
 */
async function notify(email, status, referer) {
  const key = process.env.RESEND_API_KEY;
  const from = process.env.SUBSCRIBE_FROM || process.env.DIGEST_FROM;
  const to = process.env.SUBSCRIBE_TO || process.env.DIGEST_TO;
  if (!key || !from || !to) return;          // Opt-in. Not a misconfiguration.

  try {
    const response = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${key}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from,
        to: to.split(',').map(one => one.trim()).filter(Boolean),
        reply_to: email,        // Replying reaches the subscriber, not you.
        subject: `cre-radar signup: ${email}`,
        text: [
          `${email} subscribed to the cre-radar publication.`,
          '',
          `Status: ${status}`,
          `When:   ${new Date().toISOString()}`,
          `Page:   ${referer || 'cre-radar'}`,
          '',
          'They are already in beehiiv — this is a receipt, not a to-do.',
        ].join('\n'),
      }),
    });
    if (!response.ok) {
      console.error('subscribe: receipt not sent -', response.status,
                    await response.text().catch(() => '<unreadable>'));
    }
  } catch (error) {
    console.error('subscribe: receipt not sent -', error);
  }
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return reply(req, res, 405, 'Send this as a POST.');
  }

  const body = req.body && typeof req.body === 'object' ? req.body : {};

  // Honeypot. Hidden from people, irresistible to the bots that fill every
  // field they find. Answer as if it worked — telling a bot it was caught only
  // teaches whoever wrote it. Nothing is sent.
  if (String(body.company || '').trim()) {
    return reply(req, res, 200, "You're on the list.", { heading: 'Thanks' });
  }

  const email = String(body.email || '').trim();
  if (!email || email.length > MAX_EMAIL || !LOOKS_LIKE_EMAIL.test(email)) {
    return reply(req, res, 400, 'That does not look like an email address.', {
      heading: 'Check the address',
    });
  }

  const key = process.env.BEEHIIV_API_KEY;
  const publication = process.env.BEEHIIV_PUBLICATION_ID;

  // No silent failures: a missing key is a deployment mistake, and swallowing
  // it would drop real signups behind a thank-you the visitor believes.
  if (!key || !publication) {
    console.error(
      'subscribe: not configured -',
      [!key && 'BEEHIIV_API_KEY', !publication && 'BEEHIIV_PUBLICATION_ID']
        .filter(Boolean).join(', '),
      'missing'
    );
    return reply(req, res, 500, 'Signup is not wired up right now.', {
      heading: 'Something went wrong', extra: FALLBACK_LINK,
    });
  }

  let response;
  try {
    response = await fetch(
      `${BEEHIIV_API}/publications/${encodeURIComponent(publication)}/subscriptions`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${key}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          email,
          // An address that unsubscribed once has to opt in again on purpose.
          reactivate_existing: false,
          send_welcome_email: true,
          utm_source: UTM.source,
          utm_medium: UTM.medium,
          referring_site: req.headers.referer || undefined,
        }),
      }
    );
  } catch (error) {
    console.error('subscribe: beehiiv unreachable -', error);
    return reply(req, res, 502, 'Could not reach the mailing list.', {
      heading: 'Something went wrong', extra: FALLBACK_LINK,
    });
  }

  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    // beehiiv's body carries the reason (bad key, wrong publication, blocked
    // address). Logging it is the difference between a five-minute fix and a
    // guessing game.
    console.error('subscribe: beehiiv returned', response.status,
                  JSON.stringify(payload));
    return reply(req, res, 502, 'Could not record that signup.', {
      heading: 'Something went wrong', extra: FALLBACK_LINK,
    });
  }

  // `validating` and `pending` are both successes from the visitor's side —
  // beehiiv has the address and is confirming it. Only the copy differs.
  const status = payload?.data?.status || 'unknown';
  await notify(email, status, req.headers.referer);

  const message = status === 'active'
    ? "You're on the list. Watch for the next one."
    : "Almost there — check your inbox to confirm.";

  return reply(req, res, 200, message, { heading: 'Thanks' });
};
