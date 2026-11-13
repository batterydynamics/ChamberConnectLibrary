'''
Tests for the upper level interface of the Espec controllers (P300, SCP220).

The tests run the real stack Espec -> P300 / SCP220 on top of the fake link of conftest.py:
they prepare the answers of the controller and look at the commands the library sends.
'''
import datetime
import types

import pytest

from chamberconnectlibrary import controllerinterface, espec
from chamberconnectlibrary.controllerinterface import ControllerInterfaceError
from chamberconnectlibrary.espec import Espec
from chamberconnectlibrary.especinteract import EspecError
from chamberconnectlibrary.p300 import P300
from chamberconnectlibrary.scp220 import SCP220

STEP = '%d,TEMP25.0,TEMP RAMP OFF,HUMI50,HUMI RAMP OFF,TIME%s,GRANTY OFF,REF9'
STEP_PTC = '%d,TEMP25.0,TEMP RAMP OFF,PTC ON,HUMI50,HUMI RAMP OFF,TIME%s,GRANTY OFF,REF9,DEVP10.0,DEVN-10.0'

# a temperature / humidity chamber with product temperature control in constant mode,
# program 1 has the steps 1:00, 2:00 and 0:30 and repeats the steps 1 to 2 once
CHAMBER = {
    'ROM?': 'P3001.23',
    'DATE?': '26.11/13',
    'TIME?': '12:34:56',
    'MODE?': 'CONSTANT',
    'MODE?,DETAIL': 'CONSTANT',
    'MON?': '23.5,45,CONSTANT,0',
    'ALARM?': '0',
    'TEMP?': '23.5,25.0,100.0,-40.0',
    'HUMI?': '45,50,95,20',
    '%?': '2,60.0,30.0',
    'CONSTANT SET?,TEMP': '20.0,ON',
    'CONSTANT SET?,HUMI': '55,ON',
    'CONSTANT SET?,REF': '50.0',
    'CONSTANT SET?,RELAY': '2,1,5',
    'CONSTANT SET?,PTC': 'ON,10.0,-10.0',
    'RELAY?': '2,1,3',
    'TEMP PTC?': 'ON,22.0,23.5,30.0,25.0,10.0,-10.0',
    'IPSET?': '192.168.0.10,255.255.255.0,192.168.0.1',
    'PRGM SET?': 'RAM:1,SOAK,END(STANDBY)',
    'PRGM MON?': '1,25.0,50,0:30,1,0',
    'PRGM USE?,RAM:1': 'SOAK,26.11/13',
    'PRGM USE?,RAM:2': 'CYCLE,26.11/13',
    'PRGM DATA?,RAM:1': '3,<SOAK>,COUNT,A(1.2.1),B(0.0.0),END(STANDBY)',
    'PRGM DATA?,RAM:2': '1,<CYCLE>,COUNT,A(0.0.0),B(0.0.0),END(OFF)',
    'PRGM DATA?,RAM:1,DETAIL': '100.0,-40.0,95,20,TEMPOFF,HUMIOFF',
    'PRGM DATA?,RAM:1,STEP1': STEP % (1, '1:00'),
    'PRGM DATA?,RAM:1,STEP2': STEP % (2, '2:00'),
    'PRGM DATA?,RAM:1,STEP3': STEP % (3, '0:30'),
    'PRGM DATA PTC?,RAM:1': '3,<SOAK>,COUNT,A(1.2.1),B(0.0.0),END(STANDBY)',
    'PRGM DATA PTC?,RAM:1,DETAIL': '100.0,-40.0,95,20,TEMPOFF,HUMIOFF',
    'PRGM DATA PTC?,RAM:1,STEP1': STEP_PTC % (1, '1:00'),
    'PRGM DATA PTC?,RAM:1,STEP2': STEP_PTC % (2, '2:00'),
    'PRGM DATA PTC?,RAM:1,STEP3': STEP_PTC % (3, '0:30'),
}

ALARM_CODES = [0, 1, 2, 3, 6, 7, 8, 9, 10, 11, 12, 18, 19, 21, 22, 23, 26, 30, 31, 40, 41, 43, 46, 48, 50, 51, 99]


def not_supported(command):
    '''What the link raises when the controller answers "NA:CMD ERR".'''
    return EspecError('EspecError: command:"%s" generated Error:"CMD ERR"' % command)


def connected(**kwargs):
    ctlr = Espec(interface='TCP', host='chamber', port=57732, timeout=3, **kwargs)
    ctlr.connect()
    return ctlr


def written(link):
    '''The commands that change something (every query of the P300 contains a "?").'''
    return [command for command in link.commands if '?' not in command]


def program(counter_a=(0, 0, 0), counter_b=(0, 0, 0)):
    '''A program like read_prgm() returns it, reduced to what get_prgm_time() needs.'''
    keys = ('start', 'end', 'cycles')
    return {
        'steps': [{'time': {'hour': 1, 'minute': 0}}, {'time': {'hour': 2, 'minute': 0}},
                  {'time': {'hour': 0, 'minute': 30}}],
        'counter_a': dict(zip(keys, counter_a)),
        'counter_b': dict(zip(keys, counter_b)),
    }


class Clock(object):
    '''Stands in for the time module in espec.py: every call of time() is one second later.'''

    def __init__(self):
        self.now = 1000.0

    def time(self):
        self.now += 1
        return self.now


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(espec, 'time', fake)
    return fake


@pytest.fixture
def chamber(espec_link):
    '''The link to the chamber described by CHAMBER, it accepts every write command.'''
    espec_link.answers.update(CHAMBER)
    espec_link.default = 'OK'
    return espec_link


@pytest.fixture
def ctlr(chamber):
    return connected()


class TestInit(object):
    def test_defaults(self):
        ctlr = Espec()
        assert ctlr.ctlr_type == 'P300'
        assert (ctlr.loops, ctlr.cascades) == (1, 0)
        assert ctlr.freshness == 0
        assert ctlr.total_programs == 40
        assert (ctlr.alarms, ctlr.events, ctlr.profiles) == (27, 12, True)
        assert ctlr.client is None

    def test_scp220_has_30_programs(self):
        assert Espec(ctlr_type='SCP220').total_programs == 30

    @pytest.mark.parametrize('loops, cascades, loop_map', [
        (1, 0, [{'type': 'loop', 'num': 1}]),
        (2, 0, [{'type': 'loop', 'num': 1}, {'type': 'loop', 'num': 2}]),
        (0, 1, [{'type': 'cascade', 'num': 1}]),
        (1, 1, [{'type': 'cascade', 'num': 1}, {'type': 'loop', 'num': 1}]),
    ])
    def test_loop_map(self, loops, cascades, loop_map):
        assert Espec(loops=loops, cascades=cascades).loop_map == loop_map

    @pytest.mark.parametrize('name', ['Temperature', 'temperature', 'Temp', 'temp'])
    def test_temperature_loop_has_names(self, name):
        ctlr = Espec()
        assert ctlr.loop_map[ctlr.named_loop_map[name]] == {'type': 'loop', 'num': 1}

    @pytest.mark.parametrize('name', ['Humidity', 'humidity', 'Hum', 'hum'])
    def test_humidity_loop_has_names(self, name):
        ctlr = Espec(loops=2)
        assert ctlr.loop_map[ctlr.named_loop_map[name]] == {'type': 'loop', 'num': 2}

    @pytest.mark.xfail(strict=True, reason='with two loops the names of the humidity loop replace the names of the '
                                           'temperature loop instead of being added to them')
    def test_temperature_keeps_its_names_on_a_humidity_chamber(self, chamber):
        assert connected(loops=2).get_loop('temperature', 'units') == {'units': '\xb0C'}

    @pytest.mark.xfail(strict=True, reason='with a cascade the humidity loop gets the number 1 in loop_map, '
                                           'but 1 is the temperature loop for every loop function')
    def test_humidity_name_on_a_chamber_with_cascade(self, chamber):
        assert connected(loops=1, cascades=1).get_loop('humidity', 'units') == {'units': '%RH'}


