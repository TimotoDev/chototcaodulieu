# Tom on Lightsail ($7/month instance)

This is a read-only TiMoto Slack knowledge assistant, not the repair runner.
The OpenClaw gateway and Antigravity proxy bind to `127.0.0.1`. Caddy exposes
only `https://tom.timoto.ai/slack/events` for Slack's signed HTTP webhooks;
all other paths return 404. Only SSH, HTTP for TLS issuance, and HTTPS are
public. OpenClaw and the Slack plugin are pinned to `2026.9.6`; the proxy
is pinned to `2.8.6`.

The machine is staged but Tom is not live until the existing Slack app and a
Google Antigravity account are authorized. The Mintlify deployment's Git source
(`harrynguyentimoto/timoto_space`, `main`) is mirrored hourly as a read-only
local MCP search source; it had 104 MDX pages at initial deployment. Direct
OAuth to the private Mintlify `/authed/mcp` endpoint failed with HTTP 405, so
the runtime does not use that endpoint.
The existing Slack app now has Socket Mode off and its HTTPS Request URL
verified at `https://tom.timoto.ai/slack/events`. Event delivery is off while
the Antigravity account is pending; Caddy remains available for HTTPS. The
Tom and proxy services are stopped and disabled from boot until activation.
Do not paste tokens or OAuth codes into chat or commit them to Git.

The Slack Signing Secret and bot token are already stored in the instance's
`/home/tom/.openclaw/runtime.env` with owner-only permissions. Do not copy them
into chat or Git, and do not rerun `configure-tom.sh` unless a Slack secret is
rotated.

The previously issued bot token must be rotated before enabling event delivery.
After rotating it in Slack, replace the instance copy without putting the token
in chat or shell history:
   `bash ai-infra/lightsail-tom/ssh.sh --tty 'sudo /usr/local/sbin/rotate-bot-token.sh'`
Type the new bot token at the hidden prompt. The script verifies it with
Slack's `auth.test`, atomically replaces only the bot token, preserves the
signing secret and gateway token, and leaves both services stopped.

When the approved Google email is known, run
   `sudo -u tom -H bash -c 'cd /home/tom && antigravity-claude-proxy accounts add --no-browser'`.
Complete the Google OAuth prompt with that exact account. This is an unofficial
proxy and account access may be restricted by Google.

Then run `sudo /usr/local/sbin/activate-tom.sh`. It checks that the proxy has
   an available account, validates OpenClaw's config, and smoke-checks the
   local read-only KB snapshot before enabling and starting the services.

After activation succeeds, Event Subscriptions are enabled. DM the existing
Tom app and approve DM pairing using OpenClaw's pairing command on the instance.
Slack channel policy is `groupPolicy: open`, so Tom can respond in any channel
the app is a member of. Channel messages remain mention-gated by default: invite
Tom to the channel and mention `@Tom` to ask it a question. Ask a question with
a known KB answer to verify Tom cites a page path.

Useful checks: `sudo systemctl status tom-proxy tom-openclaw --no-pager`,
`sudo journalctl -u tom-openclaw -n 100 --no-pager`, and
`curl -fsS http://127.0.0.1:8080/health | jq .counts`.
Avoid posting unredacted logs because they may contain user content.
