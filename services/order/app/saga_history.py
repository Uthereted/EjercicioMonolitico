"""Record coordinator transitions in its transactional outbox; read their MongoDB history."""
import asyncio
from datetime import datetime, timezone
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo import MongoClient, DESCENDING
from sqlalchemy import select

from .auth_verifier import verify_jwt
from .events import record_event
from .sql import models

State = Literal['PendingPaymentAndDelivery', 'PendingPayment', 'PendingDelivery',
                'PendingConfirmation', 'Approved', 'AwaitingCompensation',
                'PendingPaymentRelease', 'PendingDeliveryCancellation', 'Suspended']
EVENT_TYPE = 'order.saga.state_changed'

mongo = MongoClient(os.getenv('MONGODB_URL', 'mongodb://mongodb:27017'),
                    serverSelectionTimeoutMS=2000, socketTimeoutMS=2000,
                    connectTimeoutMS=2000, tz_aware=True)
history = mongo[os.getenv('AUDIT_DATABASE', 'manufacturing_audit')]['saga_history']


async def record_state(db, saga, *, previous_state=None, initial_snapshot=False):
    saga.history_version += 1
    await record_event(db, EVENT_TYPE, {
        'saga_id': saga.id, 'order_id': saga.order_id, 'state': saga.state,
        'previous_state': previous_state, 'sequence': saga.history_version,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'initial_snapshot': initial_snapshot,
    }, f'{EVENT_TYPE}:{saga.id}:{saga.history_version}')


async def change_state(db, saga, state):
    if saga.state == state:
        return
    previous = saga.state
    saga.state = state
    await record_state(db, saga, previous_state=previous)


async def snapshot_existing(db):
    # Snapshot only the current state; do not invent transitions predating this feature.
    sagas = await db.scalars(select(models.OrderSaga).where(models.OrderSaga.history_version == 0))
    for saga in sagas:
        await record_state(db, saga, initial_snapshot=True)


async def require_admin(payload=Depends(verify_jwt)):
    if 'admin' not in payload.get('roles', []):
        raise HTTPException(403, 'Administrator role required')
    return payload


def filters(order_id, state, date_from, date_to):
    for value in (date_from, date_to):
        if value is not None and value.tzinfo is None:
            raise HTTPException(422, 'Dates must include a timezone')
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, 'date_from must be before date_to')
    query = {}
    if order_id is not None:
        query['order_id'] = order_id
    if state:
        query['state'] = state
    if date_from or date_to:
        query['timestamp'] = {}
        if date_from:
            query['timestamp']['$gte'] = date_from
        if date_to:
            query['timestamp']['$lte'] = date_to
    return query


def serialize(items):
    for item in items:
        item['history_id'] = item.pop('_id')
    return items


async def read_history(query, limit, offset, *, latest=False, chronological=False):
    def read():
        if latest:
            # Select the actual latest transition first, then filter that state.
            # An old matching state must not masquerade as the current one.
            result = list(history.aggregate([
                {'$sort': {'saga_id': 1, 'sequence': -1}},
                {'$group': {'_id': '$saga_id', 'entry': {'$first': '$$ROOT'}}},
                {'$replaceRoot': {'newRoot': '$entry'}},
                {'$match': query},
                {'$facet': {
                    'items': [{'$sort': {'timestamp': -1, 'saga_id': -1}},
                              {'$skip': offset}, {'$limit': limit}],
                    'count': [{'$count': 'total'}],
                }},
            ]))[0]
            return {'items': serialize(result['items']),
                    'total': result['count'][0]['total'] if result['count'] else 0,
                    'limit': limit, 'offset': offset}
        sort = [('sequence', 1)] if chronological else [('timestamp', DESCENDING), ('_id', DESCENDING)]
        items = list(history.find(query).sort(sort).skip(offset).limit(limit))
        return {'items': serialize(items), 'total': history.count_documents(query),
                'limit': limit, 'offset': offset}

    try:
        return await asyncio.to_thread(read)
    except Exception as exc:
        raise HTTPException(503, 'Saga history database unavailable') from exc


router = APIRouter(prefix='/order', tags=['Saga History'], dependencies=[Depends(require_admin)])


@router.get('/sagas/history')
async def all_history(order_id: int | None = Query(None, gt=0), state: State | None = None,
                      date_from: datetime | None = None, date_to: datetime | None = None,
                      limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return await read_history(filters(order_id, state, date_from, date_to), limit, offset)


@router.get('/sagas/latest')
async def latest_states(order_id: int | None = Query(None, gt=0), state: State | None = None,
                        date_from: datetime | None = None, date_to: datetime | None = None,
                        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return await read_history(filters(order_id, state, date_from, date_to), limit, offset, latest=True)


@router.get('/{order_id}/saga/history')
async def order_history(order_id: int, state: State | None = None,
                        date_from: datetime | None = None, date_to: datetime | None = None,
                        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    if order_id <= 0:
        raise HTTPException(422, 'order_id must be positive')
    return await read_history(filters(order_id, state, date_from, date_to), limit, offset,
                              chronological=True)
