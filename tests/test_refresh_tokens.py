"""Refresh token rotation, expiry, logout and concurrent replay against SQLite."""
import asyncio
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/client'))
os.environ['SQLALCHEMY_DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
from app.main import app
from app import auth_sessions
from app.dependencies import get_db
from app.sql import database, models, crud


class RefreshTokenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db_path = Path(self.temp.name) / 'refresh.db'
        self.engine = create_async_engine(f'sqlite+aiosqlite:///{db_path.as_posix()}')
        self.sessions = async_sessionmaker(self.engine, class_=AsyncSession)
        async with self.engine.begin() as connection:
            await connection.run_sync(models.Base.metadata.create_all)
        async with self.sessions() as db:
            await crud.seed_initial_data(db)

        async def session():
            async with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = session
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        await self.engine.dispose()
        await database.engine.dispose()
        self.temp.cleanup()

    async def login(self, method='POST'):
        response = await self.client.request(method, '/auth/token', auth=('admin', 'admin123'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        tokens = response.json()
        self.assertEqual(tokens['expires_in'], 3600)
        self.assertEqual(tokens['refresh_expires_in'], 7 * 86400)
        return tokens

    async def refresh(self, token, expected=200):
        response = await self.client.post('/auth/refresh', json={'refresh_token': token})
        self.assertEqual(response.status_code, expected, response.text)
        return response

    async def test_refresh_rotates_pair_without_extending_session_expiry(self):
        original = await self.login()
        renewed = (await self.refresh(original['refresh_token'])).json()
        self.assertNotEqual(renewed['refresh_token'], original['refresh_token'])
        self.assertNotEqual(renewed['access_token'], original['access_token'])
        async with self.sessions() as db:
            rows = list((await db.scalars(select(models.RefreshToken))).all())
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].family_id, rows[1].family_id)
            self.assertEqual(rows[0].expires_at, rows[1].expires_at)
        response = await self.client.get('/clients', headers={
            'Authorization': f"Bearer {renewed['access_token']}",
        })
        self.assertEqual(response.status_code, 200)

    async def test_replay_revokes_session_but_leaves_other_login_valid(self):
        first, other = await self.login(), await self.login()
        rotated = (await self.refresh(first['refresh_token'])).json()
        await self.refresh(first['refresh_token'], expected=401)
        await self.refresh(rotated['refresh_token'], expected=401)
        await self.refresh(other['refresh_token'])

    async def test_expired_unknown_and_access_tokens_cannot_refresh(self):
        tokens = await self.login()
        async with self.sessions() as db:
            await db.execute(update(models.RefreshToken).values(
                expires_at=auth_sessions.utc_now() - timedelta(seconds=1)))
            await db.commit()
        for token in (tokens['refresh_token'], tokens['access_token'], 'unknown-token'):
            await self.refresh(token, expected=401)
        response = await self.client.post('/auth/refresh', json={})
        self.assertEqual(response.status_code, 422)

    async def test_logout_revokes_family_and_is_idempotent(self):
        tokens, other = await self.login(), await self.login()
        rotated = (await self.refresh(tokens['refresh_token'])).json()
        for _ in range(2):
            response = await self.client.post('/auth/logout', json={'refresh_token': rotated['refresh_token']})
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response.content, b'')
        await self.refresh(rotated['refresh_token'], expected=401)
        await self.refresh(tokens['refresh_token'], expected=401)
        await self.refresh(other['refresh_token'])

    async def test_parallel_use_claims_once_and_replay_revokes_winner(self):
        tokens = await self.login()
        responses = await asyncio.gather(*(
            self.client.post('/auth/refresh', json={'refresh_token': tokens['refresh_token']})
            for _ in range(2)
        ))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 401])
        winner = next(response.json() for response in responses if response.status_code == 200)
        await self.refresh(winner['refresh_token'], expected=401)

if __name__ == '__main__':
    unittest.main()
