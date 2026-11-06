# Security

PocketADM gives a web and app UI the power of a root shell on the host it runs
on. Every design decision starts from that sentence. If you find a way around
it, we want to know fast.

## Reporting

Email **maximilian.elias.paasch@gmail.com** with `PocketADM security` in the
subject. Please do not open a public issue for it. You will get an answer
within a few days, and credit in the changelog if you want it.

## Scope

- The server application (`server/`, FastAPI): auth, tokens, WebSocket tickets,
  the agent command policy, every endpoint that reaches the host or the Docker
  socket.
- The push relay (`push-relay/`): registration, rate limits, what it stores.
- Install and deployment (`install.sh`, `deploy/`, docker and compose files).
- The web client (`web/`) and the iPhone app (`client-swift/`) as far as they
  handle tokens, TLS pinning or rendered content.

## Not in scope

- Hardening of the host around it: PocketADM does not manage what it does not
  install, and advice about a hardened host is what the health checks do best
  effort.
- The demo at demo.pocketadm.com: it runs the same code but serves canned data
  with no host access. Still tell us if it lets you reach anything real.

## How we think about it

- The agent asks before it acts, and "read-only" means *stays on this server*:
  enforced in code, with the policy in `server/cmdpolicy.py`, not only in the
  prompt.
- Credentials never travel in URLs; WebSockets use single-use tickets.
- Model output is rendered escaped, under a strict CSP.
- Security fixes get a release as soon as they are verified.
