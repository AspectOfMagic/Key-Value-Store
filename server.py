from flask import Flask, request, jsonify
import requests
import hashlib
import time
import os
import re

app = Flask(__name__)

socket_address = None

shard_count = None

my_shard_id = None

N = 64

view = dict()

key_val_store = dict()

ring = [None] * N

shards = dict()

def custom_hash(s):
    sha256 = hashlib.sha256()
    sha256.update(s.encode('utf-8'))
    return int(sha256.hexdigest(), 16) % N

def create_shards():
    shard_id = 'a'
    interval = int((N - 1) / shard_count)
    index_to_insert = 0
    for _ in range(shard_count):
        ring[index_to_insert] = shard_id
        shards[shard_id] = []
        shard_id = chr(ord(shard_id) + 1)
        index_to_insert += interval

def assign_shards():
    replicas_per_shard = int(len(view) / shard_count)
    assigned_shard_id = None
    for address in view.keys():
        shard_assigned = False
        for shard_id in shards.keys():
            if len(shards[shard_id]) < replicas_per_shard:
                shards[shard_id].append(address)
                assigned_shard_id = shard_id
                shard_assigned = True
                break
        if not shard_assigned:
            shards[assigned_shard_id].append(address)
        if socket_address == address:
            global my_shard_id
            my_shard_id = assigned_shard_id

def find_shard(key):
    index_on_ring = custom_hash(key)
    shard_id = None
    for i in range(index_on_ring, N):
        if ring[i] != None:
            shard_id = ring[i]
            break
    if shard_id == None:
        for j in range(index_on_ring):
            if ring[j] != None:
                shard_id = ring[j]
                break
    return shard_id

def forward_request(method, shard_id, path, data = None):
    if shard_id not in shards:
        return jsonify({'error': 'shard_id not found'}), 404
    for address in shards[shard_id]:
        try:
            if '/kvs' in path:
                response = requests.request(method, f'http://{address}{path}', json = data, 
                                            headers = {'Content-Type': 'application/json'})
            else:
                response = requests.request(method, f'http://{address}{path}', json = data, 
                                            headers = {'Content-Type': 'application/json'}, 
                                            timeout = 1)
            return jsonify(response.json()), response.status_code
        except:
            pass
    return jsonify({'error': 'shard contains no working replica'}), 404

def broadcast(method, path, data = None):
    for address in view.keys():
        if address == socket_address:
            continue
        try:
            if '/shard' in path:
                _ = requests.request(method, f'http://{address}{path}', json = data, 
                                     headers = {'Content-Type': 'application/json'})
            else:
                _ = requests.request(method, f'http://{address}{path}', json = data, 
                                     headers = {'Content-Type': 'application/json'},
                                     timeout = 1)
        except:
            pass

def broadcast_kvs(method, path, data = None):
    crashed_replicas = list()
    for address in shards[my_shard_id]:
        if address == socket_address:
            continue
        url = f'http://{address}{path}'
        try:
            response =  requests.request(method, url, json = data, 
                                         headers = {'Content-Type': 'application/json'}, 
                                         timeout = 1)
            while response.status_code == 503:
                time.sleep(1)
                response =  requests.request(method, url, json = data, 
                                             headers = {'Content-Type': 'application/json'}, 
                                             timeout = 1)
        except:
            crashed_replicas.append(address)
    for a1 in crashed_replicas:
        shards[my_shard_id].remove(a1)
        del view[a1]
    for a2 in crashed_replicas:
        broadcast('DELETE', '/view', {'socket-address': a2, 'sender-address': socket_address})
    for a in view.keys():
        if a in shards[my_shard_id]:
            continue
        try:
            _ = requests.request('PUT', f'http://{a}/sync', 
                                 json = {'causal-metadata': view}, 
                                 headers = {'Content-Type': 'application/json'}, 
                                 timeout = 1)
        except:
            pass

def state_retrieval():
     for address in view.keys():
        if address == socket_address:
            continue
        try:
            response =  requests.request('GET', f'http://{address}/sync/state', 
                                         headers = {'Content-Type': 'application/json'}, 
                                         timeout = 1)
            global ring, shards
            json_body = response.json()
            ring = json_body.get('ring')
            shards = json_body.get('shards')
            break
        except:
            pass

def data_retrieval(shard_id):
    global my_shard_id
    my_shard_id = shard_id
    existing_kvs = dict()
    first_time = True
    for address in shards[my_shard_id]:
        try:
            response =  requests.request('GET', f'http://{address}/sync', 
                                         headers = {'Content-Type': 'application/json'}, 
                                         timeout = 1)
            json_body = response.json()
            foreign_view = json_body.get('causal-metadata')
            view_change = False
            if first_time:
                view_change = True
            else:
                for a in foreign_view.keys():
                    if foreign_view[a] > view[a]:
                        view[a] = foreign_view[a]
                        view_change = True
            if view_change:
                existing_kvs = json_body.get('key-value-store')
        except:
            pass
    for k in existing_kvs.keys():
        key_val_store[k] = existing_kvs[k]