class TestConnection(object):
    def test_connect_p300_over_tcp(self, espec_link):
        ctlr = Espec(interface='TCP', host='chamber', port=57732, timeout=3)
        ctlr.connect()
        assert type(ctlr.client) is P300
        assert espec_link.created_with == [{'host': 'chamber', 'address': 1, 'port': 57732, 'timeout': 3}]

    def test_connect_scp220(self, espec_link):
        ctlr = Espec(interface='TCP', host='chamber', port=57732, timeout=3, ctlr_type='SCP220')
        ctlr.connect()
        assert type(ctlr.client) is SCP220

    def test_port_and_timeout_are_converted_to_int(self, espec_link):
        Espec(interface='TCP', host='chamber', port='10001', timeout='5', adr=4).connect()
        assert espec_link.created_with == [{'host': 'chamber', 'address': 4, 'port': 10001, 'timeout': 5}]

    @pytest.mark.xfail(strict=True, reason='connect() calls int(self.port), a TypeError when no port was given, '
                                           'as in every example of controllerinterface.md')
    @pytest.mark.parametrize('interface_params', [
        {'interface': 'Serial', 'baudrate': 19200, 'serialport': '/dev/ttyUSB0', 'adr': 1},
        {'interface': 'TCP', 'host': '10.30.100.55'},
    ])
    def test_connect_with_the_documented_parameters(self, espec_link, interface_params):
        ctlr = Espec(**interface_params)
        ctlr.connect()
        assert ctlr.client is not None

    def test_unsupported_controller_type(self, espec_link):
        with pytest.raises(ValueError) as error:
            Espec(interface='TCP', host='chamber', port=57732, ctlr_type='F4T').connect()
        assert 'F4T' in str(error.value)
        assert espec_link.created_with == []

    def test_close(self, ctlr, chamber):
        ctlr.close()
        assert chamber.closed
        assert ctlr.client is None

    def test_close_without_connection(self):
        ctlr = Espec()
        ctlr.close()
        assert ctlr.client is None

    def test_call_without_connection_connects_and_closes(self, chamber):
        ctlr = Espec(interface='TCP', host='chamber', port=57732, timeout=3)
        assert ctlr.get_status() == 'Constant'
        assert len(chamber.created_with) == 1
        assert chamber.closed
        assert ctlr.client is None

    def test_call_with_connection_keeps_it_open(self, ctlr, chamber):
        ctlr.get_status()
        assert ctlr.client is not None
        assert not chamber.closed


class TestCache(object):
    def test_every_call_asks_the_controller_by_default(self, ctlr, chamber):
        ctlr.get_loop_pv(1)
        chamber.answers['TEMP?'] = '30.0,25.0,100.0,-40.0'
        assert ctlr.get_loop_pv(1) == {'air': 30.0}
        assert chamber.commands == ['TEMP?', 'TEMP?']

    def test_fresh_answer_is_used_again(self, chamber):
        ctlr = connected(freshness=60)
        ctlr.get_loop_pv(1)
        chamber.answers['TEMP?'] = '30.0,25.0,100.0,-40.0'
        assert ctlr.get_loop_pv(1) == {'air': 23.5}
        assert ctlr.get_loop_range(1) == {'max': 100.0, 'min': -40.0}
        assert chamber.commands == ['TEMP?']

    def test_old_answer_is_read_again(self, chamber, clock):
        ctlr = connected(freshness=60)
        ctlr.get_loop_pv(1)
        chamber.answers['TEMP?'] = '30.0,25.0,100.0,-40.0'
        clock.now += 60
        assert ctlr.get_loop_pv(1) == {'air': 30.0}
        assert chamber.commands == ['TEMP?', 'TEMP?']

    def test_cached_passes_arguments_on(self):
        def read(*args, **kwargs):
            return args, kwargs
        assert Espec().cached(read, 1, detail=True) == ((1,), {'detail': True})

    @pytest.mark.xfail(strict=True, reason='cached() only uses the function name as key and ignores the arguments, '
                                           'so the name of the program asked first is returned for every program')
    def test_cache_distinguishes_arguments(self, chamber):
        ctlr = connected(freshness=60)
        assert ctlr.get_prgm_name(1) == 'SOAK'
        assert ctlr.get_prgm_name(2) == 'CYCLE'


class TestRaw(object):
    def test_returns_the_answer(self, ctlr, chamber):
        assert ctlr.raw('TEMP?') == '23.5,25.0,100.0,-40.0'
        assert chamber.commands == ['TEMP?']

    def test_error_of_the_controller_is_returned_as_answer(self, ctlr, chamber):
        chamber.answers['FOO?'] = not_supported('FOO?')
        assert ctlr.raw('FOO?') == 'NA:CMD ERR'

    def test_timeout_is_returned_as_answer(self, ctlr, chamber):
        chamber.answers['TEMP?'] = EspecError('The chamber did not respond in time')
        assert ctlr.raw('TEMP?') == 'NA: SERIAL TIMEOUT'


class TestRefrigAndDatetime(object):
    def test_get_refrig(self, ctlr):
        assert ctlr.get_refrig() == {'mode': 'manual', 'setpoint': 50.0}

    @pytest.mark.parametrize('value, command', [
        ({'mode': 'auto', 'setpoint': 0}, 'SET,REF9'),
        ({'mode': 'manual', 'setpoint': 20}, 'SET,REF1'),
        ({'mode': 'off'}, 'SET,REF0'),
    ])
    def test_set_refrig(self, ctlr, chamber, value, command):
        ctlr.set_refrig(value)
        assert chamber.commands == [command]

    def test_get_datetime(self, ctlr):
        assert ctlr.get_datetime() == datetime.datetime(2026, 11, 13, 12, 34, 56)

    def test_set_datetime(self, ctlr, chamber):
        ctlr.set_datetime(datetime.datetime(2026, 11, 13, 8, 5, 9))
        assert chamber.commands == ['TIME,8:5:9', 'DATE,26.11/13. FRI']


