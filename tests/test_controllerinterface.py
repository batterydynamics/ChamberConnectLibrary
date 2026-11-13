'''
Tests for the generic part of the upper level interface: the functions every controller
class inherits from ControllerInterface, and the exclusive decorator.

FakeController implements every abstract function by passing the call on to a MagicMock
(controller.backend), so the tests see what the generic code asks of a controller class.
'''
import datetime
import types
from unittest import mock

import pytest

from chamberconnectlibrary import controllerinterface
from chamberconnectlibrary.controllerinterface import ControllerInterface, exclusive

LOOP_GETTERS = {
    'setpoint': 'get_loop_sp',
    'processvalue': 'get_loop_pv',
    'range': 'get_loop_range',
    'enable': 'get_loop_en',
    'units': 'get_loop_units',
    'mode': 'get_loop_mode',
    'power': 'get_loop_power',
}
CASCADE_GETTERS = {
    'setpoint': 'get_cascade_sp',
    'processvalue': 'get_cascade_pv',
    'range': 'get_cascade_range',
    'enable': 'get_cascade_en',
    'units': 'get_cascade_units',
    'mode': 'get_cascade_mode',
    'power': 'get_cascade_power',
    'deviation': 'get_cascade_deviation',
    'enable_cascade': 'get_cascade_ctl',
}


def passed_on(name):
    @exclusive
    def function(self, *args, **kwargs):
        return getattr(self.backend, name)(*args, **kwargs)
    return function


Backend = type('Backend', (object,), dict((name, passed_on(name)) for name in ControllerInterface.__abstractmethods__))


class FakeController(Backend, ControllerInterface):
    '''A controller with one cascade (product) and two loops (temperature, humidity).'''

    def __init__(self, **kwargs):
        self.init_common(**kwargs)
        self.backend = mock.MagicMock()
        self.connections = []
        self.loop_map = [{'type': 'cascade', 'num': 1}, {'type': 'loop', 'num': 1}, {'type': 'loop', 'num': 2}]
        self.named_loop_map = {'product': 0, 'temperature': 1, 'humidity': 2}

    def connect(self):
        self.connections.append('connect')
        self.client = 'connected'

    def close(self):
        self.connections.append('close')
        self.client = None


class BrokenBackend(object):
    '''Every function of the controller fails.'''

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise RuntimeError('%s failed' % name)
        return fail


def answers(ctlr, getters):
    '''What get_loop() has to return when it asks the backend with these functions.'''
    return dict((key, getattr(ctlr.backend, name).return_value) for key, name in getters.items())


@pytest.fixture
def ctlr():
    '''A controller with an open connection.'''
    controller = FakeController()
    controller.connect()
    del controller.connections[:]
    return controller


@pytest.fixture
def backend(ctlr):
    return ctlr.backend


class TestAbstractClass(object):
    def test_interface_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            ControllerInterface()

    def test_incomplete_controller_cannot_be_instantiated(self):
        class Incomplete(ControllerInterface):
            def get_status(self):
                return 'Standby'
        with pytest.raises(TypeError):
            Incomplete()

    def test_operation_modes(self, ctlr):
        assert ctlr.get_operation_modes() == ['standby', 'constant', 'program']


class TestInitCommon(object):
    def test_defaults(self):
        ctlr = FakeController()
        assert ctlr.client is None
        assert (ctlr.interface, ctlr.host, ctlr.port, ctlr.serialport, ctlr.baudrate) == (None, None, None, None, None)
        assert (ctlr.timeout, ctlr.adr, ctlr.loops, ctlr.cascades) == (3, 1, 1, 0)
        with ctlr.lock:
            with ctlr.lock:
                pass

    def test_given_values(self):
        lock = mock.MagicMock()
        ctlr = FakeController(interface='TCP', host='chamber', port=57732, timeout=7, adr=5, loops=2, cascades=1,
                              serialport='/dev/ttyUSB0', baudrate=19200, lock=lock)
        assert (ctlr.interface, ctlr.host, ctlr.port, ctlr.timeout) == ('TCP', 'chamber', 57732, 7)
        assert (ctlr.serialport, ctlr.baudrate, ctlr.adr) == ('/dev/ttyUSB0', 19200, 5)
        assert (ctlr.loops, ctlr.cascades) == (2, 1)
        assert ctlr.lock is lock

    def test_every_controller_gets_its_own_lock(self):
        assert FakeController().lock is not FakeController().lock


