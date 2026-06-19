export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // WebSub verification — YouTube hub sends GET to confirm subscription
    if (request.method === 'GET' && url.pathname === '/websub') {
      const challenge = url.searchParams.get('hub.challenge');
      if (challenge) {
        return new Response(challenge, {
          status: 200,
          headers: { 'Content-Type': 'text/plain' },
        });
      }
      return new Response('Missing hub.challenge', { status: 400 });
    }

    // WebSub push — YouTube hub sends POST when a new video is published
    if (request.method === 'POST' && url.pathname === '/websub') {
      const body = await request.text();

      // Validate X-Hub-Signature (HMAC-SHA1 of body using WEBSUB_SECRET)
      const sig = request.headers.get('X-Hub-Signature') || '';
      const [algo, receivedHex] = sig.split('=');

      if (algo !== 'sha1' || !receivedHex) {
        return new Response('Bad signature algorithm', { status: 400 });
      }

      const encoder = new TextEncoder();
      const key = await crypto.subtle.importKey(
        'raw',
        encoder.encode(env.WEBSUB_SECRET),
        { name: 'HMAC', hash: 'SHA-1' },
        false,
        ['sign']
      );
      const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(body));
      const computedHex = Array.from(new Uint8Array(signature))
        .map(b => b.toString(16).padStart(2, '0'))
        .join('');

      if (computedHex !== receivedHex) {
        return new Response('Invalid signature', { status: 403 });
      }

      // Forward to Oracle VM — return 200 to hub regardless to avoid re-delivery loops
      try {
        await fetch(env.VM_WEBHOOK_URL, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/xml',
            'X-Webhook-Secret': env.VM_WEBHOOK_SECRET,
          },
          body: body,
        });
      } catch (err) {
        console.error('Failed to forward to VM:', err);
      }

      return new Response('OK', { status: 200 });
    }

    return new Response('Not Found', { status: 404 });
  },
};