class TestLoopGetters(object):
    @pytest.mark.parametrize('method, loop, expected', [
        ('get_loop_sp', 1, {'constant': 20.0, 'current': 25.0}),
        ('get_loop_sp', 2, {'constant': 55.0, 'current': 50.0}),
        ('get_loop_pv', 1, {'air': 23.5}),
        ('get_loop_pv', 2, {'air': 45.0}),
        ('get_loop_range', 1, {'max': 100.0, 'min': -40.0}),
        ('get_loop_range', 2, {'max': 95.0, 'min': 20.0}),
        ('get_loop_en', 1, {'constant': True, 'current': True}),
        ('get_loop_en', 2, {'constant': True, 'current': True}),
        ('get_loop_units', 1, '\xb0C'),
        ('get_loop_units', 2, '%RH'),
        ('get_loop_mode', 1, {'constant': 'On', 'current': 'On'}),
        ('get_loop_mode', 2, {'constant': 'On', 'current': 'On'}),
        ('get_loop_modes', 1, ['On']),
        ('get_loop_modes', 2, ['Off', 'On']),
        ('get_loop_power', 1, {'constant': 60.0, 'current': 60.0}),
        ('get_loop_power', 2, {'constant': 30.0, 'current': 30.0}),
    ])
    def test_loop_value(self, ctlr, method, loop, expected):
        assert getattr(ctlr, method)(loop) == expected

    @pytest.mark.parametrize('name, expected', [
        ('temp', '\xb0C'), ('temperature', '\xb0C'), ('Temperature', '\xb0C'),
        ('humi', '%RH'), ('humidity', '%RH'), ('Humidity', '%RH'),
    ])
    def test_loop_can_be_given_by_name(self, ctlr, name, expected):
        assert ctlr.get_loop_units(name) == expected

    def test_humidity_switched_off(self, ctlr, chamber):
        chamber.answers.update({'HUMI?': '45,OFF,95,20', 'CONSTANT SET?,HUMI': '55,OFF'})
        assert ctlr.get_loop_en(2) == {'constant': False, 'current': False}
        assert ctlr.get_loop_mode(2) == {'constant': 'Off', 'current': 'Off'}
        assert ctlr.get_loop_sp(2) == {'constant': 55.0, 'current': 0}

    @pytest.mark.parametrize('mode', ['STANDBY', 'OFF'])
    def test_loop_is_currently_off_when_the_chamber_does_not_run(self, ctlr, chamber, mode):
        chamber.answers['MODE?'] = mode
        assert ctlr.get_loop_mode(1) == {'constant': 'On', 'current': 'Off'}
        assert ctlr.get_loop_mode(2) == {'constant': 'On', 'current': 'Off'}

    def test_power_on_a_chamber_without_humidity(self, ctlr, chamber):
        chamber.answers['%?'] = '1,60.0'
        assert ctlr.get_loop_power(1) == {'constant': 60.0, 'current': 60.0}
        assert ctlr.get_loop(2, 'loop', 'power') == {'power': None}

    @pytest.mark.parametrize('method', ['get_loop_sp', 'get_loop_mode', 'get_loop_modes'])
    def test_unknown_loop(self, ctlr, method):
        with pytest.raises(ValueError) as error:
            getattr(ctlr, method)(3)
        assert str(error.value) == 'The P300 controller only supports 2 loops (1:temperature,2:humidity)'

    @pytest.mark.xfail(strict=True, reason='self.lpd[N] raises KeyError for an unknown loop, the branch that should '
                                           'raise ValueError(self.lp_exmsg) cannot be reached')
    @pytest.mark.parametrize('method, args', [
        ('get_loop_pv', ()),
        ('get_loop_range', ()),
        ('get_loop_en', ()),
        ('get_loop_units', ()),
        ('get_loop_power', ()),
        ('set_loop_sp', (20.0,)),
        ('set_loop_range', ({'min': 0.0, 'max': 50.0},)),
        ('set_loop_en', (True,)),
    ])
    def test_unknown_loop_raises_valueerror(self, ctlr, method, args):
        with pytest.raises(ValueError):
            getattr(ctlr, method)(3, *args)


class TestLoopSetters(object):
    @pytest.mark.parametrize('method, loop, value, commands', [
        ('set_loop_sp', 1, 30.0, ['TEMP, S30.0']),
        ('set_loop_sp', 1, {'constant': 30.0}, ['TEMP, S30.0']),
        ('set_loop_sp', 2, 60.0, ['HUMI, S60.0']),
        ('set_loop_sp', 'humidity', {'constant': 60.0}, ['HUMI, S60.0']),
        ('set_loop_range', 1, {'min': -20.0, 'max': 80.0}, ['TEMP, L-20.0', 'TEMP, H80.0']),
        ('set_loop_range', 2, {'min': 10.0, 'max': 90.0}, ['HUMI, L10.0', 'HUMI, H90.0']),
        ('set_loop_en', 1, True, []),
        ('set_loop_en', 1, False, []),
        ('set_loop_en', 2, True, ['HUMI, S55.0']),
        ('set_loop_en', 2, {'constant': True}, ['HUMI, S55.0']),
        ('set_loop_en', 2, False, ['HUMI,SOFF']),
        ('set_loop_en', 2, {'constant': False}, ['HUMI,SOFF']),
        ('set_loop_mode', 1, 'On', []),
        ('set_loop_mode', 2, 'On', ['HUMI, S55.0']),
        ('set_loop_mode', 2, 'ON', ['HUMI, S55.0']),
        ('set_loop_mode', 2, 'on', ['HUMI, S55.0']),
        ('set_loop_mode', 2, 'Off', ['HUMI,SOFF']),
        ('set_loop_mode', 2, 'OFF', ['HUMI,SOFF']),
        ('set_loop_mode', 2, {'constant': 'off'}, ['HUMI,SOFF']),
    ])
    def test_loop_setter(self, ctlr, chamber, method, loop, value, commands):
        getattr(ctlr, method)(loop, value)
        assert written(chamber) == commands

    @pytest.mark.parametrize('value', [{'min': 0.0}, {'max': 50.0}])
    def test_range_needs_min_and_max(self, ctlr, value):
        with pytest.raises(AttributeError):
            ctlr.set_loop_range(1, value)

    def test_mode_has_to_be_on_or_off(self, ctlr, chamber):
        with pytest.raises(ValueError):
            ctlr.set_loop_mode(2, 'Auto')
        assert written(chamber) == []

    def test_mode_of_unknown_loop(self, ctlr):
        with pytest.raises(ValueError):
            ctlr.set_loop_mode(3, 'On')

    @pytest.mark.parametrize('method, args', [
        ('set_loop_power', (1, 50.0)),
        ('set_cascade_power', (1, 50.0)),
        ('set_prgm_name', (1, 'SOAK')),
    ])
    def test_not_implemented(self, ctlr, method, args):
        with pytest.raises(NotImplementedError):
            getattr(ctlr, method)(*args)


