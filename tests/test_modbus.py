'''
Tests for the modbus master (function codes 3, 6 and 16) over RTU and TCP.
'''
import socket
import struct

import pytest

from chamberconnectlibrary.modbus import Modbus, ModbusError, ModbusRTU, ModbusTCP


def crc16(data):
    '''Modbus CRC16 as on the wire (low byte first), written independently of the library.'''
    crc = 0xFFFF
    for byte in bytearray(data):
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack('<H', crc)


def rtu(frame):
    return frame + crc16(frame)


def mbap(frame, packet_id=1):
    return struct.pack('>3H', packet_id, 0, len(frame)) + frame


def float_words(value):
    '''The two 16 bit registers of a float the way the library splits it.'''
    return struct.unpack('HH', struct.pack('f', value))


class ScriptedModbus(Modbus):
    '''Modbus without physical interface: interact() hands out prepared answers.'''

    def __init__(self, *answers, **kwargs):
        self.address = kwargs.get('address', 1)
        self.retry = kwargs.get('retry', False)
        self.answers = list(answers)
        self.packets = []

    def interact(self, packet):
        self.packets.append(packet)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def read_answer(*values, **kwargs):
    '''Answer to function 3 (without CRC / MBAP) with the given register values.'''
    return struct.pack('>BBB', kwargs.get('address', 1), 3, len(values) * 2) + struct.pack('>%dH' % len(values), *values)


WRITE_OK = struct.pack('>BBHH', 1, 6, 100, 1)


class TestCrc(object):
    def test_crc_helper_matches_known_frame(self):
        # read holding register 0 of slave 1, the classic example frame
        assert rtu(b'\x01\x03\x00\x00\x00\x01') == b'\x01\x03\x00\x00\x00\x01\x84\x0a'


class TestMakePacket(object):
    def test_read_holding(self):
        assert ScriptedModbus(address=5).make_packet(3, 100, 2) == b'\x05\x03\x00\x64\x00\x02'

    def test_write_holding(self):
        assert ScriptedModbus().make_packet(6, 100, 513) == b'\x01\x06\x00\x64\x02\x01'

    def test_write_multiple_holding(self):
        assert ScriptedModbus().make_packet(16, 100, [1, 2]) == b'\x01\x10\x00\x64\x00\x02\x04\x00\x01\x00\x02'

    def test_other_function_codes_are_not_implemented(self):
        with pytest.raises(NotImplementedError):
            ScriptedModbus().make_packet(4, 100, 1)


class TestDecodePacket(object):
    def test_read_holding_returns_register_values(self):
        assert ScriptedModbus().decode_packet(read_answer(1, 65535, 300), b'') == (1, 65535, 300)

    @pytest.mark.parametrize('function', [6, 16])
    def test_write_answers_return_nothing(self, function):
        assert ScriptedModbus().decode_packet(struct.pack('>BBHH', 1, function, 100, 1), b'') is None

    def test_answer_of_other_slave_raises_with_hex_dump(self):
        with pytest.raises(ModbusError) as error:
            ScriptedModbus(address=1).decode_packet(read_answer(7, address=2), b'\x01\x03\x00\x64\x00\x01')
        assert str(error.value) == 'Address error; Sent=01:03:00:64:00:01, Recieved=02:03:02:00:07'

    @pytest.mark.parametrize('code, text', [(1, 'Illegal Function'), (2, 'Illegal Data Address'),
                                            (6, 'Slave Device Busy'), (99, 'Unknown error code')])
    def test_exception_answer_raises_with_message(self, code, text):
        with pytest.raises(ModbusError) as error:
            ScriptedModbus().decode_packet(struct.pack('>BBB', 1, 0x83, code), b'')
        assert str(error.value) == 'Modbus Error: Exception code = %d(%s)' % (code, text)

    def test_other_function_codes_are_not_implemented(self):
        with pytest.raises(NotImplementedError):
            ScriptedModbus().decode_packet(struct.pack('>BBB', 1, 4, 0), b'')


