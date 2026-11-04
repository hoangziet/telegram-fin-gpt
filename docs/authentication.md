# Authentication implementation

This document records the M2 authentication decisions and the intended use of
the current implementation. Username and password are the only product login
method. Microsoft Entra and public self-registration are out of scope.

## Initial owner setup

1. Run database migrations.
2. Set a random `FIN_GPT_SECRET_KEY` with at least 32 bytes.
3. Run `flask --app 'fingpt:create_app()' bootstrap-owner my-owner-name` in the
   backend environment. The command prompts twice for a password and refuses to
   run after any account exists.
4. Configure `AUTH_COOKIE_SECURE=true` in production behind HTTPS. Set it to
   `false` only for local HTTP development.

The command is protected against concurrent first-run invocations with a
PostgreSQL transaction advisory lock. It is not exposed as an HTTP endpoint.

## Authentication controls

- Usernames are NFKC-normalized, case-folded ASCII identifiers matching
  `[a-z0-9._-]`, 3–128 characters. Passwords are not trimmed or normalized;
  bootstrap accepts 15–128 Unicode characters, with a 1,024-byte UTF-8 cap.
- Passwords use Argon2id with explicit memory, time, parallelism, salt, and hash
  settings. Successful logins upgrade old hashes when the configured hasher
  requires it. Unknown usernames still verify against a random dummy hash so
  that credential errors share one response and expensive verification path.
- Login creates a random 256-bit bearer token, stores its SHA-256 hash, and
  returns it only in an `HttpOnly`, `SameSite=Strict` cookie. Sessions expire
  after 12 hours and logout revokes the server-side row. Production uses the
  `__Host-` cookie prefix, `Secure`, `Path=/`, and no `Domain` attribute.
- A random session-bound CSRF token is returned at login and can be retrieved
  after a page refresh. Every authenticated `POST`, `PUT`, `PATCH`, and `DELETE`
  under `/api/` requires it in `X-CSRF-Token`. Tokens remain in app memory; the
  browser does not put credentials or session tokens in local storage.
- Login allows ten attempts per client address in a shared PostgreSQL
  15-minute window. Only an HMAC digest is persisted. The app does not trust
  forwarded IP headers; configure proxy trust only after the production ingress
  chain is known.
- Every `/api/` path is authenticated by default except the exact login route.
  Health checks remain public. Future webhook routes must declare their own
  explicit verification and be added as a narrow exception only in their
  milestone.
- The API never takes a user ID from the browser. Resource repositories and
  queries must receive `g.auth_user.id`, and database composite ownership keys
  are a second layer of defense.

## Threat cases exercised

- Repeated first-owner bootstrap, invalid or short passwords, wrong credentials,
  missing session, client-supplied user identity, repeated login attempts,
  missing CSRF, logout revocation, and public account registration.
- Database errors during auth fail closed. API responses do not include password
  hashes, session bearer tokens, stack traces, or private database details.
- Auth request bodies are limited to 16 KiB. Future receipt uploads must use
  their own deliberate size and content validation rather than weakening this
  authentication limit.

## References

- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [OWASP CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [Flask security considerations](https://flask.palletsprojects.com/en/stable/web-security/)