class TestSetLoop(object):
    @pytest.mark.parametrize('identifier, loop_type, params, commands', [
        (1, 'loop', {'setpoint': 20.0}, ['TEMP, S20.0']),
        (1, 'loop', {'setpoint': {'constant': 20.0}}, ['TEMP, S20.0']),
        (1, 'loop', {'setPoint': 20.0}, ['TEMP, S20.0']),
        (2, 'loop', {'setpoint': 60.0}, ['HUMI, S60.0']),
        (1, 'loop', {'range': {'min': -20.0, 'max': 80.0}}, ['TEMP, L-20.0', 'TEMP, H80.0']),
        (1, 'loop', {'enable': True}, []),
        (2, 'loop', {'enable': False}, ['HUMI,SOFF']),
        (2, 'loop', {'mode': 'On'}, ['HUMI, S55.0']),
        (1, 'loop', {'setpoint': 20.0, 'enable': True}, ['TEMP, S20.0']),
        (1, 'loop', {'setPoint': {'constant': 20.0}, 'enable': {'constant': True}}, ['TEMP, S20.0']),
        (1, 'loop', {'setValue': 20.0, 'mode': 'On'}, ['TEMP, S20.0']),
        (2, 'loop', {'setpoint': 60.0, 'enable': True}, ['HUMI, S60.0']),
        (2, 'loop', {'setpoint': 60.0, 'enable': {'constant': False}}, ['HUMI,SOFF']),
        (2, 'loop', {'setpoint': 60.0, 'mode': 'On'}, ['HUMI, S60.0']),
        (2, 'loop', {'setpoint': 60.0, 'mode': 'Off'}, ['HUMI,SOFF']),
        (2, 'loop', {'setpoint': 60.0, 'mode': {'constant': 'OFF'}}, ['HUMI,SOFF']),
        (1, 'cascade', {'setpoint': 20.0}, ['TEMP, S20.0']),
        (1, 'cascade', {'setValue': 20.0}, ['TEMP, S20.0']),
        (1, 'cascade', {'setpoint': 20.0, 'enable': True}, ['TEMP, S20.0']),
        (1, 'cascade', {'range': {'min': -20.0, 'max': 80.0}}, ['TEMP, L-20.0', 'TEMP, H80.0']),
        (1, 'cascade', {'enable': True}, []),
        (1, 'cascade', {'mode': 'On'}, []),
        (1, 'cascade', {'enable_cascade': False}, ['TEMP PTC, PTCOFF, DEVP10.0, DEVN-10.0']),
        (1, 'cascade', {'deviation': {'positive': 5.0, 'negative': -5.0}}, ['TEMP PTC, PTCON, DEVP5.0, DEVN-5.0']),
        (1, 'cascade', {'deviation': {'positive': 5.0, 'negative': -5.0}, 'enable_cascade': {'constant': False}},
         ['TEMP PTC, PTCOFF, DEVP5.0, DEVN-5.0']),
        (1, 'loop', {'power': 50.0}, []),
        (1, 'loop', {'deviation': {'positive': 5.0, 'negative': -5.0}}, []),
        (1, 'loop', {'no such parameter': 1}, []),
    ])
    def test_commands(self, ctlr, chamber, identifier, loop_type, params, commands):
        ctlr.set_loop(identifier, loop_type, params)
        assert written(chamber) == commands

    def test_parameters_as_keywords(self, ctlr, chamber):
        ctlr.set_loop(1, 'loop', setpoint=20.0)
        assert written(chamber) == ['TEMP, S20.0']

    def test_loop_type_defaults_to_loop(self, ctlr, chamber):
        ctlr.set_loop(2, setpoint=60.0)
        assert written(chamber) == ['HUMI, S60.0']

    def test_loop_by_name(self, ctlr, chamber):
        ctlr.set_loop('temperature', setpoint=20.0)
        assert written(chamber) == ['TEMP, S20.0']

    def test_name_decides_the_loop_type(self, chamber):
        ctlr = connected(loops=0, cascades=1)
        ctlr.set_loop('temperature', 'loop', {'enable_cascade': False})
        assert written(chamber) == ['TEMP PTC, PTCOFF, DEVP10.0, DEVN-10.0']

    @pytest.mark.parametrize('identifier', [1.0, None, [1]])
    def test_identifier_has_to_be_number_or_name(self, ctlr, identifier):
        with pytest.raises(ValueError):
            ctlr.set_loop(identifier, 'loop', {'setpoint': 20.0})

    def test_invalid_mode_is_not_ignored(self, ctlr):
        with pytest.raises(ValueError):
            ctlr.set_loop(2, 'loop', {'mode': 'Auto'})

    @pytest.mark.xfail(strict=True, reason='"if range in param_list" tests the builtin range instead of the key '
                                           '"range": setpoint and range are not written with one command')
    def test_setpoint_enable_and_range_are_written_together(self, ctlr, chamber):
        ctlr.set_loop(1, 'loop', {'setpoint': 20.0, 'enable': True, 'range': {'min': -20.0, 'max': 80.0}})
        assert written(chamber) == ['TEMP, S20.0 H80.0 L-20.0']

    @pytest.mark.xfail(strict=True, reason='a plain bool for enable_cascade is put into a set instead of a dict, '
                                           'write_temp_ptc(**params) then raises TypeError')
    def test_deviation_with_plain_enable_cascade(self, ctlr, chamber):
        ctlr.set_loop(1, 'cascade', {'deviation': {'positive': 5.0, 'negative': -5.0}, 'enable_cascade': False})
        assert written(chamber) == ['TEMP PTC, PTCOFF, DEVP5.0, DEVN-5.0']

    @pytest.mark.xfail(strict=True, reason='the KeyError of self.lpd[N] for an unknown loop is swallowed by '
                                           '"except KeyError: pass", nothing is written and nothing is raised')
    def test_unknown_loop_raises_valueerror(self, ctlr):
        with pytest.raises(ValueError):
            ctlr.set_loop(3, 'loop', {'setpoint': 20.0})


