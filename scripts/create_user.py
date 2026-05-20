"""Create a user and optionally assign businesses to them.

Usage:
    # interactive (prompts for password):
    python scripts/create_user.py --email erez@example.com --name "Erez Habani" --assign power_roofing

    # non-interactive:
    python scripts/create_user.py --email foo@bar.com --name "Foo" --password 's3cret' --role owner --assign power_roofing,fiveboro

    # list users / tenants:
    python scripts/create_user.py --list-users
    python scripts/create_user.py --list-tenants

Notes:
- Roles: 'owner' (default) sees only their own businesses; 'admin' sees all.
- --assign takes a comma-separated list of tenant_ids (folder names under src/tenants/).
- Password input via --password is visible in shell history; prefer the interactive prompt.
- Run AFTER the engine has been started at least once so the DB schema exists.
"""

import argparse
import asyncio
import getpass
import secrets
import sys
from pathlib import Path

# Make src/ importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env so we use the same DB path the engine does
from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from src.integrations.auth.passwords import hash_password  # noqa: E402
from src.integrations.leads.db import LeadsDB  # noqa: E402


async def cmd_create(args: argparse.Namespace) -> int:
    db = LeadsDB()
    await db.init_schema()

    existing = await db.get_user_by_email(args.email)
    if existing:
        print(f"[!] A user with email '{args.email}' already exists (user_id={existing['user_id']}).")
        return 1

    password = args.password
    if not password:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm:  ")
        if password != confirm:
            print("[!] Passwords don't match. Aborting.")
            return 1
    if len(password) < 6:
        print("[!] Password must be at least 6 characters. Aborting.")
        return 1

    user_id = "u_" + secrets.token_urlsafe(10)
    await db.create_user(
        user_id=user_id,
        email=args.email,
        password_hash=hash_password(password),
        name=args.name or args.email,
        role=args.role,
    )
    print(f"[OK] Created user {args.email} (user_id={user_id}, role={args.role})")

    if args.assign:
        tenant_ids = [t.strip() for t in args.assign.split(",") if t.strip()]
        for tid in tenant_ids:
            tenant = await db.get_tenant(tid)
            if not tenant:
                # Auto-create the tenant row from the slug if missing — the engine
                # will fill in agent_id / voice_provider on the next call.
                await db.upsert_tenant(
                    tenant_id=tid,
                    name=tid.replace("_", " ").title(),
                )
            await db.assign_tenant_to_user(tid, user_id)
            print(f"  [OK] assigned tenant '{tid}' to {args.email}")
    return 0


async def cmd_list_users(_args: argparse.Namespace) -> int:
    db = LeadsDB()
    await db.init_schema()
    users = await db.list_users()
    if not users:
        print("(no users)")
        return 0
    print(f"{'user_id':<16} {'email':<32} {'name':<24} {'role':<10} {'created_at'}")
    print("-" * 100)
    for u in users:
        print(f"{u['user_id']:<16} {u['email']:<32} {(u.get('name') or ''):<24} {(u.get('role') or ''):<10} {u.get('created_at')}")
    return 0


async def cmd_list_tenants(_args: argparse.Namespace) -> int:
    db = LeadsDB()
    await db.init_schema()
    tenants = await db.list_tenants()
    if not tenants:
        print("(no tenants — make a call to register one, or assign via --assign)")
        return 0
    print(f"{'tenant_id':<20} {'owner_user_id':<16} {'name':<28} {'voice_provider'}")
    print("-" * 80)
    for t in tenants:
        print(f"{t['tenant_id']:<20} {(t.get('owner_user_id') or '-'):<16} {(t.get('name') or ''):<28} {(t.get('voice_provider') or '')}")
    return 0


async def cmd_assign(args: argparse.Namespace) -> int:
    db = LeadsDB()
    await db.init_schema()
    user = await db.get_user_by_email(args.email)
    if not user:
        print(f"[!] No user with email '{args.email}'.")
        return 1
    tenant_ids = [t.strip() for t in args.assign.split(",") if t.strip()]
    for tid in tenant_ids:
        tenant = await db.get_tenant(tid)
        if not tenant:
            await db.upsert_tenant(tenant_id=tid, name=tid.replace("_", " ").title())
        await db.assign_tenant_to_user(tid, user["user_id"])
        print(f"[OK] assigned '{tid}' to {args.email}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Create a Nexus user and assign businesses.")
    p.add_argument("--email")
    p.add_argument("--name")
    p.add_argument("--password", help="If omitted, prompts interactively (recommended).")
    p.add_argument("--role", default="owner", choices=("owner", "admin"))
    p.add_argument("--assign", help="Comma-separated tenant_ids to assign to this user.")
    p.add_argument("--list-users", action="store_true")
    p.add_argument("--list-tenants", action="store_true")
    p.add_argument("--assign-only", action="store_true",
                   help="Skip user creation; assign --assign to existing user by --email.")
    args = p.parse_args()

    if args.list_users:
        return asyncio.run(cmd_list_users(args))
    if args.list_tenants:
        return asyncio.run(cmd_list_tenants(args))

    if not args.email:
        p.error("--email is required (unless using --list-users / --list-tenants)")

    if args.assign_only:
        if not args.assign:
            p.error("--assign is required with --assign-only")
        return asyncio.run(cmd_assign(args))

    return asyncio.run(cmd_create(args))


if __name__ == "__main__":
    sys.exit(main())