class TestRead(object):
    def test_read_holding(self):
        master = ScriptedModbus(read_answer(11, 22))
        assert master.read_holding(100, 2) == (11, 22)
        assert master.packets == [b'\x01\x03\x00\x64\x00\x02']

    def test_read_holding_defaults_to_one_register(self):
        master = ScriptedModbus(read_answer(11))
        assert master.read_holding(100) == (11,)
        assert master.packets == [b'\x01\x03\x00\x64\x00\x01']

    def test_read_holding_signed(self):
        assert ScriptedModbus(read_answer(0xFFFF, 0x8000, 5)).read_holding_signed(100, 3) == [-1, -32768, 5]

    def test_read_holding_float(self):
        words = float_words(23.5) + float_words(-40.0)
        master = ScriptedModbus(read_answer(*words))
        assert master.read_holding_float(100, 2) == [23.5, -40.0]
        assert master.packets == [b'\x01\x03\x00\x64\x00\x04']

    def test_read_holding_float_is_rounded_to_one_decimal(self):
        assert ScriptedModbus(read_answer(*float_words(23.4567))).read_holding_float(100) == [23.5]

    def test_read_holding_string_skips_zero_registers(self):
        master = ScriptedModbus(read_answer(ord('P'), ord('r'), ord('o'), 0, 0))
        assert master.read_holding_string(100, 5) == 'Pro'

    def test_error_is_raised_without_retry(self):
        master = ScriptedModbus(ModbusError('no answer'), read_answer(11), retry=False)
        with pytest.raises(ModbusError):
            master.read_holding(100)
        assert len(master.packets) == 1

    def test_error_is_retried_once(self):
        master = ScriptedModbus(ModbusError('no answer'), read_answer(11), retry=True)
        assert master.read_holding(100) == (11,)
        assert len(master.packets) == 2

    def test_second_error_is_raised(self):
        master = ScriptedModbus(ModbusError('no answer'), ModbusError('still no answer'), retry=True)
        with pytest.raises(ModbusError):
            master.read_holding(100)


class TestWrite(object):
    def test_write_holding_single_value(self):
        master = ScriptedModbus(WRITE_OK)
        master.write_holding(100, 513)
        assert master.packets == [b'\x01\x06\x00\x64\x02\x01']

    @pytest.mark.parametrize('values', [[1, 2], (1, 2)])
    def test_write_holding_several_values(self, values):
        master = ScriptedModbus(WRITE_OK)
        master.write_holding(100, values)
        assert master.packets == [b'\x01\x10\x00\x64\x00\x02\x04\x00\x01\x00\x02']

    def test_write_holding_signed_single_value(self):
        master = ScriptedModbus(WRITE_OK)
        master.write_holding_signed(100, -2)
        assert master.packets == [b'\x01\x06\x00\x64\xff\xfe']

    def test_write_holding_signed_several_values(self):
        master = ScriptedModbus(WRITE_OK)
        master.write_holding_signed(100, [-1, 3])
        assert master.packets == [b'\x01\x10\x00\x64\x00\x02\x04\xff\xff\x00\x03']

    def test_write_holding_float_single_value(self):
        master = ScriptedModbus(WRITE_OK)
        master.write_holding_float(100, 23.5)
        assert master.packets == [b'\x01\x10\x00\x64\x00\x02\x04' + struct.pack('>2H', *float_words(23.5))]

    def test_write_holding_float_several_values(self):
        master = ScriptedModbus(WRITE_OK)
        master.write_holding_float(100, [23.5, -40.0])
        words = float_words(23.5) + float_words(-40.0)
        assert master.packets == [b'\x01\x10\x00\x64\x00\x04\x08' + struct.pack('>4H', *words)]

    def test_float_survives_write_and_read(self):
        master = ScriptedModbus(WRITE_OK)
        master.write_holding_float(100, 85.0)
        written = struct.unpack('>2H', master.packets[0][-4:])
        assert ScriptedModbus(read_answer(*written)).read_holding_float(100) == [85.0]

    def test_write_holding_string_is_padded(self):
        master = ScriptedModbus(WRITE_OK)
        master.write_holding_string(100, 'AB', length=4)
        assert master.packets == [b'\x01\x10\x00\x64\x00\x04\x08\x00\x41\x00\x42\x00\x00\x00\x00']

    def test_write_holding_string_is_truncated(self):
        master = ScriptedModbus(WRITE_OK)
        master.write_holding_string(100, 'ABCDEF', length=2, padder=32)
        assert master.packets == [b'\x01\x10\x00\x64\x00\x02\x04\x00\x41\x00\x42']

    def test_error_is_raised_without_retry(self):
        master = ScriptedModbus(ModbusError('no answer'), WRITE_OK, retry=False)
        with pytest.raises(ModbusError):
            master.write_holding(100, 1)
        assert len(master.packets) == 1

    def test_error_is_retried_once(self):
        master = ScriptedModbus(ModbusError('no answer'), WRITE_OK, retry=True)
        master.write_holding(100, 1)
        assert len(master.packets) == 2

    def test_exception_answer_of_the_slave_raises(self):
        master = ScriptedModbus(struct.pack('>BBB', 1, 0x86, 3))
        with pytest.raises(ModbusError) as error:
            master.write_holding(100, 1)
        assert 'Illegal Data Value' in str(error.value)