class TestCascade(object):
    @pytest.mark.parametrize('method, expected', [
        ('get_cascade_sp', {'constant': 20.0, 'current': 25.0, 'air': 30.0, 'product': 25.0}),
        ('get_cascade_pv', {'air': 23.5, 'product': 22.0}),
        ('get_cascade_range', {'max': 100.0, 'min': -40.0}),
        ('get_cascade_en', {'constant': True, 'current': True}),
        ('get_cascade_units', '\xb0C'),
        ('get_cascade_mode', {'constant': 'On', 'current': 'On'}),
        ('get_cascade_modes', ['On']),
        ('get_cascade_ctl', {'constant': True, 'current': True}),
        ('get_cascade_deviation', {'positive': 10.0, 'negative': -10.0}),
        ('get_cascade_power', {'constant': 60.0, 'current': 60.0}),
    ])
    def test_cascade_value(self, ctlr, method, expected):
        assert getattr(ctlr, method)(1) == expected

    def test_values_with_product_temperature_control_off(self, ctlr, chamber):
        chamber.answers.update({
            'TEMP PTC?': 'OFF,22.0,23.5,30.0,25.0,10.0,-10.0', 'CONSTANT SET?,PTC': 'OFF,10.0,-10.0',
        })
        assert ctlr.get_cascade_ctl(1) == {'constant': False, 'current': False}
        assert ctlr.get_cascade_sp(1) == {'constant': 20.0, 'current': 30.0, 'air': 30.0, 'product': 25.0}

    @pytest.mark.parametrize('method, value, commands', [
        ('set_cascade_sp', 30.0, ['TEMP, S30.0']),
        ('set_cascade_sp', {'constant': 30.0}, ['TEMP, S30.0']),
        ('set_cascade_range', {'min': -20.0, 'max': 80.0}, ['TEMP, L-20.0', 'TEMP, H80.0']),
        ('set_cascade_en', True, []),
        ('set_cascade_en', {'constant': False}, []),
        ('set_cascade_mode', 'On', []),
        ('set_cascade_mode', {'constant': 'Off'}, []),
        ('set_cascade_ctl', True, ['TEMP PTC, PTCON, DEVP10.0, DEVN-10.0']),
        ('set_cascade_ctl', False, ['TEMP PTC, PTCOFF, DEVP10.0, DEVN-10.0']),
        ('set_cascade_ctl', {'constant': False}, ['TEMP PTC, PTCOFF, DEVP10.0, DEVN-10.0']),
        ('set_cascade_deviation', {'positive': 5.0, 'negative': -5.0}, ['TEMP PTC, PTCON, DEVP5.0, DEVN-5.0']),
    ])
    def test_cascade_setter(self, ctlr, chamber, method, value, commands):
        getattr(ctlr, method)(1, value)
        assert written(chamber) == commands

    @pytest.mark.xfail(strict=True, reason='set_cascade_deviation() passes the dict of get_cascade_ctl() as enable, '
                                           'which is always true: writing a deviation switches the cascade on')
    def test_deviation_keeps_product_temperature_control_off(self, ctlr, chamber):
        chamber.answers.update({
            'TEMP PTC?': 'OFF,22.0,23.5,30.0,25.0,10.0,-10.0', 'CONSTANT SET?,PTC': 'OFF,10.0,-10.0',
        })
        ctlr.set_cascade_deviation(1, {'positive': 5.0, 'negative': -5.0})
        assert written(chamber) == ['TEMP PTC, PTCOFF, DEVP5.0, DEVN-5.0']

    @pytest.mark.parametrize('value', [{'positive': 5.0}, {'negative': -5.0}])
    def test_deviation_needs_positive_and_negative(self, ctlr, value):
        with pytest.raises(ValueError):
            ctlr.set_cascade_deviation(1, value)

    @pytest.mark.parametrize('method, args', [
        ('get_cascade_sp', ()),
        ('get_cascade_pv', ()),
        ('get_cascade_range', ()),
        ('get_cascade_en', ()),
        ('get_cascade_units', ()),
        ('get_cascade_mode', ()),
        ('get_cascade_ctl', ()),
        ('get_cascade_deviation', ()),
        ('get_cascade_power', ()),
        ('set_cascade_sp', (30.0,)),
        ('set_cascade_range', ({'min': -20.0, 'max': 80.0},)),
        ('set_cascade_en', (True,)),
        ('set_cascade_mode', ('On',)),
        ('set_cascade_ctl', (True,)),
        ('set_cascade_deviation', ({'positive': 5.0, 'negative': -5.0},)),
    ])
    def test_only_the_temperature_loop_can_be_a_cascade(self, ctlr, chamber, method, args):
        with pytest.raises(ValueError) as error:
            getattr(ctlr, method)(2, *args)
        assert str(error.value) == 'The P300 controller can only have loop 1 as cascade'
        assert chamber.commands == []


class TestEvents(object):
    @pytest.mark.parametrize('number, expected', [
        (1, {'current': True, 'constant': True}),
        (2, {'current': False, 'constant': False}),
        (3, {'current': True, 'constant': False}),
        (5, {'current': False, 'constant': True}),
        (12, {'current': False, 'constant': False}),
    ])
    def test_get_event(self, ctlr, number, expected):
        assert ctlr.get_event(number) == expected

    @pytest.mark.parametrize('number, value, command', [
        (3, True, 'RELAY,ON,3'),
        (3, False, 'RELAY,OFF,3'),
        (12, {'constant': True}, 'RELAY,ON,12'),
    ])
    def test_set_event(self, ctlr, chamber, number, value, command):
        ctlr.set_event(number, value)
        assert chamber.commands == [command]

    def test_there_are_12_events(self, ctlr, chamber):
        with pytest.raises(ValueError):
            ctlr.get_event(13)
        with pytest.raises(ValueError):
            ctlr.set_event(13, True)
        assert chamber.commands == []


class TestStatus(object):
    @pytest.mark.parametrize('mode, status', [
        ('OFF', 'Off'),
        ('STANDBY', 'Standby'),
        ('CONSTANT', 'Constant'),
        ('RUN', 'Program Running'),
        ('RUN PAUSE', 'Program Paused'),
        ('RUN END HOLD', 'Program End Hold'),
        ('RMT RUN', 'Remote Program Running'),
        ('RMT RUN PAUSE', 'Remote Program Paused'),
        ('RMT RUN END HOLD', 'Remote Program End Hold'),
    ])
    def test_status_of_the_p300(self, ctlr, chamber, mode, status):
        chamber.answers['MODE?,DETAIL'] = mode
        assert ctlr.get_status() == status

    def test_status_of_the_scp220_without_detail(self, chamber):
        del chamber.answers['MODE?,DETAIL']
        chamber.answers['MODE?'] = 'RUN'
        assert connected(ctlr_type='SCP220').get_status() == 'Program Running'
        assert chamber.commands == ['MON?', 'MODE?']

    def test_status_with_active_alarm(self, ctlr, chamber):
        chamber.answers['MON?'] = '23.5,45,CONSTANT,2'
        assert ctlr.get_status() == 'Alarm'

    def test_no_alarm(self, ctlr):
        assert ctlr.get_alarm_status() == {'active': [], 'inactive': ALARM_CODES}

    def test_active_alarms(self, ctlr, chamber):
        chamber.answers['ALARM?'] = '2,12,40'
        status = ctlr.get_alarm_status()
        assert status['active'] == [12, 40]
        assert status['inactive'] == [code for code in ALARM_CODES if code not in (12, 40)]


