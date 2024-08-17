import asyncio
from binascii import crc32
from collections import deque
from context import Packager
import unittest


class TestFlags(unittest.TestCase):
    def test_byte_values(self):
        flags = Packager.Flags(0)
        assert not flags.error
        assert not flags.throttle
        assert not flags.ask
        assert not flags.ack
        assert not flags.rtx
        assert not flags.reserved1
        assert not flags.reserved2
        assert not flags.mode

        flags.error = True
        flags.throttle = True
        flags.ask = True
        flags.ack = True
        flags.rtx = True
        flags.reserved1 = True
        flags.reserved2 = True
        flags.mode = True

        assert int(flags) == 255


class TestSchema(unittest.TestCase):
    def test_SCHEMA_IDS(self):
        all_schemas = Packager.SCHEMA_IDS
        assert type(all_schemas) is list
        assert all([type(s) is int for s in all_schemas])
        sequence_schemas = Packager.SCHEMA_IDS_SUPPORT_SEQUENCE
        assert type(sequence_schemas) is list
        assert all([type(s) is int for s in sequence_schemas])
        routing_schemas = Packager.SCHEMA_IDS_SUPPORT_ROUTING
        assert type(routing_schemas) is list
        assert all([type(s) is int for s in routing_schemas])

        assert len(sequence_schemas) < len(all_schemas)
        assert len(routing_schemas) < len(all_schemas)
        assert all([i in all_schemas for i in sequence_schemas])
        assert all([i in all_schemas for i in routing_schemas])

    def test_get_schema(self):
        schema = Packager.get_schema(0)
        assert isinstance(schema, Packager.Schema)

    def test_get_schemas(self):
        ids = Packager.SCHEMA_IDS
        schemas = Packager.get_schemas(ids)
        assert type(schemas) is list
        for s in schemas:
            assert type(s) is Packager.Schema, s

    def test_pack_and_unpack_schema0(self):
        schema = Packager.get_schema(0)
        body = b'hello world'
        data = schema.pack(Packager.Flags(0), {
            'packet_id': b'\x00',
            'body': body,
        })
        assert type(data) is bytes
        packet = schema.unpack(data)
        assert type(packet) is dict
        assert packet['body'] == body


class TestPacket(unittest.TestCase):
    def test_setting_properties_then_pack_unpack_e2e(self):
        schema = Packager.get_schema(0)
        packet = Packager.Packet(
            schema,
            Packager.Flags(0),
            {
                'packet_id': 0,
                'body': b'hello world',
            }
        )
        packet.body = b'hulloo'
        packet.id = 1

        packed = packet.pack()
        assert type(packed) is bytes
        unpacked = Packager.Packet.unpack(schema, packed)
        assert isinstance(unpacked, Packager.Packet)
        assert unpacked.body == packet.body
        assert unpacked.schema == packet.schema
        assert unpacked.flags == packet.flags, (unpacked.flags, packet.flags)

    def test_set_checksum(self):
        schema = Packager.get_schema(Packager.SCHEMA_IDS_SUPPORT_CHECKSUM[0])
        data = b'doo doodoo bitcoin something doodoo doo'
        packet = Packager.Packet(
            schema,
            Packager.Flags(0),
            {
                'packet_id': 0,
                'body': data
            }
        )
        assert 'checksum' not in packet.fields
        packet.set_checksum()
        assert 'checksum' in packet.fields
        assert packet.fields['checksum'] == crc32(packet.body).to_bytes(4, 'big')


def xor(b1: bytes, b2: bytes) -> bytes:
    while len(b2) > len(b1):
        b1 += b'\x00'
    b3 = bytearray(len(b1))
    for i in range(len(b2)):
        b3[i] = b1[i] ^ b2[i]
    return b3

def xor_diff(b1: bytes, b2: bytes) -> tuple[str, str]:
    b3 = xor(b1, b2)
    b4 = xor(b1, b2)
    for i in range(len(b3)):
        if b3[i] != 0:
            b3[i] = b1[i] if i < len(b1) else 255
            b4[i] = b2[i] if i < len(b2) else 255
    return (b3.hex(), b4.hex())