class TestExclusive(object):
    def test_connects_before_and_closes_after_the_call(self):
        ctlr = FakeController()
        ctlr.backend.get_status.return_value = 'Standby'
        assert ctlr.get_status() == 'Standby'
        assert ctlr.connections == ['connect', 'close']
        assert ctlr.client is None

    def test_open_connection_is_kept(self, ctlr, backend):
        backend.get_status.return_value = 'Standby'
        assert ctlr.get_status() == 'Standby'
        assert ctlr.connections == []
        assert ctlr.client == 'connected'

    def test_closes_after_an_error(self):
        ctlr = FakeController()
        ctlr.backend.get_status.side_effect = RuntimeError('no answer')
        with pytest.raises(RuntimeError):
            ctlr.get_status()
        assert ctlr.connections == ['connect', 'close']

    def test_error_of_close_is_ignored(self):
        ctlr = FakeController()
        ctlr.backend.get_status.return_value = 'Standby'
        ctlr.close = mock.Mock(side_effect=RuntimeError('already closed'))
        assert ctlr.get_status() == 'Standby'
        ctlr.close.assert_called_once_with()

    def test_error_of_connect_is_raised(self):
        ctlr = FakeController()
        ctlr.connect = mock.Mock(side_effect=OSError('no route to host'))
        with pytest.raises(OSError):
            ctlr.get_status()
        assert ctlr.backend.mock_calls == []
        assert ctlr.connections == ['close']

    def test_call_holds_the_lock(self):
        lock = mock.MagicMock()
        ctlr = FakeController(lock=lock)
        ctlr.backend.get_status.side_effect = lambda: lock.__exit__.assert_not_called()
        ctlr.get_status()
        lock.__enter__.assert_called_once_with()
        assert lock.__exit__.call_count == 1

    def test_nested_call_neither_locks_nor_connects(self):
        lock = mock.MagicMock()
        ctlr = FakeController(lock=lock)
        ctlr.backend.get_status.return_value = 'Standby'
        assert ctlr.get_status(exclusive=False) == 'Standby'
        assert lock.mock_calls == []
        assert ctlr.connections == []

    def test_arguments_are_passed_on_without_exclusive(self, ctlr, backend):
        ctlr.set_event(3, value=True, exclusive=False)
        ctlr.set_event(4, value=False, exclusive=True)
        ctlr.set_event(5, True)
        assert backend.mock_calls == [
            mock.call.set_event(3, value=True), mock.call.set_event(4, value=False), mock.call.set_event(5, True),
        ]

    def test_generic_function_uses_one_connection_for_all_its_calls(self):
        ctlr = FakeController()
        ctlr.get_loop(1, 'loop')
        assert ctlr.connections == ['connect', 'close']
        assert len(ctlr.backend.mock_calls) == len(LOOP_GETTERS)


