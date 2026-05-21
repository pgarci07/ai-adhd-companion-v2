#!/usr/bin/env python3
"""Invoke the recurrent_task_sched Supabase Edge Function using credentials from .env.

This helper keeps the invocation flow simple on machines where the Supabase CLI
does not provide `functions invoke`. It reads the project URL and a privileged
key from the local `.env`, builds the function endpoint, and sends a JSON body.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error, request


DEFAULT_FUNCTION_NAME = "recurrent_task_sched"
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
SECRET_KEY_NAME = "ADHD_COMPANION_KEY"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Invoke a Supabase Edge Function using URL/key values from .env",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_PATH),
        help="Path to the .env file. Defaults to the project root .env.",
    )
    parser.add_argument(
        "--function",
        default=DEFAULT_FUNCTION_NAME,
        help=f"Function name to invoke. Defaults to {DEFAULT_FUNCTION_NAME}.",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Invoke with dry_run=true.",
    )
    parser.add_argument(
        "--for-good",
        dest="dry_run",
        action="store_false",
        help="Invoke with dry_run=false.",
    )
    parser.set_defaults(dry_run=True)
    return parser.parse_args()


def load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f".env file not found: {path}")

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        values[key] = value

    return values


def build_endpoint(supabase_url: str, function_name: str) -> str:
    return f"{supabase_url.rstrip('/')}/functions/v1/{function_name}"


def invoke_function(endpoint: str, bearer_token: str, payload: dict[str, object]) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }
    req = request.Request(endpoint, data=body, headers=headers, method="POST")

    try:
        with request.urlopen(req) as response:
            return response.status, response.read().decode("utf-8")
    except error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def resolve_secret_key(env_values: dict[str, str]) -> str | None:
    return env_values.get(SECRET_KEY_NAME) or os.environ.get(SECRET_KEY_NAME)


def mask_secret(value: str) -> str:
    if len(value) <= 12:
        return "<redacted>"

    return f"{value[:6]}...{value[-4:]}"


def print_auth_hint(status_code: int, response_text: str, bearer_token: str) -> None:
    if status_code != 401 or "UNAUTHORIZED_INVALID_JWT_FORMAT" not in response_text:
        return

    print(
        "\nAuth hint: Supabase rejected the Authorization header before the Edge Function ran.",
        file=sys.stderr,
    )
    if bearer_token.startswith("sb_secret_"):
        print(
            "The key looks like an sb_secret_ key, which is not a JWT. "
            "Deploy this function with: supabase functions deploy recurrent_task_sched --no-verify-jwt",
            file=sys.stderr,
        )
    else:
        print(
            "This endpoint appears to require a JWT in Authorization. "
            "Use a JWT-style anon/service_role key or redeploy with --no-verify-jwt for the current sb_secret_ flow.",
            file=sys.stderr,
        )


def main() -> int:
    args = parse_args()
    env_path = Path(args.env_file).expanduser().resolve()

    try:
        env_values = load_dotenv(env_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    supabase_url = env_values.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    if not supabase_url:
        print("SUPABASE_URL is missing from .env and current environment.", file=sys.stderr)
        return 1

    bearer_token = resolve_secret_key(env_values)
    if not bearer_token:
        print(f"{SECRET_KEY_NAME} is missing from .env and current environment.", file=sys.stderr)
        return 1

    endpoint = build_endpoint(supabase_url, args.function)
    payload = {"dry_run": bool(args.dry_run)}

    status_code, response_text = invoke_function(endpoint, bearer_token, payload)

    print(f"Endpoint: {endpoint}")
    print(f"Key Used: {SECRET_KEY_NAME} ({mask_secret(bearer_token)})")
    print(f"HTTP {status_code}")
    print_auth_hint(status_code, response_text, bearer_token)

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        print(response_text)
        return 0 if 200 <= status_code < 300 else 1

    print(json.dumps(parsed, indent=2, ensure_ascii=False))
    return 0 if 200 <= status_code < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
