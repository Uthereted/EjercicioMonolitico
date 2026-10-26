"""Complete an order with the audit consumer offline, then verify MongoDB catch-up."""
import json
import os
import subprocess
import time
import uuid

import httpx
from test_event_recovery_integration import ROOT, compose, wait_for


def documents(order_id):
    expression = ('JSON.stringify(db.getSiblingDB("manufacturing_audit").business_events.find('
                  + json.dumps({'data.order_id': order_id}) + ').toArray())')
    output = subprocess.check_output(['docker', 'compose', 'exec', '-T', 'mongodb', 'mongosh',
                                      '--quiet', '--eval', expression], cwd=ROOT, text=True)
    return json.loads(output)


def replay_creation(order_id):
    code = '''import asyncio, json, os, sys
import aio_pika
from pymongo import MongoClient
from my_chassis.messaging import TopicTransport
event = MongoClient(os.environ["MONGODB_URL"])["manufacturing_audit"]["business_events"].find_one(
    {"_id": "order.created:" + sys.argv[1]})
payload = {"event_id": event["_id"], "event_type": event["event_type"],
           "producer": event["producer"], "data": event["data"]}
async def publish():
    transport = TopicTransport(os.environ["RABBITMQ_URL"], os.environ["RABBITMQ_EXCHANGE"])
    async with await transport.connect() as connection:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await transport.provision(channel)
        for _ in range(2):
            await exchange.publish(aio_pika.Message(body=json.dumps(payload).encode(),
                content_type="application/json", message_id=payload["event_id"],
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
                routing_key=payload["event_type"], mandatory=True, timeout=5)
asyncio.run(publish())
'''
    subprocess.run(['docker', 'compose', 'exec', '-T', 'event-logger', 'python', '-c', code,
                    str(order_id)], cwd=ROOT, check=True)


def run():
    with httpx.Client(base_url=os.getenv('API_BASE_URL', 'http://localhost:8080'), timeout=10) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        token = request('POST', '/api/auth/token', auth=(
            os.getenv('TEST_AUTH_USERNAME', 'admin'), os.getenv('TEST_AUTH_PASSWORD', 'admin123')))
        client.headers['Authorization'] = f"Bearer {token['access_token']}"
        compose('stop', 'event-logger')
        audit_stopped = True
        try:
            customer = request('POST', '/api/clients', json={
                'name': 'Audit integration', 'email': f'audit-{uuid.uuid4().hex}@example.org'})
            wait_for(lambda: request('POST', f"/api/payments/{customer['id']}/deposit", json={'amount': 100}))
            order = wait_for(lambda: request('POST', '/api/orders', json={
                'client_id': customer['id'], 'number_of_pieces': 1, 'address': 'Madrid'}))
            order_id = order['id']
            wait_for(lambda: request('GET', f'/api/orders/{order_id}')['status'] == 'ReadyForDelivery')
            order = request('GET', f'/api/orders/{order_id}')
            request('PATCH', f"/api/deliveries/{order['delivery_id']}/status", json={'status': 'Delivered'})
            wait_for(lambda: request('GET', f'/api/orders/{order_id}')['status'] == 'Delivered')
            assert documents(order_id) == [], 'Audit should still be offline'
            print(f'Order {order_id} completed while the audit consumer was offline', flush=True)
            compose('start', 'event-logger')
            audit_stopped = False
            expected = {'order.created', 'payment.approved', 'order.paid', 'piece.manufacturing.started',
                        'piece.manufactured', 'order.manufactured', 'delivery.created',
                        'delivery.ready', 'delivery.delivered'}
            wait_for(lambda: expected.issubset({row['event_type'] for row in documents(order_id)}))
            before = documents(order_id)
            assert len(before) == len(expected), before
            assert all(row['producer'] != 'unknown' and 'received_at' in row for row in before)
            replay_creation(order_id)
            time.sleep(2)
            after = documents(order_id)
            assert len(after) == len(before), after
            balance = request('GET', f"/api/payments/{customer['id']}/balance")
            assert balance['balance'] == 90, balance
            print(f'PASS MongoDB audit: order={order_id}, {len(after)} unique facts, replay deduplicated, balance=90', flush=True)
        finally:
            if audit_stopped:
                compose('start', 'event-logger')


if __name__ == '__main__':
    run()