class TestGetLoop(object):
    def test_all_parameters_of_a_loop(self, ctlr, backend):
        assert ctlr.get_loop(2, 'loop') == answers(ctlr, LOOP_GETTERS)
        for name in LOOP_GETTERS.values():
            getattr(backend, name).assert_called_once_with(2)

    def test_all_parameters_of_a_cascade(self, ctlr, backend):
        assert ctlr.get_loop(1, 'cascade') == answers(ctlr, CASCADE_GETTERS)
        for name in CASCADE_GETTERS.values():
            getattr(backend, name).assert_called_once_with(1)

    @pytest.mark.parametrize('arguments', [
        (1, 'loop', 'setpoint', 'range'),
        (1, 'loop', ['setpoint', 'range']),
        (1, 'loop', ('setpoint', 'range')),
        ('temperature', 'setpoint', 'range'),
        ('temperature', ['setpoint', 'range']),
        ('temperature', ('setpoint', 'range')),
    ])
    def test_ways_to_ask_for_some_parameters(self, ctlr, backend, arguments):
        assert ctlr.get_loop(*arguments) == {
            'setpoint': backend.get_loop_sp.return_value, 'range': backend.get_loop_range.return_value,
        }
        assert backend.mock_calls == [mock.call.get_loop_sp(1), mock.call.get_loop_range(1)]

    @pytest.mark.parametrize('name, getter, number', [
        ('product', 'get_cascade_pv', 1),
        ('temperature', 'get_loop_pv', 1),
        ('humidity', 'get_loop_pv', 2),
    ])
    def test_name_decides_loop_type_and_number(self, ctlr, backend, name, getter, number):
        assert ctlr.get_loop(name, 'processvalue') == {'processvalue': getattr(backend, getter).return_value}
        assert backend.mock_calls == [getattr(mock.call, getter)(number)]

    def test_all_parameters_by_name(self, ctlr):
        assert ctlr.get_loop('product') == answers(ctlr, CASCADE_GETTERS)
        assert ctlr.get_loop('humidity') == answers(ctlr, LOOP_GETTERS)

    @pytest.mark.parametrize('loop_type, parameter, getter', [
        ('loop', 'setPoint', 'get_loop_sp'),
        ('loop', 'setValue', 'get_loop_sp'),
        ('loop', 'processValue', 'get_loop_pv'),
        ('cascade', 'setPoint', 'get_cascade_sp'),
        ('cascade', 'setValue', 'get_cascade_sp'),
        ('cascade', 'processValue', 'get_cascade_pv'),
    ])
    def test_other_spellings_of_a_parameter(self, ctlr, backend, loop_type, parameter, getter):
        assert ctlr.get_loop(1, loop_type, parameter) == {parameter: getattr(backend, getter).return_value}

    @pytest.mark.parametrize('parameter', ['no such parameter', 'deviation', 'enable_cascade'])
    def test_parameter_a_loop_does_not_have_is_none(self, ctlr, backend, parameter):
        assert ctlr.get_loop(1, 'loop', parameter, 'units') == {
            parameter: None, 'units': backend.get_loop_units.return_value,
        }

    def test_parameter_the_controller_does_not_implement_is_none(self, ctlr, backend):
        backend.get_loop_power.side_effect = NotImplementedError
        result = ctlr.get_loop(1, 'loop')
        assert result['power'] is None
        assert result['units'] == backend.get_loop_units.return_value

    def test_error_of_the_controller_is_raised(self, ctlr, backend):
        backend.get_loop_sp.side_effect = ValueError('there is no loop 9')
        with pytest.raises(ValueError):
            ctlr.get_loop(9, 'loop')

    @pytest.mark.parametrize('arguments', [(1,), (1.5, 'loop'), (None, 'loop', 'setpoint')])
    def test_invalid_arguments(self, ctlr, backend, arguments):
        with pytest.raises(ValueError):
            ctlr.get_loop(*arguments)
        assert backend.mock_calls == []

    def test_unknown_name(self, ctlr):
        with pytest.raises(KeyError):
            ctlr.get_loop('pressure')


