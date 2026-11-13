'''
Tests for the physical interfaces of the Espec controllers (TCP and serial).
'''
import socket

import pytest

from chamberconnectlibrary.especinteract import EspecError, EspecSerial, EspecTCP


class TestEspecTCP(object):
    def test_connects_to_host_and_port(self, fake_socket):
        EspecTCP(host='10.0.0.5', port=10001, timeout=7)
        assert fake_socket.address == ('10.0.0.5', 10001)
        assert fake_socket.create_connection_timeout == 7
        assert fake_socket.timeout == 7

    def test_connection_defaults(self, fake_socket):
        link = EspecTCP()
        assert fake_socket.address == ('127.0.0.1', 57732)
        assert fake_socket.timeout == 3
        assert link.address is None
        assert link.delimeter == '\r\n'

    def test_interact_sends_command_and_returns_answer(self, fake_socket):
        fake_socket.reply(b'24.1,25.0,110.0,-50.0\r\n')
        assert EspecTCP(host='chamber').interact('TEMP?') == '24.1,25.0,110.0,-50.0'
        assert fake_socket.sent == [b'TEMP?\r\n']

    def test_interact_reads_one_answer_per_command(self, fake_socket):
        fake_socket.reply(b'STANDBY\r\n', b'OK:MODE,CONSTANT\r\n', b'CONSTANT\r\n')
        link = EspecTCP(host='chamber')
        assert link.interact('MODE?') == 'STANDBY'
        assert link.interact('MODE,CONSTANT') == 'OK:MODE,CONSTANT'
        assert link.interact('MODE?') == 'CONSTANT'
        assert fake_socket.sent == [b'MODE?\r\n', b'MODE,CONSTANT\r\n', b'MODE?\r\n']

    def test_address_is_not_sent_over_tcp(self, fake_socket):
        fake_socket.reply(b'STANDBY\r\n')
        EspecTCP(host='chamber', address=5).interact('MODE?')
        assert fake_socket.sent == [b'MODE?\r\n']

    def test_characters_outside_ascii_are_dropped(self, fake_socket):
        fake_socket.reply(b'OK\r\n')
        EspecTCP(host='chamber').interact(u'TEMP, S20.0\xb0')
        assert fake_socket.sent == [b'TEMP, S20.0\r\n']

    def test_error_answer_raises_especerror(self, fake_socket):
        fake_socket.reply(b'NA:DATA OUT OF RANGE\r\n')
        with pytest.raises(EspecError) as error:
            EspecTCP(host='chamber').interact('TEMP, S500.0')
        assert 'DATA OUT OF RANGE' in str(error.value)
        assert 'TEMP, S500.0' in str(error.value)

    def test_closed_connection_raises_especerror(self, fake_socket):
        # nothing to read and recv() returns b'': the chamber closed the connection
        with pytest.raises(EspecError) as error:
            EspecTCP(host='chamber').interact('TEMP?')
        assert 'The chamber did not respond in time' in str(error.value)

    def test_connection_closed_within_the_answer_raises_especerror(self, fake_socket):
        fake_socket.reply(b'24.1,25')
        with pytest.raises(EspecError):
            EspecTCP(host='chamber').interact('TEMP?')

    def test_silent_chamber_raises_socket_timeout(self, fake_socket):
        fake_socket.timeout_when_empty = True
        with pytest.raises(socket.timeout):
            EspecTCP(host='chamber').interact('TEMP?')

    def test_close(self, fake_socket, no_sleep):
        link = EspecTCP(host='chamber')
        link.close()
        assert fake_socket.closed
        assert no_sleep == [0.1]

    def test_delete_ignores_errors_of_close(self, fake_socket):
        link = EspecTCP(host='chamber')
        del link.socket
        link.__del__()

    @pytest.mark.xfail(strict=True, reason='EspecTCP.interact() always cuts two characters off the answer, '
                                           'whatever the length of the delimiter is')
    def test_delimiter_with_one_character(self, fake_socket):
        fake_socket.reply(b'STANDBY\r')
        assert EspecTCP(host='chamber', delimeter='\r').interact('MODE?') == 'STANDBY'


class TestEspecSerial(object):
    def test_opens_the_serial_port(self, fake_serial):
        EspecSerial(port='/dev/ttyUSB0', baud=19200, timeout=5)
        assert fake_serial.kwargs == {
            'port': '/dev/ttyUSB0', 'baudrate': 19200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1, 'timeout': 5
        }

    def test_serial_port_defaults(self, fake_serial):
        link = EspecSerial(port='/dev/ttyUSB0')
        assert fake_serial.kwargs['baudrate'] == 9600
        assert fake_serial.kwargs['timeout'] == 3
        assert link.address is None
        assert link.delimeter == '\r\n'

    def test_interact_writes_bytes_and_returns_answer(self, fake_serial):
        # the fake port refuses str like pyserial does on python 3
        fake_serial.reply(b'24.1,25.0,110.0,-50.0\r\n')
        assert EspecSerial(port='/dev/ttyUSB0').interact('TEMP?') == '24.1,25.0,110.0,-50.0'
        assert fake_serial.sent == [b'TEMP?\r\n']

    def test_interact_with_address(self, fake_serial):
        fake_serial.reply(b'STANDBY\r\n')
        assert EspecSerial(port='/dev/ttyUSB0', address=3).interact('MODE?') == 'STANDBY'
        assert fake_serial.sent == [b'3,MODE?\r\n']

    def test_characters_outside_ascii_are_dropped(self, fake_serial):
        fake_serial.reply(b'OK\r\n')
        EspecSerial(port='/dev/ttyUSB0').interact(u'TEMP, S20.0\xb0')
        assert fake_serial.sent == [b'TEMP, S20.0\r\n']

    def test_error_answer_raises_especerror(self, fake_serial):
        fake_serial.reply(b'NA:CMD ERR\r\n')
        with pytest.raises(EspecError) as error:
            EspecSerial(port='/dev/ttyUSB0').interact('FOO?')
        assert 'CMD ERR' in str(error.value)

    def test_no_answer_raises_especerror(self, fake_serial):
        with pytest.raises(EspecError) as error:
            EspecSerial(port='/dev/ttyUSB0').interact('TEMP?')
        assert 'The chamber did not respond in time' in str(error.value)

    def test_delimiter_with_one_character(self, fake_serial):
        fake_serial.reply(b'STANDBY\r')
        link = EspecSerial(port='/dev/ttyUSB0', delimeter='\r')
        assert link.interact('MODE?') == 'STANDBY'
        assert fake_serial.sent == [b'MODE?\r']

    def test_close(self, fake_serial):
        EspecSerial(port='/dev/ttyUSB0').close()
        assert fake_serial.closed

    def test_delete_ignores_errors_of_close(self, fake_serial):
        link = EspecSerial(port='/dev/ttyUSB0')
        del link.serial
        link.__del__()