class TestBaseClass(object):
    def test_interact_needs_a_physical_interface(self):
        with pytest.raises(NotImplementedError):
            Modbus().interact(b'')


class TestModbusRTU(object):
    READ = b'\x01\x03\x00\x64\x00\x02'

    def test_opens_the_serial_port(self, fake_serial):
        master = ModbusRTU(1, '/dev/ttyUSB0', baud=19200, parity='E', timeout=1)
        assert fake_serial.kwargs == {
            'port': '/dev/ttyUSB0', 'baudrate': 19200, 'bytesize': 8, 'parity': 'E', 'stopbits': 1, 'timeout': 1
        }
        assert master.address == 1
        assert master.retry is True

    def test_serial_port_defaults(self, fake_serial):
        ModbusRTU(1, '/dev/ttyUSB0')
        assert fake_serial.kwargs['baudrate'] == 9600
        assert fake_serial.kwargs['parity'] == 'N'
        assert fake_serial.kwargs['timeout'] == 3

    def test_read_holding(self, fake_serial, no_sleep):
        fake_serial.reply(rtu(read_answer(11, 22)))
        assert ModbusRTU(1, '/dev/ttyUSB0').read_holding(100, 2) == (11, 22)
        assert fake_serial.sent == [rtu(self.READ)]
        assert no_sleep == [0.03]

    def test_write_holding(self, fake_serial):
        fake_serial.reply(rtu(b'\x01\x06\x00\x64\x02\x01'))
        ModbusRTU(1, '/dev/ttyUSB0').write_holding(100, 513)
        assert fake_serial.sent == [rtu(b'\x01\x06\x00\x64\x02\x01')]

    def test_write_multiple_holding(self, fake_serial):
        fake_serial.reply(rtu(b'\x01\x10\x00\x64\x00\x02'))
        ModbusRTU(1, '/dev/ttyUSB0').write_holding(100, [1, 2])
        assert fake_serial.sent == [rtu(b'\x01\x10\x00\x64\x00\x02\x04\x00\x01\x00\x02')]

    def test_exception_answer_of_the_slave_raises(self, fake_serial):
        fake_serial.reply(rtu(b'\x01\x83\x02'))
        with pytest.raises(ModbusError) as error:
            ModbusRTU(1, '/dev/ttyUSB0', retry=False).read_holding(100)
        assert 'Illegal Data Address' in str(error.value)

    def test_no_answer_raises(self, fake_serial):
        with pytest.raises(ModbusError) as error:
            ModbusRTU(1, '/dev/ttyUSB0', retry=False).read_holding(100)
        assert str(error.value) == 'The slave device did not respond.'

    def test_no_answer_is_retried_once(self, fake_serial):
        fake_serial.reply(b'', rtu(read_answer(11)))
        assert ModbusRTU(1, '/dev/ttyUSB0').read_holding(100) == (11,)
        assert len(fake_serial.sent) == 2

    def test_wrong_crc_raises_with_hex_dump(self, fake_serial):
        fake_serial.reply(read_answer(11) + b'\x00\x00')
        with pytest.raises(ModbusError) as error:
            ModbusRTU(1, '/dev/ttyUSB0', retry=False).interact(b'\x01\x03\x00\x64\x00\x01')
        assert str(error.value) == 'CRC error; Sent=01:03:00:64:00:01:c5:d5, Recieved=01:03:02:00:0b:00:00'

    def test_answer_of_other_slave_raises_with_hex_dump(self, fake_serial):
        answer = rtu(read_answer(11, address=2))
        fake_serial.reply(answer)
        with pytest.raises(ModbusError) as error:
            ModbusRTU(1, '/dev/ttyUSB0', retry=False).interact(b'\x01\x03\x00\x64\x00\x01')
        received = ':'.join('%02x' % byte for byte in bytearray(answer))
        assert str(error.value) == 'Address error; Sent=01:03:00:64:00:01:c5:d5, Recieved=' + received

    def test_answer_with_other_function_code_is_not_implemented(self, fake_serial):
        fake_serial.reply(rtu(b'\x01\x04\x02\x00\x0b'))
        with pytest.raises(NotImplementedError):
            ModbusRTU(1, '/dev/ttyUSB0').interact(b'\x01\x03\x00\x64\x00\x01')

    def test_close(self, fake_serial):
        master = ModbusRTU(1, '/dev/ttyUSB0')
        master.close()
        assert fake_serial.closed

    def test_delete_ignores_errors_of_close(self, fake_serial):
        master = ModbusRTU(1, '/dev/ttyUSB0')
        del master.serial
        master.__del__()


