# Key-Value Store
Author of server.c and Dockerfile: Jason Wu

## Introduction
The program, written entirely in Python, is a sharded, replicated, fault-tolerant and causally consistent key-value store.

## Installation
You will need Docker

## Usage
Build the container image and tag it "serverimg":  
$ docker build -t serverimg .

Create a subnet called "servernet" with IP range 10.10.0.0/16:  
$ docker network create --subnet=10.10.0.0/16 servernet

Run instances(replicas) in the network:  
$ docker run --rm -p (port number):8090 --net=servernet --ip=10.10.0.x --name=(replica name) -e=SHARD_COUNT=(number of shards) -e=SOCKET_ADDRESS=10.10.0.x:8090 -e=VIEW=10.10.0.x:8090,10.10.0.y:8090,10.10.0.z:8090,... serverimg

SOCKET_ADDRESS: a string in the format "IP:PORT" describing the current node.  
VIEW: a comma-delimited string containing the socket addresses of all the running instances.  
x, y, z: numbers between 2 and 16

## Causal Dependency Mechanism

In order to implement causal consistency, I implement vector clocks as casual metadata for both message sends as well as local event history on each replica. This is implemented as a dictionary where the key is a replica's socket address and the corresponding value is the number of write requests (such as PUT or DELETE requests) that have arrived at that particular replica from a client. Prior to any write requests occurring, all clock values are initialized to zero. When a replica receives a request, it compares its own vector clock to the casual metadata field of the received request. Requests coming from a client and requests coming as the result of a write broadcast from one replica to all other replicas are handled slightly differently through the use of a special socket-address request field that is either blank, indicating that a request is coming from a client, or contains the socket address of the replica it is coming from, indicating that the request is coming from another replica.