class TestSetLoop(object):
    @pytest.mark.parametrize('loop_type, parameter, setter', [
        ('loop', 'setpoint', 'set_loop_sp'),
        ('loop', 'setPoint', 'set_loop_sp'),
        ('loop', 'setValue', 'set_loop_sp'),
        ('loop', 'range', 'set_loop_range'),
        ('loop', 'enable', 'set_loop_en'),
        ('loop', 'mode', 'set_loop_mode'),
        ('loop', 'power', 'set_loop_power'),
        ('cascade', 'setpoint', 'set_cascade_sp'),
        ('cascade', 'setPoint', 'set_cascade_sp'),
        ('cascade', 'setValue', 'set_cascade_sp'),
        ('cascade', 'range', 'set_cascade_range'),
        ('cascade', 'enable', 'set_cascade_en'),
        ('cascade', 'deviation', 'set_cascade_deviation'),
        ('cascade', 'enable_cascade', 'set_cascade_ctl'),
        ('cascade', 'mode', 'set_cascade_mode'),
        ('cascade', 'power', 'set_cascade_power'),
    ])
    def test_parameter_goes_to_its_setter(self, ctlr, backend, loop_type, parameter, setter):
        value = {'constant': 42}
        assert ctlr.set_loop(1, loop_type, {parameter: value}) is None
        assert backend.mock_calls == [getattr(mock.call, setter)(N=1, value=value)]

    def test_parameters_as_keywords(self, ctlr, backend):
        ctlr.set_loop(2, 'loop', setpoint=50.0, enable=True)
        assert backend.mock_calls == [mock.call.set_loop_sp(N=2, value=50.0), mock.call.set_loop_en(N=2, value=True)]

    def test_param_list_overrides_keywords(self, ctlr, backend):
        ctlr.set_loop(1, 'loop', {'setpoint': 50.0}, setpoint=20.0, enable=False)
        assert backend.mock_calls == [mock.call.set_loop_sp(N=1, value=50.0)]

    def test_loop_type_defaults_to_loop(self, ctlr, backend):
        ctlr.set_loop(2, setpoint=50.0)
        assert backend.mock_calls == [mock.call.set_loop_sp(N=2, value=50.0)]

    @pytest.mark.parametrize('name, setter, number', [
        ('product', 'set_cascade_sp', 1),
        ('temperature', 'set_loop_sp', 1),
        ('humidity', 'set_loop_sp', 2),
    ])
    def test_name_decides_loop_type_and_number(self, ctlr, backend, name, setter, number):
        ctlr.set_loop(name, 'no loop type', {'setpoint': 50.0})
        assert backend.mock_calls == [getattr(mock.call, setter)(N=number, value=50.0)]

    @pytest.mark.parametrize('params', [
        {'mode': 'Off', 'setpoint': 50.0, 'enable': True},
        {'setpoint': 50.0, 'enable': True, 'mode': 'Off'},
    ])
    def test_mode_is_set_first(self, ctlr, backend, params):
        ctlr.set_loop(1, 'loop', params)
        assert backend.mock_calls == [
            mock.call.set_loop_mode(N=1, value='Off'),
            mock.call.set_loop_sp(N=1, value=50.0),
            mock.call.set_loop_en(N=1, value=True),
        ]

    @pytest.mark.parametrize('parameter', ['no such parameter', 'deviation', 'enable_cascade'])
    def test_parameter_a_loop_does_not_have_is_ignored(self, ctlr, backend, parameter):
        ctlr.set_loop(1, 'loop', {parameter: 1, 'setpoint': 50.0})
        assert backend.mock_calls == [mock.call.set_loop_sp(N=1, value=50.0)]

    def test_parameter_the_controller_does_not_implement_is_ignored(self, ctlr, backend):
        backend.set_loop_power.side_effect = NotImplementedError
        ctlr.set_loop(1, 'loop', {'power': 80.0, 'setpoint': 50.0})
        backend.set_loop_sp.assert_called_once_with(N=1, value=50.0)

    def test_error_of_the_controller_is_raised(self, ctlr, backend):
        backend.set_loop_sp.side_effect = ValueError('out of range')
        with pytest.raises(ValueError):
            ctlr.set_loop(1, 'loop', {'setpoint': 5000.0})

    @pytest.mark.parametrize('identifier', [1.5, None, [1]])
    def test_invalid_identifier(self, ctlr, backend, identifier):
        with pytest.raises(ValueError):
            ctlr.set_loop(identifier, 'loop', {'setpoint': 50.0})
        assert backend.mock_calls == []