class TestModbusTCP(object):
    READ = b'\x01\x03\x00\x64\x00\x02'

    def test_connects_to_host_and_port(self, fake_socket, no_sleep):
        master = ModbusTCP(1, '10.0.0.5')
        assert fake_socket.address == ('10.0.0.5', 502)
        assert fake_socket.blocking is True
        assert master.address == 1
        assert no_sleep == [0.1]

    def test_port_can_be_given(self, fake_socket):
        ModbusTCP(1, '10.0.0.5', 1502)
        assert fake_socket.address == ('10.0.0.5', 1502)

    def test_read_holding(self, fake_socket):
        fake_socket.reply(mbap(read_answer(11, 22)))
        assert ModbusTCP(1, '10.0.0.5').read_holding(100, 2) == (11, 22)
        assert fake_socket.sent == [mbap(self.READ)]

    def test_write_holding_float(self, fake_socket):
        fake_socket.reply(mbap(b'\x01\x10\x00\x64\x00\x02'))
        ModbusTCP(1, '10.0.0.5').write_holding_float(100, 23.5)
        assert fake_socket.sent == [mbap(b'\x01\x10\x00\x64\x00\x02\x04' + struct.pack('>2H', *float_words(23.5)))]

    def test_closed_connection_raises(self, fake_socket):
        with pytest.raises(ModbusError) as error:
            ModbusTCP(1, '10.0.0.5').read_holding(100)
        assert 'did not respond' in str(error.value)

    def test_short_header_raises(self, fake_socket):
        fake_socket.reply(b'\x00\x01\x00')
        with pytest.raises(ModbusError) as error:
            ModbusTCP(1, '10.0.0.5').read_holding(100)
        assert 'MBAP length error; expected:6, got:3' in str(error.value)

    def test_answer_to_other_request_raises(self, fake_socket):
        fake_socket.reply(mbap(read_answer(11), packet_id=2))
        with pytest.raises(ModbusError) as error:
            ModbusTCP(1, '10.0.0.5').read_holding(100)
        assert 'MBAP id error; expected:1, got:2' in str(error.value)

    def test_silent_controller_raises_socket_timeout(self, fake_socket):
        fake_socket.timeout_when_empty = True
        with pytest.raises(socket.timeout):
            ModbusTCP(1, '10.0.0.5').read_holding(100)

    def test_close(self, fake_socket, no_sleep):
        master = ModbusTCP(1, '10.0.0.5')
        master.close()
        assert fake_socket.closed
        assert no_sleep == [0.1, 0.1]
