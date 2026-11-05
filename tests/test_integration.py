"""Run the essential live checks. Add --all for recovery and MongoDB audit.

Requires Docker Compose. Test records are retained; stopped services are restored.
"""
import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def compose(*args):
    return subprocess.check_output(['docker', 'compose', *args], cwd=ROOT, text=True)


def wait_for(check, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise AssertionError('Timed out waiting for asynchronous processing')


def documents(order_id):
    query = json.dumps({'data.order_id': order_id})
    expression = 'JSON.stringify(db.getSiblingDB("manufacturing_audit").business_events.find(' + query + ').toArray())'
    return json.loads(compose('exec', '-T', 'mongodb', 'mongosh', '--quiet', '--eval', expression))


def replay_saga(order_id):
    code = '''import asyncio, json, os, sys
import aio_pika
from pymongo import MongoClient
from my_chassis.messaging import TopicTransport
collection = MongoClient(os.environ["MONGODB_URL"])["manufacturing_audit"]["business_events"]
async def publish():
    for topic, exchange_name in (("saga.payment.reserve", "manufacturing.commands"),
                                 ("saga.payment.confirmed", "manufacturing.saga.responses")):
        event = collection.find_one({"event_type": topic, "data.order_id": int(sys.argv[1])})
        assert event is not None, topic
        payload = {"event_id": event["_id"], "event_type": topic,
                   "producer": event["producer"], "data": event["data"]}
        transport = TopicTransport(os.environ["RABBITMQ_URL"], exchange_name)
        async with await transport.connect() as connection:
            channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
            exchange = await transport.provision(channel)
            for _ in range(2):
                await exchange.publish(aio_pika.Message(body=json.dumps(payload).encode(),
                    content_type="application/json", message_id=payload["event_id"],
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT), routing_key=topic, mandatory=True)
asyncio.run(publish())
'''
    compose('exec', '-T', 'event-logger', 'python', '-c', code, str(order_id))


def run(all_checks=False):
    with httpx.Client(base_url=os.getenv('API_BASE_URL', 'http://localhost:8080'), timeout=10) as client:
        def request(method, path, expected=None, **kwargs):
            deadline = time.monotonic() + 10
            while True:
                response = client.request(method, path, **kwargs)
                pending = method == 'POST' and (
                    response.status_code == 404 and (path == '/api/orders' or path.endswith('/deposit'))
                    or response.status_code == 409 and path == '/api/orders'
                    and response.json().get('detail') == 'Client address is not present in the local catalogue yet')
                if not pending or time.monotonic() >= deadline:
                    break
                time.sleep(0.2)
            if expected is None:
                response.raise_for_status()
            else:
                assert response.status_code == expected, (path, response.status_code, response.text)
            return response.json() if response.content else None

        def new_order(funds=100, postal_code='01001', pieces=1):
            customer = request('POST', '/api/auth/register', expected=201, json={
                'username': f'client-{uuid.uuid4().hex}', 'password': uuid.uuid4().hex,
                'name': 'Saga test', 'email': f'saga-{uuid.uuid4().hex}@example.org',
                'address': 'Calle Mayor 10', 'postal_code': postal_code})
            if funds:
                request('POST', f"/api/payments/{customer['id']}/deposit", json={'amount': funds})
            order = request('POST', '/api/orders', expected=202, json={
                'client_id': customer['id'], 'number_of_pieces': pieces})
            assert order['status'] == 'PendingPayment', order
            assert order['address'] == f'Calle Mayor 10, {postal_code}', order
            return customer, order

        def wait_order(order_id, status):
            def check():
                order = request('GET', f'/api/orders/{order_id}')
                if status != 'Suspended':
                    assert order['status'] not in ('Compensating', 'Suspended', 'Rejected'), order
                return order if order['status'] == status else None
            return wait_for(check)

        def balance(customer):
            return request('GET', f"/api/payments/{customer['id']}/balance")['balance']

        def finish(customer, order):
            ready = wait_order(order['id'], 'ReadyForDelivery')
            assert ready['payment_completed'] and ready['integration_error'] is None, ready
            pieces = request('GET', f"/api/orders/{order['id']}/pieces")
            assert len(pieces) == order['number_of_pieces'] and all(p['status'] == 'Manufactured' for p in pieces), pieces
            request('PATCH', f"/api/deliveries/{ready['delivery_id']}/status", json={'status': 'Delivered'})
            wait_order(order['id'], 'Delivered')
            assert balance(customer) == 100 - order['total_price']
            print(f"PASS delivered: order={order['id']}", flush=True)

        wait_for(lambda: request('GET', '/api/auth/public-key'))
        request('GET', '/api/orders', expected=401)
        request('POST', '/api/auth/token', auth=('admin', 'incorrect'), expected=401)
        token = request('POST', '/api/auth/token', auth=(
            os.getenv('TEST_AUTH_USERNAME', 'admin'), os.getenv('TEST_AUTH_PASSWORD', 'admin123')))
        client.headers['Authorization'] = f"Bearer {token['access_token']}"
        request('POST', '/api/clients', expected=405, json={})

        customer, order = new_order(pieces=2)
        request('POST', f"/api/payments/{customer['id']}/charge", expected=409,
                json={'order_id': order['id'], 'amount': order['total_price']})
        request('POST', '/api/deliveries', expected=409, json={
            'order_id': order['id'], 'client_id': customer['id'], 'address': order['address']})
        request('PATCH', f"/api/orders/{order['id']}/status", expected=409, json={'status': 'Finished'})
        finish(customer, order)
        request('DELETE', f"/api/orders/{order['id']}", expected=409)

        username, password = uuid.uuid4().hex, uuid.uuid4().hex
        registered = request('POST', '/api/auth/register', expected=201, json={
            'username': username, 'password': password, 'email': f'{username}@example.org',
            'name': 'Registered client', 'address': 'Calle Mayor 10', 'postal_code': '01001'})
        assert registered['address'] == 'Calle Mayor 10' and registered['postal_code'] == '01001', registered
        user = request('POST', '/api/auth/token', auth=(username, password))
        refreshed = request('POST', '/api/auth/refresh', json={'refresh_token': user['refresh_token']})
        headers = {'Authorization': f"Bearer {refreshed['access_token']}"}
        assert [c['id'] for c in request('GET', '/api/clients', headers=headers)] == [registered['id']]
        assert request('GET', f"/api/clients/{registered['id']}", headers=headers) == registered
        request('DELETE', f"/api/clients/{registered['id']}", expected=403, headers=headers)
        request('POST', f"/api/payments/{customer['id'] + 1000000}/deposit", expected=403,
                headers=headers, json={'amount': 10})
        request('POST', '/api/auth/logout', expected=204, json={'refresh_token': refreshed['refresh_token']})
        request('POST', '/api/auth/refresh', expected=401, json={'refresh_token': refreshed['refresh_token']})
        request('DELETE', f"/api/clients/{registered['id']}", expected=204)
        request('GET', f"/api/clients/{registered['id']}", expected=404)
        request('POST', '/api/auth/token', expected=401, auth=(username, password))
        print('PASS authentication and ownership', flush=True)

        for funds, postal_code in ((0, '01001'), (100, '28001')):
            customer, order = new_order(funds, postal_code)
            suspended = wait_order(order['id'], 'Suspended')
            assert not suspended['payment_completed'] and suspended['integration_error'], suspended
            assert balance(customer) == funds
            delivery = request('GET', f"/api/deliveries/{suspended['delivery_id']}")
            assert delivery['status'] == 'Canceled', delivery
            assert all(p['status'] == 'Queued' for p in request('GET', f"/api/orders/{order['id']}/pieces"))
            print(f"PASS compensated: order={order['id']}, balance={funds}", flush=True)

        if not all_checks:
            return

        compose('stop', 'delivery')
        try:
            customer, order = new_order()
            wait_for(lambda: balance(customer) == 90)
            pending = request('GET', f"/api/orders/{order['id']}")
            assert not pending['payment_completed'] and pending['delivery_id'] is None, pending
            pieces = request('GET', f"/api/orders/{order['id']}/pieces")
            assert pieces[0]['status'] == 'Queued', pieces
            compose('start', 'delivery')
            wait_for(lambda: request('GET', '/api/machine/status')['working_piece'] == pieces[0]['id'])
            compose('restart', 'machineservice')
            finish(customer, order)
            deliveries = request('GET', '/api/deliveries')
            assert len([d for d in deliveries if d['order_id'] == order['id']]) == 1
            print('PASS recovery after delivery outage and machine restart', flush=True)
        finally:
            compose('start', 'delivery')

        compose('stop', 'event-logger')
        try:
            customer, order = new_order()
            finish(customer, order)
            assert documents(order['id']) == []
            compose('start', 'event-logger')
            expected = {'order.paid', 'piece.manufacturing.started', 'piece.manufactured',
                        'order.manufactured', 'delivery.created', 'delivery.ready', 'delivery.delivered',
                        'saga.payment.reserve', 'saga.payment.reserved', 'saga.delivery.create',
                        'saga.delivery.accepted', 'saga.payment.confirm', 'saga.payment.confirmed'}
            wait_for(lambda: expected.issubset({row['event_type'] for row in documents(order['id'])}))
            before = documents(order['id'])
            assert len(before) == len(expected), before
            replay_saga(order['id'])
            time.sleep(2)
            assert len(documents(order['id'])) == len(before)
            assert balance(customer) == 90
            print('PASS audit: 13 unique messages, replay without duplicate effects', flush=True)
        finally:
            compose('start', 'event-logger')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all', action='store_true', help='Also stop services temporarily to test recovery and audit')
    run(parser.parse_args().all)