class TestGetOperation(object):
    @pytest.mark.parametrize('status, mode', [
        ('Constant', 'constant'),
        ('Standby', 'standby'),
        ('Off', 'off'),
        ('Alarm', 'alarm'),
        ('Program Running', 'program'),
        ('Program End Hold', 'program'),
        ('Program Paused', 'program_pause'),
        pytest.param('Remote Program Running', 'program', marks=pytest.mark.xfail(
            strict=True, reason='only a status starting with "Prog" is a program, the status '
                                '"Remote Program Running" of Espec.get_status() becomes the mode "ERROR"')),
        pytest.param('Remote Program End Hold', 'program', marks=pytest.mark.xfail(
            strict=True, reason='only a status starting with "Prog" is a program, the status '
                                '"Remote Program End Hold" of Espec.get_status() becomes the mode "ERROR"')),
    ])
    def test_mode(self, ctlr, backend, status, mode):
        backend.get_status.return_value = status
        operation = ctlr.get_operation()
        assert operation['status'] == status
        assert operation['mode'] == mode

    def test_unknown_status(self, ctlr, backend):
        backend.get_status.return_value = 'Defrosting'
        assert ctlr.get_operation()['mode'] == 'ERROR'

    @pytest.mark.parametrize('status', ['Constant', 'Standby', 'Off'])
    def test_without_program_and_alarm_nothing_else_is_read(self, ctlr, backend, status):
        backend.get_status.return_value = status
        assert ctlr.get_operation()['program'] is None
        assert backend.mock_calls == [mock.call.get_status()]

    @pytest.mark.parametrize('status', [
        'Program Running', 'Program Paused', 'Program End Hold', 'Remote Program Running', 'Remote Program Paused',
    ])
    def test_running_program(self, ctlr, backend, status):
        backend.get_status.return_value = status
        backend.get_prgm_cur.return_value = 4
        assert ctlr.get_operation()['program'] == {
            'number': 4,
            'step': backend.get_prgm_cstep.return_value,
            'time_remaining': backend.get_prgm_time.return_value,
            'step_time_remaining': backend.get_prgm_cstime.return_value,
            'name': backend.get_prgm_name.return_value,
            'steps': backend.get_prgm_steps.return_value,
            'cycles': backend.get_prgm_counter.return_value,
        }
        backend.get_prgm_name.assert_called_once_with(4)
        backend.get_prgm_steps.assert_called_once_with(4)
        backend.get_prgm_time.assert_called_once_with(None)
        backend.get_alarm_status.assert_not_called()

    def test_cached_program_is_used_for_the_remaining_time(self, ctlr, backend):
        backend.get_status.return_value = 'Program Running'
        cached_program = {'steps': []}
        ctlr.get_operation(cached_program)
        backend.get_prgm_time.assert_called_once_with(cached_program)

    def test_alarm_reads_the_active_alarms(self, ctlr, backend):
        backend.get_status.return_value = 'Alarm'
        assert ctlr.get_operation()['program'] is None
        backend.get_alarm_status.assert_called_once_with()
        backend.get_prgm_cur.assert_not_called()

    def test_active_alarms(self, ctlr, backend):
        # the documentation names the key "alarm", callers rely on "alarms"
        backend.get_status.return_value = 'Alarm'
        backend.get_alarm_status.return_value = {'active': [2, 12], 'inactive': [1]}
        assert ctlr.get_operation()['alarms'] == [2, 12]

    def test_one_connection_for_the_complete_status(self):
        ctlr = FakeController()
        ctlr.backend.get_status.return_value = 'Program Running'
        ctlr.get_operation()
        assert ctlr.connections == ['connect', 'close']


