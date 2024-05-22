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

##### When a replica receives a request from a client:

- It compares its vector clock with the causal metadata in the request.
- If the metadata is null (indicating no dependencies) or matches the replica's clock, the request is processed.
- For a write request, the replica updates its vector clock and broadcasts the request to other replicas, including its socket address in the request to indicate it's a replica-to-replica message.

#### From Other Replicas

##### When a replica receives a write request from another replica:

- It checks if the sender's clock value is one more than its own for the sender's address and that all other values are equal or lower.
- If the conditions are met, it processes the write and updates its clock.
- Otherwise, it returns a 503 response, indicating the request must be retried later.

### Sharding

- Writes are propagated only within the same shard.
- Replicas in other shards update only their vector clocks, not their key-value stores.

### Conclusion

This implementation of vector clocks ensures that causal consistency is maintained across the system.

## Replica Failure Detection Mechanism

### Overview

Replica failure detection is implemented to ensure system reliability. When a client sends a successful write request (e.g., PUT or DELETE) to a replica, the replica broadcasts this request to all other replicas in its view. If a replica fails to respond to the broadcast, it's assumed to have crashed.

### Handling Failed Replicas

#### When a broadcasting replica detects a failed replica:

- It removes the failed replica from its own view.
- It broadcasts a DELETE /view call to other working replicas to remove the failed replica from their views as well.

### Handling False Positives and Negatives

- **False Positives:** A false positive may occur if a replica takes longer than the response timeout to respond to the broadcast, leading the broadcasting replica to assume it's down and remove it from the view.
- **False Negatives:** A false negative may occur if a replica responds successfully but crashes immediately afterward. In such cases, other replicas won't know the replica is down until the next broadcasted write attempt.

## Key-to-Shard Mapping Mechanism

### Overview

To evenly distribute nodes and key-value pairs among shards, consistent hashing is implemented using a "ring" structure and a dictionary of shards.

### Ring Initialization

- The ring is represented as a list, with shard IDs marked at regular intervals.
- Shard IDs are incremented alphabetically (starting from "a") and assigned to indexes spaced (# of indexes / # of shards) apart.

### Replica Assignment to Shards

- Replicas are evenly assigned to each shard.
- Any leftover replicas are assigned to the last shard.

### Key-Value Store Operations

- Keys are evenly distributed across shards using a hash function (SHA256).
- The ring is traversed clockwise from the position of a key's hash until the next shard ID is found.
- If the shard matches the replica receiving the request, the operation proceeds normally.
- If the shard differs, the request is forwarded to a replica in that shard using the shard dictionary.

### Conclusion

This approach ensures nodes and keys are evenly distributed across shards and enables handling requests sent to replicas in any shard.

## Resharding Mechanism

### Overview

Resharding is initiated through a PUT request to the /shard/reshard endpoint.

### Checking Shard Availability

- The process starts with checking if there are enough working replicas for the new specified number of shards.
- Each shard requires at least 2 replicas.
- If the number of shards divided by the number of replicas is not >= 2, a 404 error is returned.

### Resharding Steps

- If enough replicas are available, the resharding process begins.
- New shard positions are calculated in the ring using a method described earlier.
- Replicas are evenly reassigned to the new shards.
- Existing key-value pairs may need to be sent to the appropriate new shards.
- Replicas recalculate new shard mappings for keys in their local key-value store using hash and mod process.
- Key-value pairs are sent to new shard replicas if their keys now map to different shards.
- Reshard request is broadcasted to signal other replicas to initiate resharding locally.
- Original replica returns a 200 OK response to the client to indicate successful resharding.

### Conclusion

This process ensures the system maintains fault tolerance and evenly distributes key-value pairs among shards.