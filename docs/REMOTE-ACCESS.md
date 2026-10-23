# Using the dashboard away from home

ChorePilot runs on your own computer and never relays your data through a service of ours. To
use the dashboard from your phone on mobile data, or from your office, put an encrypted path in
front of it. Two are supported. Both keep ChorePilot listening on 127.0.0.1 only, and neither
needs a port opened on your router.

Never port-forward 8771 to the internet, and never use a public tunnel without an access layer:
anyone who finds the address could start guessing PINs.

## Tailscale (recommended for a household)

[Tailscale](https://tailscale.com) is a private network between your own devices.

1. Install Tailscale on the ChorePilot computer and on each phone, and sign in with the same
   account (or share the machine with family members' accounts).
2. On the ChorePilot computer:

   ```bash
   tailscale serve --bg 8771
   chorepilot web --public-url https://<this-machine>.<your-tailnet>.ts.net
   ```

   `tailscale serve` gives the dashboard an HTTPS address with a real certificate that only
   devices on your tailnet can open. Settings > Away from home shows the exact name when
   Tailscale runs on the same machine, and its "Save" button writes `web.public_url` into
   house.json so `chorepilot run` and the service use it too.
3. On the phone, open that address and add it to the home screen (Share > Add to Home Screen
   on iPhone; menu > Install app on Android).

## Cloudflare Tunnel with Access (a business, or a shared address)

1. Create a tunnel and a DNS name for it:

   ```bash
   cloudflared tunnel create chorepilot
   cloudflared tunnel route dns chorepilot home.example.com
   cloudflared tunnel run --url http://127.0.0.1:8771 chorepilot
   ```

2. In the Cloudflare dashboard, add an **Access application** for `home.example.com` with a
   policy that allows only your people (one-time email codes, or your company's identity
   provider). Requests that don't pass Access never reach ChorePilot.
3. Start the dashboard with `chorepilot web --public-url https://home.example.com`.

## What `--public-url` changes

- That host name is accepted in the Host header (DNS-rebinding protection stays on for every
  other name) and that origin is accepted for writes, next to the local one.
- Requests that arrive through it get `Secure` session cookies and an HSTS header.
  `X-Forwarded-Proto: https` from a loopback proxy counts too, for proxies that rewrite Host.
- Plain `http://` public URLs are refused: PINs must not cross the internet in clear text.
- Creating the first owner is never possible through it: that needs a request from the machine
  itself, with no proxy headers.

Everything else is unchanged: PIN sign-in with lockout, HttpOnly SameSite=Strict cookies, the
CSRF header on every write, the Content-Security-Policy, and the policy tiers (an unlock still
asks for the owner's PIN, kids still need a parent for water and heat).

## Only at home

`chorepilot web --lan --allow-host house.local` listens on every interface. There is no HTTPS on
the LAN, so use it only on a network you trust. Without `--allow-host`, the dashboard answers to
IP addresses (`http://192.168.1.20:8771`), localhost and this machine's own name
(`<hostname>` / `<hostname>.local`), and refuses any other Host header, so a web page on some
other site can't reach it through DNS rebinding. Add `--allow-host` for any other name you use.
