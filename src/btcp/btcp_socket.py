import struct
from enum import Enum


class BTCPStates(Enum):
    """Enum class that helps you implement the bTCP state machine.

    These states are NOT exhaustive! We left out some states that you will need
    to implement the bTCP state machine correctly. The intention of this enum
    is to give you some idea for states and how simple the transitions between
    them are.

    Don't use the integer values of this enum directly. Always refer to them as
    BTCPStates.CLOSED etc.
    """
    CLOSED      = 0
    ACCEPTING   = 1
    SYN_SENT    = 2
    SYN_RCVD    = 3
    ESTABLISHED = 4 # There's an obvious state that goes here. Give it a name.
    FIN_SENT    = 5
    CLOSING     = 6
    # __          = 7 # If you need more states, extend the Enum like this.


class BTCPSocket:
    """Base class for bTCP client and server sockets. Contains static helper
    methods that will definitely be useful for both sending and receiving side.
    """
    def __init__(self, window, timeout):
        self._window = window
        self._timeout = timeout


    @staticmethod
    def in_cksum(segment):
        """Compute the internet checksum of the segment given as argument.
        Consult lecture 3 for details.

        Our bTCP implementation always has an even number of bytes in a segment.

        Remember that, when computing the checksum value before *sending* the
        segment, the checksum field in the header should be set to 0x0000, and
        then the resulting checksum should be put in its place.
        """
        # Signal nonsensical request (checksum of nothing?) with 0x0000
        if not segment:
            return int(0x0000,16)

        checksum = 0
        for b in struct.iter_unpack('!H', segment): 
            checksum += b[0]
            if checksum > 2 ** 16:
                checksum -= 2 ** 16 - 1  # double minus is adding 1
        return 2 ** 16 - checksum  # invert the checksum


    @staticmethod
    def build_segment_header(seqnum, acknum,
                             syn_set=False, ack_set=False, fin_set=False,
                             window=0x01, length=0, checksum=0):
        """Pack the method arguments into a valid bTCP header using struct.pack

        This method is given because historically students had a lot of trouble
        figuring out how to pack and unpack values into / out of the header.
        We have *not* provided an implementation of the corresponding unpack
        method (see below), so inspect the code, look at the documentation for
        struct.pack, and figure out what this does, so you can implement the
        unpack method yourself.

        Of course, you are free to implement it differently, as long as you
        do so correctly *and respect the network byte order*.

        You are allowed to change the SYN, ACK, and FIN flag locations in the
        flags byte, but make sure to do so correctly everywhere you pack and
        unpack them.

        The method is written to have sane defaults for the arguments, so
        you don't have to always set all flags explicitly true/false, or give
        a checksum of 0 when creating the header for checksum computation.
        """
        flag_byte = syn_set << 2 | ack_set << 1 | fin_set
        return struct.pack("!HHBBHH",
                           seqnum, acknum, flag_byte, window, length, checksum)


    @staticmethod
    def unpack_segment_header(header):
        """Unpack the individual bTCP header field values from the header.

        Remember that Python supports multiple return values through automatic
        tupling, so it's easy to simply return all of them in one go rather
        than make a separate method for every individual field.
        """
        seqnum, acknum, flags, window, datalen, checksum = \
            struct.unpack("!HHBBHH", header)
        syn_set = flags > 3
        flags %= 4
        ack_set = flags > 1
        flags %= 2
        fin_set = flags > 0
        return seqnum, acknum, syn_set, ack_set, fin_set, window, datalen, checksum