class TestSetOperation(object):
    @pytest.mark.parametrize('mode, kwargs, call', [
        ('standby', {}, mock.call.stop()),
        ('off', {}, mock.call.stop()),
        ('constant', {}, mock.call.const_start()),
        ('program', {'program': 3}, mock.call.prgm_start(3, 1)),
        ('program', {'program': 3, 'step': 4}, mock.call.prgm_start(3, 4)),
        ('program', {'program': {'number': 3}}, mock.call.prgm_start(3, 1)),
        ('program', {'program': {'number': 3, 'step': 4}}, mock.call.prgm_start(3, 4)),
        ('program', {'program': {'number': 3, 'step': 4}, 'step': 9}, mock.call.prgm_start(3, 4)),
        ('program_pause', {}, mock.call.prgm_pause()),
        ('program_resume', {}, mock.call.prgm_resume()),
        ('program_advance', {}, mock.call.prgm_next_step()),
    ])
    def test_mode(self, ctlr, backend, mode, kwargs, call):
        ctlr.set_operation(mode, **kwargs)
        assert backend.mock_calls == [call]

    @pytest.mark.parametrize('mode', ['paused', 'Constant', '', None])
    def test_unsupported_mode(self, ctlr, backend, mode):
        with pytest.raises(ValueError):
            ctlr.set_operation(mode)
        assert backend.mock_calls == []

    def test_program_needs_a_program_number(self, ctlr, backend):
        with pytest.raises(KeyError):
            ctlr.set_operation('program')
        assert backend.mock_calls == []


class TestPrograms(object):
    def test_get_program(self, ctlr, backend):
        assert ctlr.get_program(3) is backend.get_prgm.return_value
        backend.get_prgm.assert_called_once_with(3)

    def test_set_program(self, ctlr, backend):
        prgm = {'name': 'SOAK', 'steps': []}
        assert ctlr.set_program(3, prgm) is backend.set_prgm.return_value
        assert backend.mock_calls == [mock.call.set_prgm(3, prgm)]

    def test_set_program_none_deletes_the_program(self, ctlr, backend):
        assert ctlr.set_program(3, None) is backend.prgm_delete.return_value
        assert backend.mock_calls == [mock.call.prgm_delete(3)]

    def test_get_program_list(self, ctlr, backend):
        assert ctlr.get_program_list() is backend.get_prgms.return_value
        backend.get_prgms.assert_called_once_with()

    def test_get_program_details(self, ctlr, backend):
        backend.get_prgm_name.return_value = 'SOAK'
        backend.get_prgm_steps.return_value = 7
        assert ctlr.get_program_details(3) == {'number': 3, 'name': 'SOAK', 'steps': 7}
        assert backend.mock_calls == [mock.call.get_prgm_name(3), mock.call.get_prgm_steps(3)]


class TestSample(object):
    @pytest.fixture
    def backend(self, ctlr):
        ctlr.backend.get_datetime.return_value = datetime.datetime(2026, 11, 13, 12, 34, 56)
        ctlr.backend.get_status.return_value = 'Constant'
        ctlr.backend.get_cascade_pv.side_effect = lambda number: {'air': 23.5, 'product': 22.0}
        ctlr.backend.get_loop_pv.side_effect = lambda number: {'air': [None, 23.5, 45.0][number]}
        return ctlr.backend

    def test_sample(self, ctlr, backend):
        sample = ctlr.sample()
        assert sample['datetime'] == datetime.datetime(2026, 11, 13, 12, 34, 56)
        assert sample['status'] == 'Constant'
        assert [loop['processvalue'] for loop in sample['loops']] == [
            {'air': 23.5, 'product': 22.0}, {'air': 23.5}, {'air': 45.0},
        ]

    def test_parameters_of_the_loops(self, ctlr, backend):
        cascade, temperature, humidity = ctlr.sample()['loops']
        assert sorted(cascade) == ['enable', 'enable_cascade', 'mode', 'power', 'processvalue', 'setpoint']
        assert sorted(temperature) == ['enable', 'mode', 'power', 'processvalue', 'setpoint']
        assert sorted(humidity) == sorted(temperature)
        assert humidity['setpoint'] is backend.get_loop_sp.return_value
        assert backend.get_loop_sp.mock_calls == [mock.call(1), mock.call(2)]
        backend.get_cascade_ctl.assert_called_once_with(1)
        backend.get_loop_range.assert_not_called()

    def test_lookup_is_added_to_the_loops(self, ctlr, backend):
        lookup = {
            'cascade': [{'name': 'Product'}],
            'loop': [{'name': 'Temperature', 'processvalue': 'replaced'}, {'name': 'Humidity'}],
        }
        loops = ctlr.sample(lookup)['loops']
        assert [loop['name'] for loop in loops] == ['Product', 'Temperature', 'Humidity']
        assert loops[1]['processvalue'] == {'air': 23.5}
        assert lookup['loop'][0] == {'name': 'Temperature', 'processvalue': 'replaced'}

    def test_controller_without_loops(self, ctlr, backend):
        ctlr.loop_map = []
        assert ctlr.sample() == {
            'datetime': datetime.datetime(2026, 11, 13, 12, 34, 56), 'loops': [], 'status': 'Constant',
        }

    def test_one_connection_for_the_complete_sample(self):
        ctlr = FakeController()
        ctlr.sample()
        assert ctlr.connections == ['connect', 'close']


