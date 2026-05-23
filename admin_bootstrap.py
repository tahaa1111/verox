"""One-shot script: insert bootstrap admin row if not present.
Run as a Cloud Run Job using the medibox-api image.
"""
import os
import asyncio
import asyncpg


async def run():
    host = os.environ.get("DB_HOST", "127.0.0.1")
    user = os.environ.get("DB_USER", "medibox")
    password = os.environ["DB_PASSWORD"]
    name = os.environ.get("DB_NAME", "medibox")

    if host.startswith("/"):
        # Cloud SQL Unix socket
        dsn = f"postgresql://{user}:{password}@/{name}?host={host}"
    else:
        port = os.environ.get("DB_PORT", "5432")
        dsn = f"postgresql://{user}:{password}@{host}:{port}/{name}"

    conn = await asyncpg.connect(dsn)

    result = await conn.execute(
        """
        INSERT INTO admin_role_grants (user_id, granted_by, notes)
        SELECT $1, $2, $3
        WHERE NOT EXISTS (
            SELECT 1 FROM admin_role_grants
            WHERE user_id = $1 AND revoked_at IS NULL
        )
        """,
        "2UzPsPp4npTqlvjztL7B5Ja4PW72",
        "system-bootstrap",
        "Initial admin for guesmitaha96@gmail.com",
    )
    print("INSERT result:", result)

    rows = await conn.fetch("SELECT id, user_id, granted_by, granted_at FROM admin_role_grants")
    for r in rows:
        print("Admin row:", dict(r))

    await conn.close()
    print("Bootstrap complete.")


asyncio.run(run())