class TestOperation(object):
    def test_get_operation_in_constant_mode(self, ctlr):
        operation = ctlr.get_operation()
        assert operation['status'] == 'Constant'
        assert operation['mode'] == 'constant'
        assert operation['program'] is None

    @pytest.mark.parametrize('detail, mode', [('STANDBY', 'standby'), ('OFF', 'off')])
    def test_get_operation_when_stopped(self, ctlr, chamber, detail, mode):
        chamber.answers['MODE?,DETAIL'] = detail
        assert ctlr.get_operation()['mode'] == mode

    def test_get_operation_with_running_program(self, ctlr, chamber):
        chamber.answers['MODE?,DETAIL'] = 'RUN'
        operation = ctlr.get_operation()
        assert operation['status'] == 'Program Running'
        assert operation['mode'] == 'program'
        assert operation['program'] == {
            'number': 1,
            'step': 1,
            'time_remaining': '6:00:00',
            'step_time_remaining': '0:30:00',
            'name': 'SOAK',
            'steps': 3,
            'cycles': [
                {'name': 'A', 'remaining': 1, 'start': 1, 'end': 2, 'cycles': 1},
                {'name': 'B', 'remaining': 0, 'start': 0, 'end': 0, 'cycles': 0},
            ],
        }

    def test_get_operation_with_alarm(self, ctlr, chamber):
        chamber.answers.update({'MON?': '23.5,45,STANDBY,2', 'ALARM?': '2,12,40'})
        operation = ctlr.get_operation()
        assert operation['status'] == 'Alarm'
        assert operation['mode'] == 'alarm'
        assert 'ALARM?' in chamber.commands

    @pytest.mark.parametrize('mode, kwargs, command', [
        ('constant', {}, 'MODE,CONSTANT'),
        ('standby', {}, 'MODE,STANDBY'),
        ('off', {}, 'MODE,STANDBY'),
        ('program', {'program': 2}, 'PRGM,RUN,RAM:2,STEP1'),
        ('program', {'program': 2, 'step': 3}, 'PRGM,RUN,RAM:2,STEP3'),
        ('program', {'program': {'number': 41, 'step': 2}}, 'PRGM,RUN,ROM:41,STEP2'),
        ('program_pause', {}, 'PRGM,PAUSE'),
        ('program_resume', {}, 'PRGM,CONTINUE'),
        ('program_advance', {}, 'PRGM,ADVANCE'),
    ])
    def test_set_operation(self, ctlr, chamber, mode, kwargs, command):
        ctlr.set_operation(mode, **kwargs)
        assert chamber.commands == [command]

    @pytest.mark.parametrize('method, args, command', [
        ('const_start', (), 'MODE,CONSTANT'),
        ('stop', (), 'MODE,STANDBY'),
        ('prgm_start', (2, 3), 'PRGM,RUN,RAM:2,STEP3'),
        ('prgm_pause', (), 'PRGM,PAUSE'),
        ('prgm_resume', (), 'PRGM,CONTINUE'),
        ('prgm_next_step', (), 'PRGM,ADVANCE'),
        ('prgm_delete', (3,), 'PRGM ERASE,RAM:3'),
    ])
    def test_operation_command(self, ctlr, chamber, method, args, command):
        getattr(ctlr, method)(*args)
        assert chamber.commands == [command]

    def test_refused_command_raises_especerror(self, ctlr, chamber):
        chamber.answers['MODE,CONSTANT'] = not_supported('MODE,CONSTANT')
        with pytest.raises(EspecError):
            ctlr.set_operation('constant')


class TestProductionCalls(object):
    '''The calls of a typical application, on a temperature only chamber.'''

    @pytest.fixture
    def link(self, espec_link):
        espec_link.answers.update({
            'MON?': '23.5,,CONSTANT,0',
            'MODE?': 'CONSTANT',
            'MODE?,DETAIL': 'CONSTANT',
            'TEMP?': '23.5,25.0,100.0,-40.0',
            'CONSTANT SET?,TEMP': '20.0,ON',
            '%?': '1,60.0',
            'MODE,CONSTANT': 'OK:MODE,CONSTANT',
            'MODE,STANDBY': 'OK:MODE,STANDBY',
            'TEMP, S20.0': 'OK:TEMP, S20.0',
        })
        return espec_link

    @pytest.fixture
    def ctlr(self, link):
        ctlr = Espec(interface='TCP', host='192.168.0.10', port=57732, timeout=3)
        ctlr.connect()
        return ctlr

    def test_connect_and_close(self, ctlr, link):
        assert link.created_with == [{'host': '192.168.0.10', 'address': 1, 'port': 57732, 'timeout': 3}]
        ctlr.close()
        assert link.closed

    def test_get_operation(self, ctlr, link):
        operation = ctlr.get_operation()
        assert (operation['status'], operation['mode'], operation['program']) == ('Constant', 'constant', None)
        assert link.commands == ['MON?', 'MODE?,DETAIL']

    @pytest.mark.parametrize('mode, command', [
        ('constant', 'MODE,CONSTANT'), ('off', 'MODE,STANDBY'), ('standby', 'MODE,STANDBY'),
    ])
    def test_set_operation(self, ctlr, link, mode, command):
        ctlr.set_operation(mode)
        assert link.commands == [command]

    def test_get_loop_temperature(self, ctlr, link):
        assert ctlr.get_loop('temperature') == {
            'setpoint': {'constant': 20.0, 'current': 25.0},
            'processvalue': {'air': 23.5},
            'range': {'max': 100.0, 'min': -40.0},
            'enable': {'constant': True, 'current': True},
            'units': '\xb0C',
            'mode': {'constant': 'On', 'current': 'On'},
            'power': {'constant': 60.0, 'current': 60.0},
        }

    def test_set_loop_setpoint(self, ctlr, link):
        ctlr.set_loop(1, 'loop', {'setpoint': 20.0})
        assert link.commands == ['TEMP, S20.0']