class TestSelfTest(object):
    @pytest.fixture(autouse=True)
    def sleeps(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(controllerinterface, 'time', types.SimpleNamespace(sleep=sleeps.append))
        return sleeps

    @pytest.fixture
    def backend(self, ctlr):
        backend = ctlr.backend
        backend.process_controller.return_value = 'FAKE-1'
        backend.get_status.return_value = 'Standby'
        backend.get_datetime.return_value = datetime.datetime(2026, 11, 13, 12, 34, 56)
        backend.get_loop_sp.side_effect = lambda number: {'constant': 10.0 * number, 'current': 11.0 * number}
        backend.get_loop_range.return_value = {'min': -40.0, 'max': 100.0}
        backend.get_loop_en.return_value = {'constant': True, 'current': False}
        backend.get_cascade_sp.return_value = {'constant': 30.0, 'current': 31.0}
        backend.get_cascade_ctl.return_value = {'constant': False, 'current': True}
        backend.get_cascade_deviation.return_value = {'positive': 5.0, 'negative': -5.0}
        backend.get_event.side_effect = lambda number: {'constant': False, 'current': number == 3}
        backend.get_prgm.return_value = {'name': 'SOAK'}
        backend.get_network_settings.return_value = {'address': '10.0.0.5'}
        return backend

    def test_prints_the_result_of_every_function(self, ctlr, backend, capsys):
        ctlr.self_test(2, 1)
        out = capsys.readouterr().out
        assert out.startswith("process_controller():\n\t'FAKE-1'\nget_datetime:\n")
        assert 'get_datetime:\n\tdatetime.datetime(2026, 11, 13, 12, 34, 56)\nset_datetime:\n\tok\n' in out
        assert "get_operation:\n\t{'status': 'Standby', 'mode': 'standby', 'program': None" in out
        assert "get_loop_sp(2):\n\t{'constant': 20.0, 'current': 22.0}\nset_loop_sp(2):\n\tok\n" in out
        assert "get_cascade_deviation(1):\n\t{'positive': 5.0, 'negative': -5.0}\n" in out
        assert "get_event(12):\n\t{'constant': False, 'current': False}\nset_event(12):\n\tok\n" in out
        assert "get_status:\n\t'Standby'\n" in out
        assert 'call prgm_resume():\n\tok\ncall sample():\n' in out
        assert out.count('\tok\n') == 31
        assert 'Traceback' not in out
        assert out.endswith('Testing Complete\n')

    def test_writes_back_what_it_has_read(self, ctlr, backend, capsys):
        ctlr.self_test(2, 1)
        backend.set_datetime.assert_called_once_with(datetime.datetime(2026, 11, 13, 12, 34, 56))
        assert backend.set_loop_sp.mock_calls == [mock.call(1, 10.0), mock.call(2, 20.0)]
        assert backend.set_loop_range.mock_calls == [
            mock.call(1, {'min': -40.0, 'max': 100.0}), mock.call(2, {'min': -40.0, 'max': 100.0}),
        ]
        assert backend.set_loop_en.mock_calls == [mock.call(1, True), mock.call(2, True)]
        backend.set_cascade_sp.assert_called_once_with(1, 30.0)
        backend.set_cascade_ctl.assert_called_once_with(1, {'constant': False, 'current': True})
        backend.set_cascade_deviation.assert_called_once_with(1, {'positive': 5.0, 'negative': -5.0})
        assert backend.set_event.mock_calls == [mock.call(number, number == 3) for number in range(1, 13)]
        backend.set_network_settings.assert_called_once_with({'address': '10.0.0.5'})

    def test_runs_the_chamber(self, ctlr, backend, capsys, sleeps):
        ctlr.self_test(1, 0)
        operations = [call for call in backend.mock_calls if call[0] in (
            'const_start', 'stop', 'prgm_start', 'prgm_pause', 'prgm_resume', 'prgm_next_step',
        )]
        assert operations == [
            mock.call.const_start(), mock.call.stop(), mock.call.prgm_start(1, 1),
            mock.call.prgm_pause(), mock.call.prgm_resume(),
        ]
        assert sleeps == [5, 5, 5, 5, 5]

    @pytest.mark.parametrize('loops, cascades', [(0, 0), (1, 0), (2, 1), (4, 2)])
    def test_number_of_loops_and_cascades(self, ctlr, backend, capsys, loops, cascades):
        backend.get_cascade_sp.side_effect = None
        ctlr.loop_map = []
        ctlr.self_test(loops, cascades)
        out = capsys.readouterr().out
        assert backend.get_loop_pv.mock_calls == [mock.call(number) for number in range(1, loops + 1)]
        assert backend.get_cascade_pv.mock_calls == [mock.call(number) for number in range(1, cascades + 1)]
        assert out.count('\nget_loop_units(') == loops
        assert out.count('\nget_cascade_units(') == cascades

    def test_reads_the_first_five_programs(self, ctlr, backend, capsys):
        ctlr.self_test(0, 0)
        assert backend.get_prgm_name.mock_calls == [mock.call(number) for number in range(1, 6)]
        assert backend.get_prgm_steps.mock_calls == [mock.call(number) for number in range(1, 6)]
        backend.get_prgms.assert_called_once_with()

    def test_connects_for_every_function_when_not_connected(self, capsys):
        ctlr = FakeController()
        ctlr.self_test(0, 0)
        assert ctlr.client is None
        assert ctlr.connections[:4] == ['connect', 'close', 'connect', 'close']
        assert 'Traceback' not in capsys.readouterr().out

    def test_errors_are_printed_and_the_test_goes_on(self, ctlr, capsys, sleeps):
        ctlr.backend = BrokenBackend()
        ctlr.self_test(2, 1)
        out = capsys.readouterr().out
        assert out.startswith('process_controller():\n\tTraceback (most recent call last):\n')
        assert '\tRuntimeError: process_controller failed\n' in out
        assert '\tRuntimeError: get_loop_sp failed\n' in out
        assert '\tRuntimeError: get_cascade_deviation failed\n' in out
        assert '\tRuntimeError: get_event failed\n' in out
        assert 'call sample():\n\tTraceback (most recent call last):\n' in out
        assert '\tok\n' not in out
        assert out.endswith('Testing Complete\n')
        assert sleeps == []

    def test_one_failing_function_does_not_stop_the_others(self, ctlr, backend, capsys):
        backend.get_loop_pv.side_effect = RuntimeError('sensor broken')
        ctlr.self_test(1, 0)
        out = capsys.readouterr().out
        assert 'get_loop_pv(1):\n\tTraceback (most recent call last):\n' in out
        assert '\tRuntimeError: sensor broken\n' in out
        assert "get_loop_range(1):\n\t{'min': -40.0, 'max': 100.0}\n" in out
        assert out.endswith('Testing Complete\n')