In the case of a request coming from a sender, the request is safe to handle if the vector clock of the receiving replica is equivalent to the causal metadata sent in the message (indicating that causal dependencies have been satisfied), or if the casual-metadata field is null, indicating that there are no causal dependencies that need to be satisfied (i.e. the request is the first one being handled by any replica during a single execution's runtime). If the request is a write, such as a PUT or DELETE, then a replica must update their vector clock by adding one to the clock value at its own socket address key, then broadcast the request to all the other replicas to ensure that they update their local key-value stores as well; the broadcasting replica also includes its socket address as a request field to indicate that the request is replica-to-replica.Otherwise, any differences between the casual metadata of the message and the local vector clock of the replica indicates that causal dependencies have not been satisfied, and a 503 response is returned to the client.

For the case of a request occuring as part of a write broadcast from another replica, where the sending replica's socket address is included as part of the request fields, I slightly change my approach by checking to see instead that the clock value at the sender's position in the message's causal metadata field is strictly greater by one than the receiver's local vector clock value at the sender's position, and that all other clock values from the message's causal metadata are less than or equal to their corresponding values in the receiver's vector clock. If so, then there is no violation of causal dependencies and the write operation is safe to process. The receiving replica then also updates its own vector clock by incrementing the value at the sending replica's socket address position. Any other result in the comparison between the message's casual metadata and the receiving replica's local vector clock indicates that causal dependencies have not been satisfied yet on the receiving replica, and a 503 response is returned to the sending replica, signaling that the request to that specific replica will have to be retried.

With the addition of sharding in assignment 4, writes are only propagated to the other replicas in the same shard as opposed to every other replica, while replicas located in other shards are notified to update their vector clocks only for causal consistency purposes and not their key-value stores as well.

Through this implementation of vector clocks, I am able to ensure that causal consistency is never violated.

## Replica Failure Detection Mechanism

I implement replica failure detection by checking for errors when a replica broadcasts a write operation to other replicas. When a client sends a successful write request to a replica, such as a PUT or DELETE, that replica will then broadcast this request to all other replicas in its view. During this broadcast, I choose to make the assumption that a crash has occured if a replica never returns a response to the broadcast request. For every replica that a broadcasting replica has determined has gone down through this method, it first deletes the replica from its own view, then broadcasts a DELETE /view call to the other working replicas so that they can remove that replica from their view as well.

A case where a false positive might happen (a replica is deemed to be down when it is actually up) is if it takes an abnormally long time (longer than the set response timeout) to respond to a broadcast request, at which point the broadcasting replica may have already assumed it was down and taken the steps to remove the slow replica from its and other replicas' view.

A case where a false negative might happen (where a replica is deemed to be up when it is actually down) might occur if a replica successfully sends a non-503 response back to a broadcasting replica and then immediately crashes, in which case the other replicas would not "know" that the particular replica is down as indicated by their vector clocks until the next broadcasted write occurs, at which a broadcasting replica would attempt to reach the downed replica again.

## Key-to-Shard Mapping Mechanism

In order to ensure that nodes and key-value pairs are being evenly distributed amongst shards, I implement consistent hashing through the use of a "ring" structure represented as a list, and a dictionary of shards in which the keys of the dictionary are the IDs of a shard (represented as a UTF-8 character starting from "a" in alphabetical order) and the values are the list of addresses of replicas that belong to the corresponding shard. 

To initialize the ring, I mark the indexes that are spaced at (# of indexes divided by the # of shards) intervals around the ring with a shard ID, incrementing the shard ID each time. For example, in a 64-index ring where the number of shards given is 4, I mark index 0 with shard "a", index 16 with shard "b", index 32 with shard "c", and index 48 with shard "d". I then evenly assign replicas to each shard, first by assigning X replicas to each shard in order where X = floor(# of shards divided by # of replicas), then assigning any leftover replicas to the last shard that was calculated. This process ensures that nodes are relatively evenly distributed across shards.

For key-value store operations, I also need to make sure that keys are evenly distributed across shards. When a replica receives a request, it first calculates the position that the given key maps to in the ring by using a hash function on the key (in our case, SHA256), and modding the result by the number of indexes in the ring list. This process ensures that different keys land randomly and evenly at locations in the ring. In order to determine the shard that a key belongs in, I traverse the ring in clockwise direction, starting from the position of the a key's hash, until I find the next element in the ring that is a shard ID. This is the shard that the key must be stored, if the request is a PUT, or located, if the request is a GET or DELETE. If the shard of the key is the same as the shard of the replica receiving the request, then it is safe for the replica to continue with the specified key-value store operations that need to be processed. On the other hand, if I discover the shard that the key hashes to is not the same shard that the replica receiving the request belongs to, then the request must be forwarded to a member of the replica that is a member of the shard that the key also maps to. I can use the shard dictionary calculated earlier to determine what replicas belong in that particular shard, so that the original request can be forwarded to them.

Through these methods, I can map nodes and keys to their respective shards, distribute them evenly, and handle requests sent to replicas located in any shard.

## Resharding Mechanism

In the event of a reshard request through a PUT request call at the /shard/reshard endpoint, I first need to check if there are enough available shards, as each shard requires at least 2 replicas to ensure fault tolerance. If I find that the number of shards divided by the number of replicas is not >= 2, then I return a 404 error indicating that there are not enough nodes to provide fault tolerance with the requested shard count.

If I do have enough replicas, then resharding begins. I can use the method described earlier in order to calculate the positions that the new shards take in the ring. After recalculating the distance between shard IDs in the ring, replicas can be reassigned to their new shards evenly. In addition, existing key-value pairs may need to be sent to the appropriate new shards. The replica utilizes the hash and mod process described earlier to recalculate the new shard mappings of the keys in its local key-value store, and does not make any changes in the local key-value store for key-value pairs whose keys that still map to the same shard as the replica. Existing key-value pairs whose keys now map to different shards are sent to all the replicas belonging to the new shards that the keys now hash to and are deleted from the original replica's local key-value store. The original resharding replica also broadcasts a reshard request to signal that every other replica should initiate the resharding process locally, attaching its socket address to the broadcast message to indicate that these replicas should not also send their own reshard broadcasts. Finally, the original replica returns a 200 OK response to the client to indicate that the reshard request has succeeded.