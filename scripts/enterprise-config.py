#!/usr/bin/env python3
"""Generate a secret-free deployment bundle; never create users or attest business facts."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def origin(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname)
    ):
        raise argparse.ArgumentTypeError(
            "use an exact HTTPS origin without path or credentials"
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid port") from exc
    return value


def bundle(site, api, identity):
    authority = identity + "/realms/repayguard"
    endpoint = authority + "/protocol/openid-connect"
    runtime = {
        "schema_version": 1,
        "mode": "connected",
        "api_base_url": api + "/api/v1",
        "oidc_authority": authority,
        "oidc_client_id": "repayguard-web",
        "oidc_audience": "repayguard-api",
        "oidc_authorization_endpoint": endpoint + "/auth",
        "oidc_token_endpoint": endpoint + "/token",
        "oidc_logout_endpoint": endpoint + "/logout",
    }
    realm = {
        "realm": "repayguard",
        "enabled": True,
        "registrationAllowed": False,
        "resetPasswordAllowed": True,
        "sslRequired": "all",
        "accessTokenLifespan": 300,
        "clients": [
            {
                "clientId": "repayguard-web",
                "enabled": True,
                "publicClient": True,
                "protocol": "openid-connect",
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "implicitFlowEnabled": False,
                "redirectUris": [site + "/"],
                "webOrigins": [site],
                "attributes": {
                    "pkce.code.challenge.method": "S256",
                    "post.logout.redirect.uris": site + "/",
                },
                "protocolMappers": [
                    {
                        "name": "business-api-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "consentRequired": False,
                        "config": {
                            "included.custom.audience": "repayguard-api",
                            "access.token.claim": "true",
                            "id.token.claim": "false",
                        },
                    }
                ],
            }
        ],
    }
    return runtime, realm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-origin", type=origin, required=True)
    parser.add_argument("--api-origin", type=origin, required=True)
    parser.add_argument("--identity-origin", type=origin, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runtime, realm = bundle(args.site_origin, args.api_origin, args.identity_origin)
    args.output.mkdir(parents=True, exist_ok=True)
    for name in [
        "runtime-config.json",
        "repayguard-realm.json",
        "backend-public.env",
        "nginx.conf",
    ]:
        path = args.output / name
        if path.exists():
            parser.error(f"{path.name} already exists; use a new output directory")
    for name, data in [
        ("runtime-config.json", runtime),
        ("repayguard-realm.json", realm),
    ]:
        (args.output / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )
    (args.output / "backend-public.env").write_text(
        "AUTH_MODE=oidc\nOIDC_ISSUER=" + runtime["oidc_authority"] + "\n"
        "OIDC_AUDIENCE=repayguard-api\nOIDC_JWKS_URL="
        + runtime["oidc_authority"]
        + "/protocol/openid-connect/certs\n"
        "OIDC_REQUIRED_CLAIMS=sub,exp,iat,jti\nCORS_ORIGINS=" + args.site_origin + "\n"
    )
    nginx = (
        Path(__file__).resolve().parents[1] / "deploy/1panel/nginx.conf"
    ).read_text()
    nginx = nginx.replace(
        "connect-src 'self';",
        "connect-src 'self' " + args.api_origin + " " + args.identity_origin + ";",
    )
    nginx = nginx.replace(
        "    location /api/ {",
        """    location = /runtime-config.json {
        add_header Cache-Control "no-store";
        try_files $uri =404;
    }

    location /api/ {""",
    )
    (args.output / "nginx.conf").write_text(nginx)
    print(
        json.dumps(
            {"generated": True, "contains_secrets": False, "users_created": False}
        )
    )


if __name__ == "__main__":
    main()
