from btcp.btcp_socket import BTCPSocket, BTCPStates
from btcp.lossy_layer import LossyLayer
from btcp.constants import *

# from btcp.btcp_socket import BTCPSocket, BTCPStates
# from btcp.lossy_layer import LossyLayer
# from btcp.constants import *

import queue
from os import urandom
import time


class BTCPClientSocket(BTCPSocket):
    """bTCP client socket
    A client application makes use of the services provided by bTCP by calling
    connect, send, shutdown, and close.

    You're implementing the transport layer, exposing it to the application
    layer as a (variation on) socket API.

    To implement the transport layer, you also need to interface with the
    network (lossy) layer. This happens by both calling into it
    (LossyLayer.send_segment) and providing callbacks for it
    (BTCPClientSocket.lossy_layer_segment_received, lossy_layer_tick).

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
        """Constructor for the bTCP client socket. Allocates local resources
        and starts an instance of the Lossy Layer.

        You can extend this method if you need additional attributes to be
        initialized, but do *not* call connect from here.
        """
        super().__init__(window, timeout)
        self._lossy_layer = LossyLayer(self, CLIENT_IP, CLIENT_PORT, SERVER_IP, SERVER_PORT)

        # The data buffer used by send() to send data from the application
        # thread into the network thread. Bounded in size.
        self._sendbuf = queue.Queue(maxsize=1000)

        self.state = BTCPStates.CLOSED
        self.seqnum = None
        self.acknum = 0
        self.client_window = WINDOW
        self.server_window = WINDOW
        self.last_rec_ack = 0  # last acknowledged segment by the server
        self.synack_recv = False
        self.finack_recv = False
        self.segments_in_transit = []


    ###########################################################################
    ### The following section is the interface between the transport layer  ###
    ### and the lossy (network) layer. When a segment arrives, the lossy    ###
    ### layer will call the lossy_layer_segment_received method "from the   ###
    ### network thread". In that method you should handle the checking of   ###
    ### the segment, and take other actions that should be taken upon its   ###
    ### arrival.                                                            ###
    ###                                                                     ###
    ### Of course you can implement this using any helper methods you want  ###
    ### to add.                                                             ###
    ###########################################################################


    def lossy_layer_segment_received(self, segment):
        """Called by the lossy layer whenever a segment arrives.

        Things you should expect to handle here (or in helper methods called
        from here):
            - checksum verification (and deciding what to do if it fails)
            - receiving syn/ack during handshake
            - receiving ack and registering the corresponding segment as being
              acknowledged
            - receiving fin/ack during termination
            - any other handling of the header received from the server

        Remember, we expect you to implement this *as a state machine!*
        """

        if self.state == BTCPStates.CLOSED:
            return None

        seqnum, acknum, syn_set, ack_set, fin_set, window, datalen, checksum = self.unpack_segment_header(segment[:HEADER_SIZE])
        print(f"Segment Received: CHECKSUM = {checksum}, seqnum = {seqnum}, acknum={acknum}, syn_set = {syn_set}, ack_set = {ack_set}, fin_set = {fin_set}, window = {window}, datalen = {datalen}")
        # checksum verification
        check = self.in_cksum(self.build_segment_header(seqnum, acknum, syn_set, ack_set, fin_set, window, datalen))
        print(f"Check equals {check}")
        if check != checksum:
            print("Incorrect checksum")
            return None  # do nothing when checksum fails


        # connection handshake
        elif self.state == BTCPStates.SYN_SENT:
            if syn_set == 1 and ack_set == 1 and self.seqnum+1 == acknum:
                print("SYNACK received")
                self.acknum = seqnum
                self.server_window = window
                self.synack_recv = True
        # termination handshake
        elif self.state == BTCPStates.FIN_SENT:
            if ack_set == 1 and fin_set == 1 and self.seqnum+1 == acknum:
                self.finack_recv = True
        # regular ack from server
        elif self.state == BTCPStates.ESTABLISHED:
            self.server_window = window
            if self.last_rec_ack < acknum and ack_set == 1:
                self.last_rec_ack = acknum
                # clean up the segments in transit queue
                i = 0
                timeout, acknum, segment = self.segments_in_transit[i]
                while self.last_rec_ack > acknum:
                    i += 1
                    timeout, acknum, segment = self.segments_in_transit[i]
                self.segments_in_transit = self.segments_in_transit[i:]


    def lossy_layer_tick(self):
        """Called by the lossy layer whenever no segment has arrived for
        TIMER_TICK milliseconds. Defaults to 100ms, can be set in constants.py.

        NOTE: Will NOT be called if segments are arriving; do not rely on
        simply counting calls to this method for an accurate timeout. If 10
        segments arrive, each 99 ms apart, this method will NOT be called for
        over a second!

        The primary use for this method is to be able to do things in the
        "network thread" even while no segments are arriving -- which would
        otherwise trigger a call to lossy_layer_segment_received.

        For example, checking for timeouts on acknowledgement of previously
        sent segments -- to trigger retransmission -- should work even if no
        segments are being received. Although you can't count these ticks
        themselves for the timeout, you can trigger the check from here.

        You will probably see some code duplication of code that doesn't handle
        the incoming segment among lossy_layer_segment_received and
        lossy_layer_tick. That kind of duplicated code would be a good
        candidate to put in a helper method which can be called from either
        lossy_layer_segment_received or lossy_layer_tick.
        """

        # Send all data available for sending.
        # Relies on an eventual exception to break from the loop when no data
        # is available.
        # You should be checking whether there's space in the window as well,
        # and storing the segments for retransmission somewhere.

        # resend all segments whose timer has run out
        for i in range(len(self.segments_in_transit)):
            timeout, seqnum, segment = self.segments_in_transit[i]
            if timeout+TIMEOUT < time.time():
                self.segments_in_transit[i] = (time.time(), seqnum, segment)
                self._lossy_layer.send_segment(segment)

        while self.seqnum - self.last_rec_ack < self.server_window:
            try:
                # Get a chunk of data from the buffer, if available.
                chunk = self._sendbuf.get_nowait()
                datalen = len(chunk)
                if datalen < PAYLOAD_SIZE:
                    chunk = chunk + b'\x00' * (PAYLOAD_SIZE - datalen)
                segment = self.build_segment_header(self.seqnum,
                                                    self.acknum,
                                                    window=self.client_window,
                                                    length=datalen
                                                    ) + chunk
                checksum = self.in_cksum(segment)
                segment = self.build_segment_header(self.seqnum,
                                                    self.acknum,
                                                    window=self.client_window,
                                                    length=datalen,
                                                    checksum=checksum
                                                    ) + chunk
                self.segments_in_transit.append((time.time(), self.seqnum, segment))
                self._lossy_layer.send_segment(segment)
                self.seqnum += 1
            except queue.Empty:
                # No data was available for sending.
                break


    ###########################################################################
    ### You're also building the socket API for the applications to use.    ###
    ### The following section is the interface between the application      ###
    ### layer and the transport layer. Applications call these methods to   ###
    ### connect, shutdown (disconnect), send data, etc. Conceptually, this  ###
    ### happens in "the application thread".                                ###
    ###                                                                     ###
    ### You *can*, from this application thread, send segments into the     ###
    ### lossy layer, i.e. you can call LossyLayer.send_segment(segment)     ###
    ### from these methods without ensuring that happens in the network     ###
    ### thread. However, if you do want to do this from the network thread, ###
    ### you should use the lossy_layer_tick() method above to ensure that   ###
    ### segments can be sent out even if no segments arrive to trigger the  ###
    ### call to lossy_layer_segment_received. When passing segments between ###
    ### the application thread and the network thread, remember to use a    ###
    ### Queue for its inherent thread safety.                               ###
    ###                                                                     ###
    ### Note that because this is the client socket, and our (initial)      ###
    ### implementation of bTCP is one-way reliable data transfer, there is  ###
    ### no recv() method available to the applications. You should still    ###
    ### be able to receive segments on the lossy layer, however, because    ###
    ### of acknowledgements and synchronization. You should implement that  ###
    ### above.                                                              ###
    ###########################################################################


    def connect(self):
        """Perform the bTCP three-way handshake to establish a connection.

        connect should *block* (i.e. not return) until the connection has been
        successfully established or the connection attempt is aborted. You will
        need some coordination between the application thread and the network
        thread for this, because the syn/ack from the server will be received
        in the network thread.

        Hint: assigning to a boolean or enum attribute in thread A and reading
        it in a loop in thread B (preferably with a short sleep to avoid
        wasting a lot of CPU time) ensures that thread B will wait until the
        boolean or enum has the expected value. We do not think you will need
        more advanced thread synchronization in this project.
        """
        self.seqnum = int.from_bytes(urandom(2), byteorder='big')
        header = self.build_segment_header(self.seqnum,
                                           self.acknum,
                                           syn_set=True,
                                           window=self.client_window)
        checksum = self.in_cksum(header)
        header = self.build_segment_header(self.seqnum,
                                           self.acknum,
                                           syn_set=True,
                                           window=self.client_window,
                                           checksum=checksum
                                           )
        # set payload = 0
        payload = b"".join([b"\x00" for _ in range(1008)])
        # combine header and payload
        segment = header + payload
        # send segment
        self._lossy_layer.send_segment(segment)
        print("SYN sent")
        self.state = BTCPStates.SYN_SENT

        # wait for server to send a segment back
        for i in range(RETRIES):
            timeout = time.time() + TIMEOUT
            while time.time() < timeout and not self.synack_recv:
                time.sleep(TIMER_TICK / 1000)
            if not self.synack_recv:
                self._lossy_layer.send_segment(segment)
            else:
                break

        if not self.synack_recv:
            self.state = BTCPStates.CLOSED
        else:
            print("SYNACK received")
            # add 1 to the seqnum of the server
            self.acknum += 1
            # add 1 to self.seqnum
            self.seqnum += 1
            # set ACK
            header = self.build_segment_header(self.seqnum,
                                               self.acknum,
                                               ack_set=True,
                                               window=self.client_window
                                               # length=
                                               )
            checksum = self.in_cksum(header)

            header = self.build_segment_header(self.seqnum,
                                               self.acknum,
                                               ack_set=True,
                                               checksum=checksum,
                                               window=self.client_window
                                               # length=
                                               )
            # send segment
            segment = header + payload
            self._lossy_layer.send_segment(segment)
            print("ACK sent and Connection established")
            self.state = BTCPStates.ESTABLISHED


    def send(self, data):
        """Send data originating from the application in a reliable way to the
        server.

        This method should *NOT* block waiting for acknowledgement of the data.


        You are free to implement this however you like, but the following
        explanation may help to understand how sockets *usually* behave and you
        may choose to follow this concept as well:

        The way this usually works is that "send" operates on a "send buffer".
        Once (part of) the data has been successfully put "in the send buffer",
        the send method returns the number of bytes it was able to put in the
        buffer. The actual sending of the data, i.e. turning it into segments
        and sending the segments into the lossy layer, happens *outside* of the
        send method (e.g. in the network thread).
        If the socket does not have enough buffer space available, it is up to
        the application to retry sending the bytes it was not able to buffer
        for sending.

        Again, you should feel free to deviate from how this usually works.
        Note that our rudimentary implementation here already chunks the data
        in maximum 1008-byte bytes objects because that's the maximum a segment
        can carry. If a chunk is smaller we do *not* pad it here, that gets
        done later.
        """

        # Example with a finite buffer: a queue with at most 1000 chunks,
        # for a maximum of 985KiB data buffered to get turned into packets.
        # See BTCPSocket__init__() in btcp_socket.py for its construction.
        datalen = len(data)
        sent_bytes = 0
        while sent_bytes < datalen:
            # Loop over data using sent_bytes. Reassignments to data are too
            # expensive when data is large.
            chunk = data[sent_bytes:sent_bytes+PAYLOAD_SIZE]
            try:
                self._sendbuf.put_nowait(chunk)
                sent_bytes += len(chunk)
            except queue.Full:
                break
        return sent_bytes


    def shutdown(self):
        """Perform the bTCP three-way finish to shutdown the connection.

        shutdown should *block* (i.e. not return) until the connection has been
        successfully terminated or the disconnect attempt is aborted. You will
        need some coordination between the application thread and the network
        thread for this, because the fin/ack from the server will be received
        in the network thread.

        Hint: assigning to a boolean or enum attribute in thread A and reading
        it in a loop in thread B (preferably with a short sleep to avoid
        wasting a lot of CPU time) ensures that thread B will wait until the
        boolean or enum has the expected value. We do not think you will need
        more advanced thread synchronization in this project.
        """
        # set payload = 0
        payload = b"".join([b"\x00" for _ in range(1008)])
        # add 1 to the seqnum of the server
        self.acknum += 1
        # add 1 to self.seqnum
        self.seqnum += 1
        # set ACK
        header = self.build_segment_header(self.seqnum,
                                           self.acknum,
                                           fin_set=True,
                                           window=self.client_window
                                           # length
                                           )
        checksum = self.in_cksum(header)

        header = self.build_segment_header(self.seqnum,
                                           self.acknum,
                                           fin_set=True,
                                           window=self.client_window,
                                           checksum=checksum
                                           # length
                                           )
        # send segment
        segment = header + payload
        self._lossy_layer.send_segment(segment)
        self.state = BTCPStates.FIN_SENT

        # search for ack/fin segment in the self.received_segments
        for i in range(RETRIES):
            timeout = time.time() + TIMEOUT
            while time.time() < timeout and not self.finack_recv:
                time.sleep(TIMER_TICK / 1000)
            if not self.finack_recv:
                self._lossy_layer.send_segment(segment)
            else:
                break

        if not self.finack_recv:
            # number of retries has been exceeded, close connection
            self.state = BTCPStates.CLOSED
        else:
            # respond with ack
            # add 1 to the seqnum of the server
            self.acknum += 1
            # add 1 to self.seqnum
            self.seqnum += 1
            # set ACK
            header = self.build_segment_header(self.seqnum,
                                               self.acknum,
                                               ack_set=True
                                               # window, length
                                               )
            checksum = self.in_cksum(header)

            header = self.build_segment_header(self.seqnum,
                                               self.acknum,
                                               ack_set=True,
                                               checksum=checksum
                                               # window, length
                                               )
            # send segment
            segment = header + payload
            self._lossy_layer.send_segment(segment)
            self.state = BTCPStates.CLOSED


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
