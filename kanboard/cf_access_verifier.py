#!/usr/bin/env python3
"""Cloudflare Access JWT verifier — nginx auth_request target.

nginx calls /verify on each request; we validate Cf-Access-Jwt-Assertion
against Cloudflare's team public keys + the app AUD.
  ENFORCE=true  -> block invalid/missing tokens (403)
  ENFORCE unset -> monitor mode: always 200, log the verdict (safe rollout)
/healthz always 200 (container healthcheck).
"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from jwt import PyJWKClient

TEAM = os.environ.get("CF_TEAM", "opteia")
AUD = os.environ.get("CF_APP_AUD", "")
ENFORCE = os.environ.get("ENFORCE", "").lower() in ("1", "true", "yes")
JWKS_URI = f"https://{TEAM}.cloudflareaccess.com/cdn-cgi/access/certs"
_JWKS = PyJWKClient(JWKS_URI, cache_keys=True, lifespan=3600, max_cached_keys=8)


def verify(assertion: str):
    if not assertion:
        return None, "missing-assertion"
    try:
        key = _JWKS.get_signing_key_from_jwt(assertion).key
        claims = jwt.decode(
            assertion, key, algorithms=["RS256"], audience=AUD,
            options={"require": ["exp", "iat", "email", "aud"]},
        )
        return claims.get("email"), "valid"
    except Exception as e:  # InvalidTokenError subclasses
        return None, f"invalid:{type(e).__name__}"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/healthz"):
            self._send(200)
            return
        assertion = self.headers.get("Cf-Access-Jwt-Assertion", "")
        email, verdict = verify(assertion)
        print(f"[verify] {verdict} email={email}", flush=True)
        self._send(200 if (verdict == "valid" or not ENFORCE) else 403)

    do_POST = do_GET

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"cf-access-verifier up: team={TEAM} aud={AUD[:10]}... enforce={ENFORCE}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
