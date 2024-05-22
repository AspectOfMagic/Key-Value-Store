# Key-Value Store
Author of server.c and Dockerfile: Jason Wu

## Introduction
The program, written in Python, is a sharded, replicated, fault-tolerant and causally consistent key-value store. The key-value store runs as a network of communicating nodes, where each node is able to fulfill clients' requests. In addition, consistent hashing is used in the system to partition nodes into shards and distribute key-value pairs evenly across said shards.


## Installation
You will need Docker

## Usage
Build the container image and tag it "serverimg":  
$ docker build -t serverimg .

Create a subnet called "servernet" with IP range 10.10.0.0/16:  
$ docker network create --subnet=10.10.0.0/16 servernet

Run instances(replicas) in the network:  
$ docker run --rm -p (port number):8090 --net=servernet --ip=10.10.0.x --name=(replica name) -e=SHARD_COUNT=(number of shards) -e=SOCKET_ADDRESS=10.10.0.x:8090 -e=VIEW=10.10.0.x:8090,10.10.0.y:8090,10.10.0.z:8090,... serverimg

- SOCKET_ADDRESS: a string in the format "IP:PORT" describing the current node.  
- VIEW: a comma-delimited string containing the socket addresses of all the running instances.  
- x, y, z: numbers between 2 and 16

## Causal Dependency Mechanism

### Overview

In order to achieve causal consistency, vector clocks are utilized as metadata for tracking message sends and local event history on each replica.

### Vector Clocks Setup

Each replica maintains a vector clock, represented as a dictionary where the key is the replica's socket address and the value is the number of write requests (e.g., PUT or DELETE) it has received. Initially, all clock values are set to zero.

### Handling Requests

#### From Clients

When a replica receives a request from a client:
- It compares its vector clock with the causal metadata in the request.
- If the metadata is null (indicating no dependencies) or matches the replica's clock, the request is processed.
- For a write request, the replica updates its vector clock and broadcasts the request to other replicas, including its socket address in the request to indicate it's a replica-to-replica message.

#### From Other Replicas

When a replica receives a write request from another replica:
- It checks if the sender's clock value is one more than its own for the sender's address and that all other values are equal or lower.
- If the conditions are met, it processes the write and updates its clock.
- Otherwise, it returns a 503 response, indicating the request must be retried later.

### Sharding

With sharding:
- Writes are propagated only within the same shard.
- Replicas in other shards update only their vector clocks, not their key-value stores.

### Conclusion

This implementation of vector clocks ensures that causal consistency is maintained across the system.

## Replica Failure Detection Mechanism

### Overview

Replica failure detection is implemented to ensure system reliability. When a client sends a successful write request (e.g., PUT or DELETE) to a replica, the replica broadcasts this request to all other replicas in its view. If a replica fails to respond to the broadcast, it's assumed to have crashed.

### Handling Failed Replicas

When a broadcasting replica detects a failed replica:
- It removes the failed replica from its own view.
- It broadcasts a DELETE /view call to other working replicas to remove the failed replica from their views as well.

### Handling False Positives and Negatives

- **False Positives:** A false positive may occur if a replica takes longer than the response timeout to respond to the broadcast, leading the broadcasting replica to assume it's down and remove it from the view.
- **False Negatives:** A false negative may occur if a replica responds successfully but crashes immediately afterward. In such cases, other replicas won't know the replica is down until the next broadcasted write attempt.

## Key-to-Shard Mapping Mechanism

In order to ensure that nodes and key-value pairs are being evenly distributed amongst shards, consistent hashing is implemented through the use of a "ring" structure represented as a list, and a dictionary of shards in which the keys of the dictionary are the IDs of a shard (represented as a UTF-8 character starting from "a" in alphabetical order) and the values are the list of addresses of replicas that belong to the corresponding shard. 

To initialize the ring, I mark the indexes that are spaced at (# of indexes divided by the # of shards) intervals around the ring with a shard ID, incrementing the shard ID each time. For example, in a 64-index ring where the number of shards given is 4, I mark index 0 with shard "a", index 16 with shard "b", index 32 with shard "c", and index 48 with shard "d". I then evenly assign replicas to each shard, first by assigning X replicas to each shard in order where X = floor(# of shards divided by # of replicas), then assigning any leftover replicas to the last shard that was calculated. This process ensures that nodes are relatively evenly distributed across shards.

For key-value store operations, I also need to make sure that keys are evenly distributed across shards. When a replica receives a request, it first calculates the position that the given key maps to in the ring by using a hash function on the key (in our case, SHA256), and modding the result by the number of indexes in the ring list. This process ensures that different keys land randomly and evenly at locations in the ring. In order to determine the shard that a key belongs in, I traverse the ring in clockwise direction, starting from the position of the a key's hash, until I find the next element in the ring that is a shard ID. This is the shard that the key must be stored, if the request is a PUT, or located, if the request is a GET or DELETE. If the shard of the key is the same as the shard of the replica receiving the request, then it is safe for the replica to continue with the specified key-value store operations that need to be processed. On the other hand, if I discover the shard that the key hashes to is not the same shard that the replica receiving the request belongs to, then the request must be forwarded to a member of the replica that is a member of the shard that the key also maps to. I can use the shard dictionary calculated earlier to determine what replicas belong in that particular shard, so that the original request can be forwarded to them.

Through these methods, I can map nodes and keys to their respective shards, distribute them evenly, and handle requests sent to replicas located in any shard.

## Resharding Mechanism

In the event of a reshard request through a PUT request call at the /shard/reshard endpoint, I first need to check if there are enough available shards, as each shard requires at least 2 replicas to ensure fault tolerance. If I find that the number of shards divided by the number of replicas is not >= 2, then I return a 404 error indicating that there are not enough nodes to provide fault tolerance with the requested shard count.

If I do have enough replicas, then resharding begins. I can use the method described earlier in order to calculate the positions that the new shards take in the ring. After recalculating the distance between shard IDs in the ring, replicas can be reassigned to their new shards evenly. In addition, existing key-value pairs may need to be sent to the appropriate new shards. The replica utilizes the hash and mod process described earlier to recalculate the new shard mappings of the keys in its local key-value store, and does not make any changes in the local key-value store for key-value pairs whose keys that still map to the same shard as the replica. Existing key-value pairs whose keys now map to different shards are sent to all the replicas belonging to the new shards that the keys now hash to and are deleted from the original replica's local key-value store. The original resharding replica also broadcasts a reshard request to signal that every other replica should initiate the resharding process locally, attaching its socket address to the broadcast message to indicate that these replicas should not also send their own reshard broadcasts. Finally, the original replica returns a 200 OK response to the client to indicate that the reshard request has succeeded.