def put_view():
    json_body = request.get_json(silent = True)
    if json_body == None:
        return jsonify({'error': 'view operation does not specify an address'}), 400
    address = json_body.get('socket-address')
    if address == None:
        return jsonify({'error': 'view operation does not specify an address'}), 400
    if view.get(address) != None:
        return jsonify({'result': 'already present'}), 200
    else:
        view[address] = 0
        return jsonify({'result': 'added'}), 201

def get_view():
    return jsonify({'view': list(view.keys())}), 200

def delete_view():
    json_body = request.get_json(silent = True)
    if json_body == None:
        return jsonify({'error': 'view operation does not specify an address'}), 400
    address = json_body.get('socket-address')
    if address == None:
        return jsonify({'error': 'view operation does not specify an address'}), 400
    if view.get(address) != None:
        for shard_id in shards.keys():
            if address in shards[shard_id]:
                shards[shard_id].remove(address)
        del view[address]
        return jsonify({'result': 'deleted'}), 200
    else:
        return jsonify({'error': 'View has no such replica'}), 404

def put_kvs(key):
    json_body = request.get_json(silent = True)
    if json_body == None:
        return jsonify({'error': 'JSON payload missing'}), 400
    if len(key) > 50:
        return jsonify({'error': 'Key is too long'}), 400
    val = json_body.get('value')
    if val == None:
        return jsonify({'error': 'PUT request does not specify a value'}), 400
    causal_metadata = json_body.get('causal-metadata')
    if causal_metadata == None:
        view[socket_address] += 1
        broadcast_kvs('PUT', f'/kvs/{key}', {'value': val, 'causal-metadata': view, 'sender-address': socket_address})
    else:
        sender = json_body.get('sender-address')
        if sender == None:
            for a1 in causal_metadata.keys():
                if causal_metadata.get(a1) > view.get(a1):
                    return jsonify({'error': 'Causal dependencies not satisfied; try again later'}), 503
            view[socket_address] += 1
            broadcast_kvs('PUT', f'/kvs/{key}', {'value': val, 'causal-metadata': view, 'sender-address': socket_address})
        else:
            for a2 in causal_metadata.keys():
                if a2 == sender:
                    if causal_metadata.get(a2) != view.get(a2) + 1:
                        return jsonify({'error': 'Causal dependencies not satisfied; try again later'}), 503
                else:
                    if causal_metadata.get(a2) > view.get(a2):
                        return jsonify({'error': 'Causal dependencies not satisfied; try again later'}), 503
            view[sender] += 1           
    if key_val_store.get(key) == None:
        key_val_store[key] = val
        return jsonify({'result': 'created', "causal-metadata": view}), 201
    else:
        key_val_store[key] = val
        return jsonify({'result': 'replaced', "causal-metadata": view}), 200

def get_kvs(key):
    json_body = request.get_json(silent = True)
    if json_body == None:
        return jsonify({'error': 'JSON payload missing'}), 400
    causal_metadata = json_body.get('causal-metadata')
    if causal_metadata != None:
        for a in causal_metadata.keys():
            if causal_metadata.get(a) > view.get(a):
                return jsonify({'error': 'Causal dependencies not satisfied; try again later'}), 503
    if key_val_store.get(key) == None:
        return jsonify({'error': 'Key does not exist'}), 404
    return jsonify({'result': 'found', 'value': key_val_store[key], 'causal-metadata': view}), 200

def delete_kvs(key):
    json_body = request.get_json(silent = True)
    if json_body == None:
        return jsonify({'error': 'JSON payload missing'}), 400
    causal_metadata = json_body.get('causal-metadata')
    if causal_metadata == None:
        view[socket_address] += 1
        broadcast_kvs('DELETE', f'/kvs/{key}', {'causal-metadata': view, 'sender-address': socket_address})
    else:
        sender = json_body.get('sender-address')
        if sender == None:
            for a1 in causal_metadata.keys():
                if causal_metadata.get(a1) > view.get(a1):
                    return jsonify({'error': 'Causal dependencies not satisfied; try again later'}), 503
            view[socket_address] += 1
            broadcast_kvs('DELETE', f'/kvs/{key}', {'causal-metadata': view, 'sender-address': socket_address})
        else:
            for a2 in causal_metadata.keys():
                if a2 == sender:
                    if causal_metadata.get(a2) != view.get(a2) + 1:
                        return jsonify({'error': 'Causal dependencies not satisfied; try again later'}), 503
                else:
                    if causal_metadata.get(a2) > view.get(a2):
                        return jsonify({'error': 'Causal dependencies not satisfied; try again later'}), 503
            view[sender] += 1
    if key_val_store.get(key) == None:
        return jsonify({'error': 'Key does not exist'}), 404
    del key_val_store[key]
    return jsonify({'result': 'deleted', 'causal-metadata': view}), 200

@app.route('/view', methods=['PUT', 'GET', 'DELETE'])
def view_request_handler():
    if request.method == 'PUT':
        return put_view()
    elif request.method == 'GET':
        return get_view()
    else:
        return delete_view()

