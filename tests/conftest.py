'''
Shared mocks for the tests.

No test talks to a real controller: the physical interfaces (TCP socket, serial port)
are replaced by fakes that record what the library sends and hand back prepared answers.
'''
import collections
import socket
import types

import pytest

from chamberconnectlibrary import especinteract, modbus
from chamberconnectlibrary.especinteract import EspecError


class FakeLink(object):
    '''
    Common part of the fake socket and the fake serial port.

    replies: every write of the library releases the next prepared answer
    incoming: bytes the controller has "sent" and the library has not read yet
    '''

    def __init__(self):
        self.sent = []
        self.replies = collections.deque()
        self.incoming = bytearray()
        self.closed = False

    def reply(self, *answers):
        '''Prepare the answers to the next writes of the library.'''
        self.replies.extend(answers)
        return self

    def _write(self, data):
        self.sent.append(bytes(data))
        if self.replies:
            self.incoming += self.replies.popleft()
        return len(data)

    def _read(self, size):
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data

    def close(self):
        self.closed = True


class FakeSocket(FakeLink):
    '''
    Stands in for socket.socket. When nothing is left to read, recv() behaves like
    a closed connection (returns b'') or, with timeout_when_empty, like a silent one.
    '''

    def __init__(self):
        super(FakeSocket, self).__init__()
        self.address = None
        self.timeout = None
        self.blocking = None
        self.timeout_when_empty = False

    def connect(self, address):
        self.address = address

    def settimeout(self, timeout):
        self.timeout = timeout

    def setblocking(self, blocking):
        self.blocking = blocking

    def send(self, data):
        return self._write(data)

    def recv(self, size):
        if not self.incoming and self.timeout_when_empty:
            raise socket.timeout('timed out')
        return self._read(size)


class FakeSerial(FakeLink):
    '''Stands in for serial.Serial. An empty read() is what pyserial returns on a timeout.'''

    def __init__(self, **kwargs):
        super(FakeSerial, self).__init__()
        self.kwargs = kwargs

    def write(self, data):
        if not isinstance(data, (bytes, bytearray)):
            # this is what pyserial does on python 3
            raise TypeError('unicode strings are not supported, please encode to bytes: %r' % (data,))
        return self._write(data)

    def read(self, size=1):
        return self._read(size)


class FakeEspecLink(object):
    '''
    Stands in for EspecTCP / EspecSerial below the P300 and SCP220 classes: maps the
    command strings of the library to the answers of the controller.

    answers: {command: answer}, an answer can also be an exception (is raised) or a
             list (one entry per call, the last one is repeated)
    default: answer to every command that is not in answers. Without a default such
             a command raises EspecError, like a controller answering "NA:CMD ERR"
    '''

    def __init__(self, answers=None, default=None):
        self.answers = dict(answers or {})
        self.default = default
        self.commands = []
        self.closed = False

    def interact(self, message):
        self.commands.append(message)
        if message in self.answers:
            answer = self.answers[message]
            if isinstance(answer, list):
                answer = answer.pop(0) if len(answer) > 1 else answer[0]
        elif self.default is not None:
            answer = self.default
        else:
            raise EspecError('EspecError: command:"%s" generated Error:"CMD ERR"' % message)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    '''The library sleeps after closing a connection and between modbus frames.'''
    sleeps = []
    fake_time = types.SimpleNamespace(sleep=sleeps.append)
    monkeypatch.setattr(especinteract, 'time', fake_time)
    monkeypatch.setattr(modbus, 'time', fake_time)
    return sleeps


@pytest.fixture
def fake_socket(monkeypatch):
    '''Every TCP connection the library opens ends in this fake socket.'''
    fake = FakeSocket()

    def create_connection(address, timeout=None):
        fake.address = address
        fake.create_connection_timeout = timeout
        return fake

    monkeypatch.setattr(socket, 'create_connection', create_connection)
    monkeypatch.setattr(socket, 'socket', lambda *args, **kwargs: fake)
    return fake


@pytest.fixture
def fake_serial(monkeypatch):
    '''Every serial port the library opens ends in this fake port.'''
    fake = FakeSerial()

    def open_port(**kwargs):
        fake.kwargs = kwargs
        return fake

    monkeypatch.setattr(especinteract.serial, 'Serial', open_port)
    return fake


@pytest.fixture
def espec_link(monkeypatch):
    '''
    Replaces the physical interface below P300 / SCP220 (TCP and serial). Fill
    espec_link.answers before or after creating the controller object.
    '''
    from chamberconnectlibrary import p300
    link = FakeEspecLink()
    link.created_with = []

    def create(**kwargs):
        link.created_with.append(kwargs)
        return link

    monkeypatch.setattr(p300, 'EspecTCP', create)
    monkeypatch.setattr(p300, 'EspecSerial', create)
    return link
