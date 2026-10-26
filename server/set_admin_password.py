"""Run locally: .venv\Scripts\python.exe -m server.set_admin_password"""
import asyncio
import getpass

from server.accounts import initialize, password_hash
from server.config import ROOT
from server.database import Database


async def main():
    password = getpass.getpass('Set admin password (at least 10 characters): ')
    if len(password) < 10 or len(password) > 256:
        raise SystemExit('Password must contain 10–256 characters.')
    if password != getpass.getpass('Confirm password: '):
        raise SystemExit('Passwords do not match.')
    directory = ROOT / 'server' / 'data'
    directory.mkdir(parents=True, exist_ok=True)
    db = Database(directory / 'app.db')
    await db.initialize()
    await initialize(db)
    async with db.connect() as conn:
        await conn.execute("UPDATE users SET password_hash=?,disabled=0,deleted=0 WHERE id='admin'", (password_hash(password),))
        await conn.execute("DELETE FROM sessions WHERE user_id='admin'")
    print('Administrator: admin. Password saved as a salted scrypt hash. Restart backend to close existing connections.')


if __name__ == '__main__':
    asyncio.run(main())