class TestPrograms(object):
    def test_values_of_the_running_program(self, ctlr):
        assert ctlr.get_prgm_cur() == 1
        assert ctlr.get_prgm_cstep() == 1
        assert ctlr.get_prgm_cstime() == '0:30:00'
        assert ctlr.get_prgm_name(1) == 'SOAK'
        assert ctlr.get_prgm_steps(1) == 3

    def test_step_time_with_more_than_a_day(self, ctlr, chamber):
        chamber.answers['PRGM MON?'] = '1,25.0,50,123:05,1,0'
        assert ctlr.get_prgm_cstime() == '123:05:00'

    def test_get_prgm_counter(self, ctlr):
        assert ctlr.get_prgm_counter() == [
            {'name': 'A', 'remaining': 1, 'start': 1, 'end': 2, 'cycles': 1},
            {'name': 'B', 'remaining': 0, 'start': 0, 'end': 0, 'cycles': 0},
        ]

    def test_get_prgm_time_reads_the_running_program(self, ctlr, chamber):
        assert ctlr.get_prgm_time() == '6:00:00'
        assert 'PRGM DATA?,RAM:1,STEP3' in chamber.commands

    def test_get_prgm_time_with_given_program(self, ctlr, chamber):
        assert ctlr.get_prgm_time(program(counter_a=(1, 2, 1))) == '6:00:00'
        assert chamber.commands == ['PRGM MON?']

    # the steps take 1:00, 2:00 and 0:30
    @pytest.mark.parametrize('counter_a, counter_b, monitor, expected', [
        ((0, 0, 0), (0, 0, 0), '1,25.0,50,0:30,0,0', '3:00:00'),
        ((0, 0, 0), (0, 0, 0), '2,25.0,50,2:00,0,0', '2:30:00'),
        ((0, 0, 0), (0, 0, 0), '3,25.0,50,0:10,0,0', '0:10:00'),
        ((1, 2, 1), (0, 0, 0), '1,25.0,50,0:30,1,0', '6:00:00'),
        ((1, 2, 1), (0, 0, 0), '1,25.0,50,0:30,0,0', '3:00:00'),
        ((1, 2, 3), (0, 0, 0), '2,25.0,50,2:00,3,0', '11:30:00'),
        ((0, 0, 0), (1, 2, 1), '1,25.0,50,0:30,0,1', '6:00:00'),
        ((2, 2, 1), (1, 3, 1), '1,25.0,50,1:00,1,1', '11:00:00'),
        ((1, 3, 1), (2, 2, 1), '1,25.0,50,1:00,1,1', '11:00:00'),
        ((2, 2, 1), (1, 3, 1), '3,25.0,50,0:30,0,0', '0:30:00'),
        ((1, 2, 1), (1, 3, 1), '1,25.0,50,1:00,1,1', '13:00:00'),
    ])
    def test_get_prgm_time(self, ctlr, chamber, counter_a, counter_b, monitor, expected):
        chamber.answers['PRGM MON?'] = monitor
        assert ctlr.get_prgm_time(program(counter_a, counter_b)) == expected

    def test_get_prgm_time_without_humidity(self, ctlr, chamber):
        chamber.answers['PRGM MON?'] = '2,25.0,2:00,0,0'
        assert ctlr.get_prgm_time(program()) == '2:30:00'

    def test_get_prgm_time_gives_up_on_inconsistent_counters(self, ctlr, chamber):
        chamber.answers['PRGM MON?'] = '1,25.0,50,0:30,50,0'
        with pytest.raises(RuntimeError):
            ctlr.get_prgm_time(program(counter_a=(1, 2, 1)))

    @pytest.mark.xfail(strict=True, reason='get_prgm_time() swaps counter_a and counter_b in the program of the '
                                           'caller, the next call with this cached program computes a different time')
    def test_get_prgm_time_twice_with_the_same_cached_program(self, ctlr, chamber):
        chamber.answers['PRGM MON?'] = '1,25.0,50,0:30,0,1'
        cached_program = program(counter_b=(1, 2, 1))
        assert ctlr.get_prgm_time(cached_program) == '6:00:00'
        assert ctlr.get_prgm_time(cached_program) == '6:00:00'

    @pytest.mark.xfail(strict=True, reason='get_prgm_time() swaps the counters inside the cached answer of '
                                           'read_prgm_mon, with freshness > 0 the next call swaps them back')
    def test_get_prgm_time_twice_with_cached_answers(self, chamber):
        chamber.answers.update({
            'PRGM MON?': '1,25.0,50,0:30,0,1',
            'PRGM DATA?,RAM:1': '3,<SOAK>,COUNT,A(0.0.0),B(1.2.1),END(STANDBY)',
        })
        ctlr = connected(freshness=60)
        assert ctlr.get_prgm_time() == '6:00:00'
        assert ctlr.get_prgm_time() == '6:00:00'

    def test_get_prgms_of_the_p300(self, ctlr, chamber):
        del chamber.answers['PRGM USE?,RAM:2']
        chamber.default = None
        programs = ctlr.get_prgms()
        assert len(programs) == 40
        assert programs[:3] == [{'number': 1, 'name': 'SOAK'}, {'number': 2, 'name': ''}, {'number': 3, 'name': ''}]
        assert chamber.commands[-1] == 'PRGM USE?,RAM:40'

    def test_get_prgms_of_the_scp220(self, chamber):
        chamber.default = None
        programs = connected(ctlr_type='SCP220').get_prgms()
        assert [prgm for prgm in programs if prgm['name']] == [
            {'number': 1, 'name': 'SOAK'}, {'number': 2, 'name': 'CYCLE'},
        ]
        assert len(programs) == 30
        assert chamber.commands[19:21] == ['PRGM USE?,RAM:20', 'PRGM USE?,ROM:21']

    def test_get_prgm(self, ctlr, chamber):
        prgm = ctlr.get_prgm(1)
        assert prgm['name'] == 'SOAK'
        assert prgm['counter_a'] == {'start': 1, 'end': 2, 'cycles': 1}
        assert [step['time'] for step in prgm['steps']] == [
            {'hour': 1, 'minute': 0}, {'hour': 2, 'minute': 0}, {'hour': 0, 'minute': 30},
        ]
        assert 'deviation' not in prgm['steps'][0]['temperature']
        assert chamber.commands[0] == 'PRGM DATA?,RAM:1'

    def test_get_prgm_with_cascade(self, chamber):
        prgm = connected(loops=0, cascades=1).get_prgm(1)
        assert prgm['steps'][0]['temperature']['deviation'] == {'positive': 10.0, 'negative': -10.0}
        assert prgm['steps'][0]['temperature']['enable_cascade'] is True
        assert chamber.commands[0] == 'PRGM DATA PTC?,RAM:1'

    def test_get_prgm_that_does_not_exist(self, ctlr, chamber):
        chamber.default = None
        with pytest.raises(ControllerInterfaceError):
            ctlr.get_prgm(7)

    def test_set_prgm_writes_what_get_prgm_has_read(self, ctlr, chamber):
        prgm = ctlr.get_prgm(1)
        del chamber.commands[:]
        ctlr.set_prgm(2, prgm)
        assert chamber.commands[0] == 'PRGM DATA WRITE, PGM2, EDIT START'
        assert chamber.commands[1].startswith('PRGM DATA WRITE, PGM2, STEP1,TIME1:0,')
        assert 'PRGM DATA WRITE,PGM2,COUNT,A(1.2.1)' in chamber.commands
        assert 'PRGM DATA WRITE, PGM2, NAME,SOAK' in chamber.commands
        assert chamber.commands[-1] == 'PRGM DATA WRITE, PGM2, EDIT END'

    def test_program_functions_of_the_base_class(self, ctlr, chamber):
        assert ctlr.get_program(1) == ctlr.get_prgm(1)
        assert ctlr.get_program_details(1) == {'number': 1, 'name': 'SOAK', 'steps': 3}
        ctlr.set_program(3, None)
        assert chamber.commands[-1] == 'PRGM ERASE,RAM:3'
        chamber.default = None
        assert ctlr.get_program_list()[:3] == [
            {'number': 1, 'name': 'SOAK'}, {'number': 2, 'name': 'CYCLE'}, {'number': 3, 'name': ''},
        ]