class TestSequence(unittest.TestCase):
    def test_set_data_and_get_packet(self):
        with self.assertRaises(AssertionError) as e:
            Packager.Sequence(Packager.get_schema(0), 1)
        assert 'schema must include' in str(e.exception)

        schema = Packager.get_schema(Packager.SCHEMA_IDS_SUPPORT_SEQUENCE[0])
        data = b''.join([(i%256).to_bytes(1, 'big') for i in range(1200)])
        flags = Packager.Flags(0)
        sequence = Packager.Sequence(schema, 0, len(data))
        sequence.set_data(data)
        assert sequence.data == data
        assert len(sequence.get_missing()) == 0, sequence.get_missing()
        assert data == b''.join([
            sequence.get_packet(i, flags, {}).body
            for i in range(sequence.seq_size)
        ])

    def test_e2e(self):
        with self.assertRaises(AssertionError) as e:
            Packager.Sequence(Packager.get_schema(0), 1)
        assert 'schema must include' in str(e.exception)

        schema = Packager.get_schema(Packager.SCHEMA_IDS_SUPPORT_SEQUENCE[0])
        data = b''.join([(i%256).to_bytes(1, 'big') for i in range(1221)])
        flags = Packager.Flags(0)
        sequence = Packager.Sequence(schema, 0, len(data))
        sequence.set_data(data)
        seq2 = Packager.Sequence(schema, 0, seq_size=sequence.seq_size)
        assert len(sequence.get_missing()) == 0, sequence.get_missing()
        assert len(seq2.get_missing()) > 0

        for i in range(sequence.seq_size):
            packet = sequence.get_packet(i, flags, {})
            assert isinstance(packet, Packager.Packet)
            if i % 5:
                assert not seq2.add_packet(packet)

        assert len(seq2.get_missing()) > 0
        for id in seq2.get_missing():
            seq2.add_packet(sequence.get_packet(id, flags, {}))

        assert len(seq2.get_missing()) == 0
        assert seq2.data == data, \
            (len(seq2.data), len(data), sequence.seq_size, seq2.seq_size,
             xor_diff(seq2.data, data))


outbox = deque()
inbox = deque()
castbox = deque()
config = {}

def configure(_: Packager.Interface, data: dict):
    for key, value in data.items():
        config[key] = value

def receive():
    return inbox.popleft() if len(inbox) else None

def send(datagram: Packager.Datagram):
    outbox.append(datagram)

def broadcast(datagram: Packager.Datagram):
    castbox.append(datagram)


class TestInterface(unittest.TestCase):
    mock_interface: Packager.Interface
    @classmethod
    def setUpClass(cls) -> None:
        cls.mock_interface = Packager.Interface(
            'mock',
            1200,
            configure,
            Packager.SCHEMA_IDS,
            receive,
            send,
            broadcast
        )
        return super().setUpClass()

    def test_validate(self):
        assert self.mock_interface.validate()

    def test_configure(self):
        assert 'thing' not in config
        self.mock_interface.configure({'thing': 123})
        assert 'thing' in config

    def test_receive_process(self):
        assert len(inbox) == 0
        dgram = Packager.Datagram(b'hello', b'mac address')
        inbox.append(dgram)
        assert len(self.mock_interface.inbox) == 0
        asyncio.run(self.mock_interface.process())
        assert len(self.mock_interface.inbox) == 1
        assert self.mock_interface.receive() == dgram
        assert len(self.mock_interface.inbox) == 0
        assert len(inbox) == 0

    def test_send_process(self):
        assert len(outbox) == 0
        dgram = Packager.Datagram(b'hello', b'mac address')
        assert len(self.mock_interface.outbox) == 0
        self.mock_interface.send(dgram)
        assert len(self.mock_interface.outbox) == 1
        assert len(outbox) == 0
        asyncio.run(self.mock_interface.process())
        assert len(self.mock_interface.outbox) == 0
        assert len(outbox) == 1

    def test_broadcast_process(self):
        assert len(castbox) == 0
        dgram = Packager.Datagram(b'hello', b'mac address')
        assert len(self.mock_interface.castbox) == 0
        self.mock_interface.broadcast(dgram)
        assert len(self.mock_interface.castbox) == 1
        assert len(castbox) == 0
        asyncio.run(self.mock_interface.process())
        assert len(self.mock_interface.castbox) == 0
        assert len(castbox) == 1


class TestPeer(unittest.TestCase):
    ...


class TestPackager(unittest.TestCase):
    # def test_add_peer_remove_peer_e2e(self):
        # ...
    ...


if __name__ == '__main__':
    unittest.main()