@app.route('/kvs/<key>', methods=['PUT', 'GET', 'DELETE'])
def kvs_request_handler(key):
    shard_id = find_shard(key)
    if shard_id != my_shard_id:
        json_body = request.get_json(silent = True)
        return forward_request(request.method, shard_id, f'/kvs/{key}', json_body)
    elif request.method == 'PUT':
        return put_kvs(key)
    elif request.method == 'GET':
        return get_kvs(key)
    else:
        return delete_kvs(key)
    
@app.route('/shard/ids', methods=['GET'])
def shard_ids():
    return jsonify({'shard-ids': list(shards.keys())}), 200

@app.route('/shard/node-shard-id', methods=['GET'])
def node_shard_id():
    return jsonify({'node-shard-id': my_shard_id}), 200

@app.route('/shard/members/<ID>', methods=['GET'])
def find_members(ID):
    if ID in shards.keys():
        return jsonify({'shard-members': shards[ID]}), 200
    return jsonify({'error': 'shard not found'}), 404

@app.route('/shard/key-count/<ID>', methods=['GET'])
def shard_key_count(ID):
    if ID in shards.keys():
        if my_shard_id == ID:
            return jsonify({'shard-key-count': len(key_val_store)}), 200
        else:
            return forward_request('GET', ID, f'/shard/key-count/{ID}')
    return jsonify({'error': 'shard not found'}), 404

@app.route('/shard/add-member/<ID>', methods=['PUT'])
def shard_add_member(ID):
    json_body = request.get_json(silent = True)
    address = json_body.get('socket-address')
    sender = json_body.get('sender-address')
    if (ID in shards.keys()) and (address in view.keys()):
        if sender == None:
             broadcast('PUT', f'/shard/add-member/{ID}', 
                       {'socket-address': address, 'sender-address': socket_address})
        if socket_address == address:
            data_retrieval(ID)
        shards[ID].append(address)
        return jsonify({'result': 'node added to shard'}), 200
    return jsonify({'error': 'shard not found'}), 404

@app.route('/shard/reshard', methods=['PUT'])
def reshard():
    json_body = request.get_json(silent = True)
    num_shards =  json_body.get('shard-count')
    if int(len(view) / num_shards) < 2:
        return jsonify({'error': 'Not enough nodes to provide fault tolerance with requested shard count'}), 400
    global shard_count, ring, shards
    old_neighbors = shards[my_shard_id].copy()
    shard_count = num_shards
    ring = [None] * N
    shards = dict()
    create_shards()
    assign_shards()
    keys_to_delete = list()
    for key in key_val_store.keys():
        shard_id = find_shard(key)
        if shard_id != my_shard_id:
            for address in shards[shard_id]:
                if (old_neighbors != None) and (address in old_neighbors):
                    continue
                try:
                    _ = requests.request('PUT', f'http://{address}/merge/{key}', 
                                         json = {'value': key_val_store[key]}, 
                                         headers = {'Content-Type': 'application/json'}, 
                                         timeout = 1)
                except:
                    pass
            keys_to_delete.append(key)
        else:
            for address in shards[my_shard_id]:
                if (old_neighbors != None) and (address in old_neighbors):
                    continue
                try:
                    _ = requests.request('PUT', f'http://{address}/merge/{key}', 
                                         json = {'value': key_val_store[key]}, 
                                         headers = {'Content-Type': 'application/json'}, 
                                         timeout = 1)
                except:
                    pass
    for k in keys_to_delete:
        del key_val_store[k]
    sender = json_body.get('sender-address')
    if sender == None:
        broadcast('PUT', '/shard/reshard', {'shard-count': num_shards, 'sender-address': socket_address})
    return jsonify({'result': 'resharded'}), 200

@app.route('/sync', methods=['PUT', 'GET'])
def sync_request_handler():
    if request.method == 'PUT':
        json_body = request.get_json(silent = True)
        foreign_view = json_body.get('causal-metadata')
        for address in foreign_view.keys():
            if foreign_view[address] > view[address]:
                view[address] = foreign_view[address]
        return jsonify({'success': 'causal metadata synced'}), 200
    else:
        return jsonify({'causal-metadata': view, 'key-value-store': key_val_store}), 200

@app.route('/sync/state', methods=['GET'])
def sync_state():
    return jsonify({'ring': ring, 'shards': shards}), 200

@app.route('/merge/<key>', methods=['PUT'])
def merge(key):
    json_body = request.get_json(silent = True)
    if key not in key_val_store:
        value = json_body.get('value')
        key_val_store[key] = value
    return jsonify({'result': 'done'}), 200

if __name__ == '__main__':
    socket_address = os.getenv('SOCKET_ADDRESS')
    view_str = os.getenv('VIEW')
    num_shards = os.getenv('SHARD_COUNT')
    view_list = re.split(',', view_str)
    if num_shards != None:
        for address in view_list:
            view[address] = 0
        shard_count = int(num_shards)
        create_shards()
        assign_shards()
    else:
        for address in view_list:
            if address == socket_address:
                continue
            view[address] = 0
        view[socket_address] = 0
        broadcast('PUT', '/view', {'socket-address': socket_address})
        state_retrieval()
    app.run(host = '0.0.0.0', port = 8090)