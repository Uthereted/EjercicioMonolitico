"""Refresh token rotation, expiry, logout and concurrent replay against SQLite."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/client'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['SQLALCHEMY_DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
from app.main import app
from app import auth_sessions
from app.events import Outbox
from app.dependencies import get_db
from app.sql import database, models, crud


class RefreshTokenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        gate = patch('service_logging.runtime.checkpoint', new_callable=AsyncMock)
        gate.start()
        self.addCleanup(gate.stop)
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

    async def test_registration_creates_client_credentials_and_event_atomically(self):
        data = {'username': 'customer', 'password': 'test-password', 'name': 'Customer',
                'email': 'customer@example.org', 'address': 'Calle Mayor 10', 'postal_code': '01001'}
        denied = await self.client.post('/auth/register', json={**data, 'roles': ['admin']})
        self.assertEqual(denied.status_code, 400)
        with patch.object(crud, 'record_event', side_effect=RuntimeError('outbox unavailable')):
            with self.assertRaises(RuntimeError):
                await self.client.post('/auth/register', json=data)
        async with self.sessions() as db:
            self.assertIsNone(await crud.get_user_by_username(db, data['username']))
            self.assertIsNone(await crud.get_client_by_email(db, data['email']))
        response = await self.client.post('/auth/register', json=data)
        self.assertEqual(response.status_code, 201, response.text)
        registered = response.json()
        self.assertEqual(registered['address'], data['address'])
        self.assertEqual(registered['postal_code'], '01001')
        self.assertNotIn('password', registered)
        login = await self.client.post('/auth/token', auth=(data['username'], data['password']))
        self.assertEqual(login.status_code, 200, login.text)
        profile = await self.client.get('/clients', headers={
            'Authorization': f"Bearer {login.json()['access_token']}"})
        self.assertEqual([c['id'] for c in profile.json()], [registered['id']])
        self.assertEqual(profile.json()[0], registered)
        detail = await self.client.get(f"/clients/{registered['id']}", headers={
            'Authorization': f"Bearer {login.json()['access_token']}"})
        self.assertEqual(detail.json(), registered)
        self.assertEqual((await self.client.post('/clients', json=data)).status_code, 405)
        duplicate = await self.client.post('/auth/register', json=data)
        self.assertEqual(duplicate.status_code, 409)
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(models.Client)), 1)
            payload = json.loads(await db.scalar(select(Outbox.payload).where(Outbox.event_type == 'client.created')))
            self.assertEqual(payload, {'client_id': registered['id'], 'address': data['address'], 'postal_code': '01001'})

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

    async def test_only_admin_can_delete_client_and_failure_rolls_back(self):
        data = {'username': 'delete-test', 'password': 'test-password', 'name': 'Delete test',
                'email': 'delete@example.org', 'address': 'Calle Mayor 10', 'postal_code': '01001'}
        created = (await self.client.post('/auth/register', json=data)).json()
        path = f"/clients/{created['id']}"
        self.assertEqual((await self.client.delete(path)).status_code, 401)
        user = (await self.client.post('/auth/token', auth=(data['username'], data['password']))).json()
        user_headers = {'Authorization': f"Bearer {user['access_token']}"}
        self.assertEqual((await self.client.delete(path, headers=user_headers)).status_code, 403)
        other = await self.client.get('/clients/999999', headers=user_headers)
        self.assertEqual(other.status_code, 403)
        admin = await self.login()
        headers = {'Authorization': f"Bearer {admin['access_token']}"}
        with patch.object(crud, 'record_event', side_effect=RuntimeError('outbox unavailable')):
            with self.assertRaises(RuntimeError):
                await self.client.delete(path, headers=headers)
        self.assertEqual((await self.client.get(path, headers=headers)).status_code, 200)
        response = await self.client.delete(path, headers=headers)
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b'')
        self.assertEqual((await self.client.get(path, headers=headers)).status_code, 404)
        self.assertEqual((await self.client.delete(path, headers=headers)).status_code, 404)
        self.assertEqual((await self.client.get('/clients', headers=headers)).json(), [])
        login = await self.client.post('/auth/token', auth=(data['username'], data['password']))
        self.assertEqual(login.status_code, 401)
        await self.refresh(user['refresh_token'], expected=401)
        async with self.sessions() as db:
            self.assertTrue((await db.get(models.Client, created['id'])).deleted)
            payload = json.loads(await db.scalar(select(Outbox.payload).where(Outbox.event_type == 'client.deleted')))
            self.assertEqual(payload, {'client_id': created['id']})

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