class TestSample(object):
    def test_temperature_chamber(self, chamber):
        assert connected().sample() == {
            'datetime': datetime.datetime(2026, 11, 13, 12, 34, 56),
            'status': 'Constant',
            'loops': [{
                'setpoint': {'constant': 20.0, 'current': 25.0},
                'processvalue': {'air': 23.5},
                'enable': {'constant': True, 'current': True},
                'mode': {'constant': 'On', 'current': 'On'},
                'power': {'constant': 60.0, 'current': 60.0},
            }],
        }

    def test_humidity_chamber(self, chamber):
        loops = connected(loops=2).sample()['loops']
        assert len(loops) == 2
        assert loops[0]['processvalue'] == {'air': 23.5}
        assert loops[1] == {
            'setpoint': {'constant': 55.0, 'current': 50.0},
            'processvalue': {'air': 45.0},
            'enable': {'constant': True, 'current': True},
        }

    def test_chamber_with_cascade(self, chamber):
        loops = connected(loops=0, cascades=1).sample()['loops']
        assert len(loops) == 1
        assert loops[0]['processvalue'] == {'air': 23.5, 'product': 22.0}
        assert loops[0]['setpoint'] == {'constant': 20.0, 'current': 25.0, 'air': 30.0, 'product': 25.0}
        assert loops[0]['enable_cascade'] == {'constant': True, 'current': True}

    def test_humidity_chamber_with_cascade(self, chamber):
        loops = connected(loops=1, cascades=1).sample()['loops']
        assert loops[0]['processvalue'] == {'air': 23.5, 'product': 22.0}
        assert loops[1]['processvalue'] == {'air': 45.0}

    def test_lookup_is_added_to_the_loops(self, chamber):
        lookup = {'loop': [{'name': 'Temperature'}, {'name': 'Humidity'}]}
        loops = connected(loops=2).sample(lookup)['loops']
        assert [loop['name'] for loop in loops] == ['Temperature', 'Humidity']
        assert loops[1]['processvalue'] == {'air': 45.0}

    def test_lookup_with_cascade(self, chamber):
        lookup = {'cascade': [{'name': 'Temperature'}], 'loop': [{'name': 'Humidity'}]}
        loops = connected(loops=1, cascades=1).sample(lookup)['loops']
        assert [loop['name'] for loop in loops] == ['Temperature', 'Humidity']


class TestProcessController(object):
    @pytest.mark.parametrize('rom, missing, part_number, loops, cascades', [
        ('P3001.23', ['TEMP PTC?', 'HUMI?'], 'P300 ', 1, 0),
        ('P3001.23', ['TEMP PTC?'], 'P300 W/Humidity', 2, 0),
        ('P3001.23', ['HUMI?'], 'P300 W/PTCON ', 0, 1),
        ('P3001.23', [], 'P300 W/PTCON W/Humidity', 1, 1),
        ('SCP220 1.0', ['TEMP PTC?', 'HUMI?'], 'SCP-220 ', 1, 0),
    ])
    def test_detects_the_configuration(self, chamber, rom, missing, part_number, loops, cascades):
        chamber.answers['ROM?'] = rom
        for command in missing:
            chamber.answers[command] = not_supported(command)
        ctlr = connected(loops=5, cascades=5)
        assert ctlr.process_controller() == part_number
        assert (ctlr.loops, ctlr.cascades) == (loops, cascades)
        assert len(ctlr.loop_map) == loops + cascades

    def test_loop_map_of_a_cascade_chamber(self, chamber):
        chamber.answers['HUMI?'] = not_supported('HUMI?')
        ctlr = connected()
        ctlr.process_controller()
        assert ctlr.loop_map == [{'type': 'cascade', 'num': 1}]
        assert ctlr.get_loop('temperature', 'processvalue') == {'processvalue': {'air': 23.5, 'product': 22.0}}

    def test_without_update_the_configuration_is_kept(self, chamber):
        ctlr = connected(loops=2, cascades=0)
        ctlr.process_controller(update=False)
        assert (ctlr.loops, ctlr.cascades) == (2, 0)
        assert chamber.commands == ['ROM?']

    @pytest.mark.xfail(strict=True, reason='with update=False the controller is not asked for PTCON and humidity, '
                                           'the returned part number always claims both')
    def test_part_number_without_update(self, chamber):
        chamber.answers.update({'TEMP PTC?': not_supported('TEMP PTC?'), 'HUMI?': not_supported('HUMI?')})
        assert connected().process_controller(update=False) == 'P300 '


class TestNetworkSettings(object):
    def test_get_network_settings(self, ctlr):
        assert ctlr.get_network_settings() == {
            'address': '192.168.0.10', 'mask': '255.255.255.0', 'gateway': '192.168.0.1', 'message': '', 'host': '',
        }

    @pytest.mark.parametrize('value, command', [
        ({'address': '10.0.0.5', 'mask': '255.0.0.0', 'gateway': '10.0.0.1'}, 'IPSET,10.0.0.5,255.0.0.0,10.0.0.1'),
        ({'address': '10.0.0.5'}, 'IPSET,10.0.0.5,0.0.0.0,0.0.0.0'),
        ({}, 'IPSET,0.0.0.0,0.0.0.0,0.0.0.0'),
        (None, 'IPSET,0.0.0.0,0.0.0.0,0.0.0.0'),
    ])
    def test_set_network_settings(self, ctlr, chamber, value, command):
        ctlr.set_network_settings(value)
        assert chamber.commands == [command]

    def test_scp220_has_no_network_settings(self, chamber):
        with pytest.raises(NotImplementedError):
            connected(ctlr_type='SCP220').get_network_settings()


class TestSelfTest(object):
    @pytest.fixture(autouse=True)
    def sleeps(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(controllerinterface, 'time', types.SimpleNamespace(sleep=sleeps.append))
        return sleeps

    def test_self_test_of_a_complete_chamber(self, chamber, capsys, sleeps):
        for number in range(3, 41):
            command = 'PRGM USE?,RAM:%d' % number
            chamber.answers[command] = not_supported(command)
        for number in range(3, 6):
            chamber.answers['PRGM DATA?,RAM:%d' % number] = CHAMBER['PRGM DATA?,RAM:2']
        ctlr = Espec(interface='TCP', host='chamber', port=57732, timeout=3)
        ctlr.self_test(2, 1)
        out = capsys.readouterr().out
        assert "process_controller():\n\t'P300 W/PTCON W/Humidity'\n" in out
        assert 'get_datetime:\n\tdatetime.datetime(2026, 11, 13, 12, 34, 56)\n' in out
        assert "get_loop_sp(2):\n\t{'constant': 55.0, 'current': 50.0}\n" in out
        assert 'set_cascade_deviation(1):\n\tok\n' in out
        assert "get_status:\n\t'Constant'\n" in out
        assert 'Traceback' not in out
        assert out.endswith('Testing Complete\n')
        assert sleeps == [5, 5, 5, 5, 5]
        assert 'TEMP, S20.0' in chamber.commands
        assert 'MODE,CONSTANT' in chamber.commands
        assert ctlr.client is None

    def test_self_test_reports_errors_and_goes_on(self, chamber, capsys):
        chamber.answers['TEMP?'] = EspecError('The chamber did not respond in time')
        Espec(interface='TCP', host='chamber', port=57732, timeout=3).self_test(1, 0)
        out = capsys.readouterr().out
        assert 'get_loop_pv(1):\n\tTraceback (most recent call last):\n' in out
        assert 'EspecError: The chamber did not respond in time\n' in out
        assert "get_loop_units(1):\n\t'\xb0C'\n" in out
        assert out.endswith('Testing Complete\n')
