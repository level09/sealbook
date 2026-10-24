/**
 * One passkey gesture over a server-prepared ledger hash. The server keeps the
 * prepared statement; the browser only signs its hash and posts the assertion.
 */
const decode = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(s.length / 4) * 4, '=')), c => c.charCodeAt(0));
const encode = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

function credentialJSON(cred) {
  if (typeof cred.toJSON === 'function') return cred.toJSON();
  const r = cred.response;
  return {
    id: cred.id,
    rawId: encode(cred.rawId),
    type: cred.type,
    response: {
      authenticatorData: encode(r.authenticatorData),
      clientDataJSON: encode(r.clientDataJSON),
      signature: encode(r.signature),
      userHandle: r.userHandle ? encode(r.userHandle) : null,
    },
    clientExtensionResults: cred.getClientExtensionResults(),
  };
}

const refusal = err => {
  const body = err.response && err.response.data;
  const out = new Error((body && (body.error || body.message)) || err.message || 'Review failed');
  if (body && body.reason === 'no_passkey') {
    out.noPasskey = true;
    out.setupUrl = setupUrl(body.setup_url);
  }
  return out;
};

// Back to this page after registration, whatever the server guessed from the referrer.
const setupUrl = url => `${(url || '/wan-register').split('?')[0]}?next=${encodeURIComponent(location.pathname + location.search)}`;

/* The person has no passkey yet: say so in place, next to the control they pressed,
   with the way to set one up. One notice per page; a page may render its own instead
   by catching err.noPasskey and passing {inline: false}. */
function noPasskeyNotice(err, anchor) {
  document.querySelector('.pk-needed')?.remove();
  const box = document.createElement('div');
  box.className = 'pk-needed';
  box.setAttribute('role', 'alert');
  box.innerHTML = '<i class="ti ti-fingerprint" aria-hidden="true"></i><span></span><a class="pk-setup"></a>';
  box.querySelector('span').textContent = err.message;
  Object.assign(box.querySelector('a'), {href: err.setupUrl, textContent: 'Set up a passkey'});
  if (!document.getElementById('pk-needed-css')) {
    document.head.append(Object.assign(document.createElement('style'), {id: 'pk-needed-css', textContent:
      '.pk-needed{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;margin:8px 0;padding:8px 10px;border:1px solid var(--line-strong,#c9c9c3);border-radius:6px;background:var(--panel,#fff);color:var(--ink,#18191b);font-size:13px;line-height:1.45}' +
      '.pk-needed .pk-setup{font-weight:600;color:inherit;text-underline-offset:2px}'}));
  }
  const at = anchor && anchor !== document.body && anchor.closest('.v-btn, button') || null;
  if (at && at.isConnected) at.closest('.v-card-actions, .d-flex, div')?.after(box);
  else (document.querySelector('main, #app') || document.body).prepend(box);
}

/** Stage a statement or gesture: {hash, options, ...} to show before signing. */
export async function prepare(item, start = '/api/review/options', {inline = true} = {}) {
  const anchor = document.activeElement;
  try {
    return (await axios.post(start, {item})).data;
  } catch (err) {
    const out = refusal(err);
    if (out.noPasskey && inline) noPasskeyNotice(out, anchor);
    throw out;
  }
}

/** Ask the passkey to sign what `prepare` staged and commit it through /api/review/commit. */
export async function sign(prepared) {
  try {
    const o = prepared.options;
    const cred = await navigator.credentials.get({
      publicKey: {
        ...o,
        challenge: decode(o.challenge),
        allowCredentials: (o.allowCredentials || []).map(c => ({...c, id: decode(c.id)})),
      },
    });
    return (await axios.post('/api/review/commit', {item: {hash: prepared.hash, credential: credentialJSON(cred)}})).data;
  } catch (err) { throw refusal(err); }
}

/** Resolve with the commit response, or throw an Error whose message is the refusal. */
export async function attest(item, start = '/api/review/options', opts = {}) {
  return sign(await prepare(item, start, opts));
}
