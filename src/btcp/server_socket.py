from btcp.btcp_socket import BTCPSocket, BTCPStates
from btcp.lossy_layer import LossyLayer
from btcp.constants import *
from os import urandom

import queue
import struct
import time

class BTCPServerSocket(BTCPSocket):
    """bTCP server socket
    A server application makes use of the services provided by bTCP by calling
    accept, recv, and close.

    You're implementing the transport layer, exposing it to the application
    layer as a (variation on) socket API. Do note, however, that this socket
    as presented is *always* in "listening" state, and handles the client's
    connection in the same socket. You do not have to implement a separate
    listen socket. If you get everything working, you may do so for some extra
    credit.

    To implement the transport layer, you also need to interface with the
    network (lossy) layer. This happens by both calling into it
    (LossyLayer.send_segment) and providing callbacks for it
    (BTCPServerSocket.lossy_layer_segment_received, lossy_layer_tick).

    Your implementation will operate in two threads, the network thread,
    where the lossy layer "lives" and where your callbacks will be called from,
    and the application thread, where the application calls connect, send, etc.
    This means you will need some thread-safe information passing between
    network thread and application thread.
    Writing a boolean or enum attribute in one thread and reading it in a loop
    in another thread should be sufficient to signal state changes.
    Lists, however, are not thread safe, so to pass data and segments around
    you probably want to use Queues, or a similar thread safe collection.
    """


    def __init__(self, window, timeout):
        """Constructor for the bTCP server socket. Allocates local resources
        and starts an instance of the Lossy Layer.

        You can extend this method if you need additional attributes to be
        initialized, but do *not* call accept from here.
        """
        super().__init__(window, timeout)
        self._lossy_layer = LossyLayer(self, SERVER_IP, SERVER_PORT, CLIENT_IP, CLIENT_PORT)
        
        self.state = BTCPStates.CLOSED
        self.seqnum = None
        self.acknum = 0
        
        # The data buffer used by lossy_layer_segment_received to move data
        # from the network thread into the application thread. Bounded in size.
        # If data overflows the buffer it will get lost -- that's what window
        # size negotiation should solve.
        # For this rudimentary implementation, we simply hope receive manages
        # to be faster than send.
        self._recvbuf = queue.Queue(maxsize=1000)
        
        self.fin_retries = None
        self.fin_timeout = None
        self.ack_timeout = None
        self.window = WINDOW


    ###########################################################################
    ### The following section is the interface between the transport layer  ###
    ### and the lossy (network) layer. When a segment arrives, the lossy    ###
    ### layer will call the lossy_layer_segment_received method "from the   ###
    ### network thread". In that method you should handle the checking of   ###
    ### the segment, and take other actions that should be taken upon its   ###
    ### arrival, like acknowledging the segment and making the data         ###
    ### available for the application thread that calls to recv can return  ###
    ### the data.                                                           ###
    ###                                                                     ###
    ### Of course you can implement this using any helper methods you want  ###
    ### to add.                                                             ###
    ###                                                                     ###
    ### Since the implementation is inherently multi-threaded, you should   ###
    ### use a Queue, not a List, to transfer the data to the application    ###
    ### layer thread: Queues are inherently threadsafe, Lists are not.      ###
    ###########################################################################

    def lossy_layer_segment_received(self, segment):
        """Called by the lossy layer whenever a segment arrives.

        Things you should expect to handle here (or in helper methods called
        from here):
            - checksum verification (and deciding what to do if it fails)
            - receiving syn and client's ack during handshake
            - receiving segments and sending acknowledgements for them,
              making data from those segments available to application layer
            - receiving fin and client's ack during termination
            - any other handling of the header received from the client

        Remember, we expect you to implement this *as a state machine!*
        """
        if self.state == BTCPStates.CLOSED:
            return None  # don't receive segments if server is in the CLOSED state
   
        
        seqnum, acknum, syn_set, ack_set, fin_set, window, datalen, checksum = self.unpack_segment_header(segment[:HEADER_SIZE])
        chunk = segment[HEADER_SIZE:HEADER_SIZE + datalen]
        print(f"Segment Received: CHECKSUM = {checksum}, seqnum = {seqnum}, acknum={acknum}, syn_set = {syn_set}, ack_set = {ack_set}, fin_set = {fin_set}, window = {window}, datalen = {datalen}")
        # checksum verification
        check = self.in_cksum(self.build_segment_header(seqnum, acknum, syn_set, ack_set, fin_set, window, datalen) + chunk)
        if check != checksum:
            print("Incorrect checksum")
            return None  # do nothing when checksum fails

        
        # which flags combination is set in the received segment
        SYNACK = syn_set and ack_set
        SYN = syn_set and not SYNACK
        ACK = ack_set and not SYNACK
        FIN = fin_set and not SYN and not ACK
        NOFLAG = not(SYN or ACK or FIN or SYNACK)
        
        #(dis)connection handshake 
        if self.state != BTCPStates.ESTABLISHED:
            if self.state == BTCPStates.ACCEPTING and SYN:
                self.acknum = seqnum + 1
                self.state = BTCPStates.SYN_RCVD
                return

            if self.state == BTCPStates.SYN_RCVD and ACK:
                print("ACK received")
                self.state = BTCPStates.ESTABLISHED
                return
            
            if self.state == BTCPStates.CLOSING and ACK:
                self.state = BTCPStates.CLOSED
                return
            
            if self.state == BTCPStates.CLOSING and FIN:
                if self.fin_retries >= 10:
                    self.state = BTCPStates.CLOSED
                    return
                header = self.build_segment_header(self.seqnum, self.acknum, fin_set=True, ack_set=True)
                payload = b"".join([b"\x00" for _ in range(1008)])
                checksum = self.in_cksum(header)
                header = self.build_segment_header(self.seqnum, self.acknum, fin_set=True, ack_set=True, checksum=checksum)
                fin_ack = header + payload
                self._lossy_layer.send_segment(fin_ack)
                self.fin_retries += 1
                return

        else:
            if FIN:
                self.state = BTCPStates.CLOSING
                self.fin_retries = 0
                self.fin_timeout = time.time() + 30
                header = self.build_segment_header(self.seqnum, self.acknum, fin_set=True, ack_set=True)
                payload = b"".join([b"\x00" for _ in range(1008)])
                checksum = self.in_cksum(header)
                header = self.build_segment_header(self.seqnum, self.acknum, fin_set=True, ack_set=True, checksum=checksum)
                fin_ack = header + payload
                self._lossy_layer.send_segment(fin_ack)
                return
            
            if NOFLAG:
                self.ack_timeout = None
                if seqnum == (self.acknum + 1):
                    self._lossy_layer.send_segment(self.generate_ack())
                    self.acknum += 1
                    try:
                        self._recvbuf.put_nowait(chunk)
                    except queue.Full:
                        # Data gets silently dropped if the receive buffer is full. You
                        # need to ensure this doesn't happen by using window sizes and not
                        # acknowledging dropped data.
                        pass
                else:
                    self._lossy_layer.send_segment(self.generate_ack())

    def lossy_layer_tick(self):
        """Called by the lossy layer whenever no segment has arrived for
        TIMER_TICK milliseconds. Defaults to 100ms, can be set in constants.py.

        NOTE: Will NOT be called if segments are arriving; do not rely on
        simply counting calls to this method for an accurate timeout. If 10
        segments arrive, each 99 ms apart, this method will NOT be called for
        over a second!

        The primary use for this method is to be able to do things in the
        "network thread" even while no segments are arriving -- which would
        otherwise trigger a call to lossy_layer_segment_received. On the server
        side, you may find you have no actual need for this method. Or maybe
        you do. See if it suits your implementation.

        You will probably see some code duplication of code that doesn't handle
        the incoming segment among lossy_layer_segment_received and
        lossy_layer_tick. That kind of duplicated code would be a good
        candidate to put in a helper method which can be called from either
        lossy_layer_segment_received or lossy_layer_tick.
        """
        
        #close connection if no FIN_ACK is received after the timeout has passed
        if self.state == BTCPStates.CLOSING and time.time() >= self.fin_timeout:
            self.state = BTCPStates.CLOSED
            return None
        
        #resend ACK if timeout has passed
        if self.state == BTCPStates.ESTABLISHED:
            if self.ack_timeout is None:
                self.ack_timeout = time.time() + TIMEOUT
                return None
            elif time.time() >= self.ack_timeout:
                self._lossy_layer.send_segment(self.generate_ack())
                return None
                
    
    def generate_ack(self):
        header = self.build_segment_header(self.seqnum, self.acknum, ack_set=True)
        payload = b"".join([b"\x00" for _ in range(1008)])
        checksum = self.in_cksum(header)
        header = self.build_segment_header(self.seqnum, self.acknum, ack_set=True, checksum=checksum, window=self.window)
        ack = header + payload
        return ack


    ###########################################################################
    ### You're also building the socket API for the applications to use.    ###
    ### The following section is the interface between the application      ###
    ### layer and the transport layer. Applications call these methods to   ###
    ### accept connections, receive data, etc. Conceptually, this happens   ###
    ### in "the application thread".                                        ###
    ###                                                                     ###
    ### You *can*, from this application thread, send segments into the     ###
    ### lossy layer, i.e. you can call LossyLayer.send_segment(segment)     ###
    ### from these methods without ensuring that happens in the network     ###
    ### thread. However, if you do want to do this from the network thread, ###
    ### you should use the lossy_layer_tick() method above to ensure that   ###
    ### segments can be sent out even if no segments arrive to trigger the  ###
    ### call to lossy_layer_segment_received. When passing segments between ###
    ### the application thread and the network thread, remember to use a    ###
    ### Queue for its inherent thread safety. Whether you need to send      ###
    ### segments from the application thread into the lossy layer is up to  ###
    ### you; you may find you can handle all receiving *and* sending of     ###
    ### segments in the lossy_layer_segment_received and lossy_layer_tick   ###
    ### methods.                                                            ###
    ###                                                                     ###
    ### Note that because this is the server socket, and our (initial)      ###
    ### implementation of bTCP is one-way reliable data transfer, there is  ###
    ### no send() method available to the applications. You should still    ###
    ### be able to send segments on the lossy layer, however, because       ###
    ### of acknowledgements and synchronization. You should implement that  ###
    ### above.                                                              ###
    ###########################################################################
            
    def accept(self):
        """Accept and perform the bTCP three-way handshake to establish a
        connection.

        accept should *block* (i.e. not return) until a connection has been
        successfully established (or some timeout is reached, if you want. Feel
        free to add a timeout to the arguments). You will need some
        coordination between the application thread and the network thread for
        this, because the syn and final ack from the client will be received in
        the network thread.

        Hint: assigning to a boolean or enum attribute in thread A and reading
        it in a loop in thread B (preferably with a short sleep to avoid
        wasting a lot of CPU time) ensures that thread B will wait until the
        boolean or enum has the expected value. We do not think you will need
        more advanced thread synchronization in this project.
        """
        
        while True:
            connect_timeout = time.time() + 20
            self.state = BTCPStates.ACCEPTING

            print("State = ACCEPTING")

            while self.state != BTCPStates.SYN_RCVD:
                if time.time() >= connect_timeout:
                    self.state = BTCPStates.CLOSED
                    return

            print("State = SYN_RCVD")

            self.seqnum = int.from_bytes(urandom(2), byteorder='big')
            header = self.build_segment_header(self.seqnum, self.acknum, syn_set=True, ack_set=True, window=self.window)
            payload = b"".join([b"\x00" for _ in range(1008)])
            checksum = self.in_cksum(header)
            header = self.build_segment_header(self.seqnum, self.acknum, syn_set=True, ack_set=True, window=self.window, checksum=checksum)
            syn_ack = header + payload
            retries = 0
            while self.state != BTCPStates.ESTABLISHED and retries < 10:
                self._lossy_layer.send_segment(syn_ack)
                print(f"sent SYNACK with CHECKSUM = {checksum}, seqnum = {self.seqnum}, acknum={self.acknum},  window = {self.window}, datalen = 0")
                retries += 1
                time.sleep(10)

            if self.state == BTCPStates.ESTABLISHED:
                print("Connection established")
                return
                                               
            continue
         
        
        

    def recv(self):
        """Return data that was received from the client to the application in
        a reliable way.

        If no data is available to return to the application, this method
        should block waiting for more data to arrive. If the connection has
        been terminated, this method should return with no data (e.g. an empty
        bytes b'').

        If you want, you can add an argument to this method stating how many
        bytes you want to receive in one go at the most (but this is not
        required for this project).

        You are free to implement this however you like, but the following
        explanation may help to understand how sockets *usually* behave and you
        may choose to follow this concept as well:

        The way this usually works is that "recv" operates on a "receive
        buffer". Once data has been successfully received and acknowledged by
        the transport layer, it is put "in the receive buffer". A call to recv
        will simply return data already in the receive buffer to the
        application.  If no data is available at all, the method will block
        until at least *some* data can be returned.
        The actual receiving of the data, i.e. reading the segments, sending
        acknowledgements for them, reordering them, etc., happens *outside* of
        the recv method (e.g. in the network thread).
        Because of this blocking behaviour, an *empty* result from recv signals
        that the connection has been terminated.

        Again, you should feel free to deviate from how this usually works.
        """
        
        # Rudimentary example implementation:
        # Empty the queue in a loop, reading into a larger bytearray object.
        # Once empty, return the data as bytes.
        # If no data is received for 10 seconds, this returns no data and thus
        # signals disconnection to the server application.
        # Proper handling should use the bTCP state machine to check that the
        # client has disconnected when a timeout happens, and keep blocking
        # until data has actually been received if it's still connected.
        
        data = bytearray()
        try:
            # Wait until one segment becomes available in the buffer, or
            # timeout signalling disconnect.
            data.extend(self._recvbuf.get(block=True, timeout=10))
            while True:
                # Empty the rest of the buffer, until queue.Empty exception
                # exits the loop. If that happens, data contains received
                # segments so that will *not* signal disconnect.
                data.extend(self._recvbuf.get_nowait())
                self.window = self._recvbuf.maxsize-self._recvbuf.qsize()
        except queue.Empty:
            pass  # (Not break: the exception itself has exited the loop)
        return bytes(data)


    def close(self):
        """Cleans up any internal state by at least destroying the instance of
        the lossy layer in use. Also called by the destructor of this socket.

        Do not confuse with shutdown, which disconnects the connection.
        close destroys *local* resources, and should only be called *after*
        shutdown.

        Probably does not need to be modified, but if you do, be careful to
        gate all calls to destroy resources with checks that destruction is
        valid at this point -- this method will also be called by the
        destructor itself. The easiest way of doing this is shown by the
        existing code:
            1. check whether the reference to the resource is not None.
                2. if so, destroy the resource.
            3. set the reference to None.
        """
        if self._lossy_layer is not None:
            self._lossy_layer.destroy()
        self._lossy_layer = None


    def __del__(self):
        """Destructor. Do not modify."""
        self.close()
