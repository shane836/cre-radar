// The only server-side code in this project.
//
// The page is otherwise a static file. This exists because a static page
// cannot put anyone on a mailing list on its own: an ESP's hosted signup page
// is a handoff that loses people, and beehiiv's in particular cannot be posted
// to from a hand-rolled field (`/create` needs a per-session `visit_token` a
// static page cannot mint, and `/subscribe?email=` does not prefill, so the
// visitor types the address twice). An API has no such problem — it just needs
// a key, which means a server.
//
// So the box posts here, and this puts the address on the list.
//
// **MailerLite is the list that matters.** It is what actually sends the weekly
// issue (`digest/newsletter.py`), on the same authenticated sending domain the
// firm is warming for its own blast — one domain, one reputation, one warm-up.
// The two audiences stay apart as two *groups* inside one account, which is why
// MAILERLITE_GROUP_ID is not optional: without it a signup would land in the
// account rather than in the events group.
//
// beehiiv is kept as a **mirror only**, and only while the migration is in
// flight. It cannot send — the cre-radar publication is on the free plan and
// beehiiv's Create Post endpoint is Max/Enterprise — so a failure there is
// logged and never surfaced. Once the existing subscribers are moved across,
// delete the two BEEHIIV_ variables from the Vercel project and this whole leg
// goes quiet on its own, with no code change.
//
// Runs on the Node.js runtime (Fluid Compute, the default). No dependencies:
// `fetch` is built in, and Vercel parses JSON and form-encoded bodies into
// `req.body` before this is called.
//
// Environment, set on the **Vercel project** (the local `.env` is not
// deployed):
//   MAILERLITE_API_KEY      required. MailerLite -> Integrations -> API.
//   MAILERLITE_GROUP_ID     required. The cre-radar group, NOT the associates one.
//   BEEHIIV_API_KEY         optional, transitional. The mirror above.
//   BEEHIIV_PUBLICATION_ID  optional, transitional. The cre-radar publication.
//   RESEND_API_KEY          optional. Adds a copy to the radar alias.
//   SUBSCRIBE_FROM / _TO    optional, with DIGEST_FROM / DIGEST_TO as
//                           fallbacks. Only used for that copy.

// The product's public name. Duplicated from `src/cre_radar/__init__.py`
// because this file is the one piece of the project that is not Python; the
// two must be changed together.
const APP_NAME = 'SoCal CRE Events';

const BEEHIIV_API = 'https://api.beehiiv.com/v2';
const MAILERLITE_API = 'https://connect.mailerlite.com/api';

// Where a failed signup is sent instead, so a broken function never costs the
// visitor the signup. The cre-radar publication's own hosted page — never the
// firm's, which would put an events reader on the wrong list. Unset means the
// error page carries no link, the same rule `site.py` follows: omit it rather
// than ship a wrong or dead one.
const FALLBACK_URL = process.env.BEEHIIV_SUBSCRIBE_URL || '';

// How the signup is tagged on the list, so cre-radar traffic stays separable
// from the firm's own inside a shared MailerLite account.
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

/** A copy to the radar alias, so a signup is visible without opening the ESP.
 *
 * Best-effort by design: the list already has the subscriber by the time this
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
          `${email} joined the cre-radar list.`,
          '',
          `Status: ${status}`,
          `When:   ${new Date().toISOString()}`,
          `Page:   ${referer || 'cre-radar'}`,
          '',
          'They are already on the list — this is a receipt, not a to-do.',
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

/** Keep beehiiv in step while the list is being moved off it.
 *
 * Best-effort on purpose, the same way `notify` is: MailerLite already has the
 * subscriber by the time this runs and is the thing that sends, so a beehiiv
 * failure is a stale mirror, not a lost signup. Turning it into an error would
 * report a successful subscription as a failure.
 *
 * Unset BEEHIIV_ variables are the expected end state, not a misconfiguration —
 * that is how this leg is retired.
 */
async function mirrorToBeehiiv(email, referer) {
  const key = process.env.BEEHIIV_API_KEY;
  const publication = process.env.BEEHIIV_PUBLICATION_ID;
  if (!key || !publication) return;

  try {
    const response = await fetch(
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
          // MailerLite sends the welcome now. Two would be a mess.
          send_welcome_email: false,
          utm_source: UTM.source,
          utm_medium: UTM.medium,
          referring_site: referer || undefined,
        }),
      }
    );
    if (!response.ok) {
      console.error('subscribe: beehiiv mirror failed -', response.status,
                    await response.text().catch(() => '<unreadable>'));
    }
  } catch (error) {
    console.error('subscribe: beehiiv mirror failed -', error);
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

  const mlKey = process.env.MAILERLITE_API_KEY;
  const mlGroup = process.env.MAILERLITE_GROUP_ID;

  // No silent failures: a missing key is a deployment mistake, and swallowing
  // it would drop real signups behind a thank-you the visitor believes.
  if (!mlKey || !mlGroup) {
    console.error(
      'subscribe: not configured -',
      [!mlKey && 'MAILERLITE_API_KEY', !mlGroup && 'MAILERLITE_GROUP_ID']
        .filter(Boolean).join(', '),
      'missing'
    );
    return reply(req, res, 500, 'Signup is not wired up right now.', {
      heading: 'Something went wrong', extra: FALLBACK_LINK,
    });
  }

  let response;
  try {
    // Upserts: 201 for a new subscriber, 200 for one already there. Either is
    // a success, so a visitor who signs up twice sees a thank-you rather than
    // an error about a problem that is not theirs.
    response = await fetch(`${MAILERLITE_API}/subscribers`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${mlKey}`,
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify({
        email,
        groups: [mlGroup],
        // Carried across from beehiiv so cre-radar signups stay separable from
        // the firm's own traffic inside a shared account.
        fields: { source: UTM.source, medium: UTM.medium },
      }),
    });
  } catch (error) {
    console.error('subscribe: mailerlite unreachable -', error);
    return reply(req, res, 502, 'Could not reach the mailing list.', {
      heading: 'Something went wrong', extra: FALLBACK_LINK,
    });
  }

  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    // The body carries the reason (bad token, unknown group, blocked address).
    // Logging it is the difference between a five-minute fix and a guessing
    // game.
    console.error('subscribe: mailerlite returned', response.status,
                  JSON.stringify(payload));
    return reply(req, res, 502, 'Could not record that signup.', {
      heading: 'Something went wrong', extra: FALLBACK_LINK,
    });
  }

  // The subscriber is on the sending list from here on, so nothing below may
  // turn this into a failure the visitor sees.
  //
  // MailerLite answers `active` when the group takes the address outright, and
  // `unconfirmed` when double opt-in is on and it has sent a confirmation. Both
  // are successes from the visitor's side; only the copy differs, because only
  // one of them still needs something from them.
  const status = payload?.data?.status || 'unknown';
  await mirrorToBeehiiv(email, req.headers.referer);

  await notify(email, status, req.headers.referer);

  const message = status === 'active'
    ? "You're on the list. Watch for the next one."
    : "Almost there — check your inbox to confirm.";

  return reply(req, res, 200, message, { heading: 'Thanks' });
};
