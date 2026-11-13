'''
Tests for the SCP220 controller: only what differs from the P300 (see test_p300.py).

The answer strings are derived from what the parsers expect, the SCP220 sends the
negative deviation of the product temperature control without a sign.
'''
import copy

import pytest

from chamberconnectlibrary.especinteract import EspecError
from chamberconnectlibrary.p300 import P300
from chamberconnectlibrary.scp220 import SCP220

PRGM_DATA = '1,<MYPROG>,COUNT,A(1.1.3),B(0.0.0),END(STANDBY)'
STEP = '1,TEMP25.0,TEMP RAMP OFF,TIME1:30,GRANTY OFF,REF9'
STEP_PTC = '1,TEMP25.0,TEMP RAMP OFF,PTC ON,TIME1:30,GRANTY OFF,REF9,PAUSE OFF,DEVP5.0,DEVN2.5'

STEP_PARSED = {
    'number': 1,
    'time': {'hour': 1, 'minute': 30},
    'paused': False,
    'granty': False,
    'refrig': {'mode': 'auto', 'setpoint': 0},
    'temperature': {'setpoint': 25.0, 'ramp': False},
    'relay': [False] * 12,
}
STEP_PTC_PARSED = dict(STEP_PARSED, temperature={
    'setpoint': 25.0,
    'ramp': False,
    'enable_cascade': True,
    'deviation': {'positive': 5.0, 'negative': -2.5},
})
PRGM_PARSED = {
    'name': 'MYPROG',
    'end': 'STANDBY',
    'next_prgm': 0,
    'counter_a': {'start': 1, 'end': 1, 'cycles': 3},
    'counter_b': {'start': 0, 'end': 0, 'cycles': 0},
}


@pytest.fixture
def scp220(espec_link):
    return SCP220('TCP', host='chamber')


class TestConnection(object):
    def test_is_a_p300(self, scp220):
        assert isinstance(scp220, P300)

    def test_tcp_parameters(self, espec_link):
        SCP220('TCP', host='chamber', port=10001, timeout=5, address=2)
        assert espec_link.created_with == [{'host': 'chamber', 'address': 2, 'port': 10001, 'timeout': 5}]

    def test_serial_parameters(self, espec_link):
        SCP220('Serial', serialport='/dev/ttyUSB0', baudrate=9600, address=1)
        assert espec_link.created_with == [{'port': '/dev/ttyUSB0', 'baud': 9600, 'address': 1}]

    def test_commands_of_the_p300_are_inherited(self, scp220, espec_link):
        espec_link.answers['TEMP?'] = '23.4,25.0,180.0,-70.0'
        assert scp220.read_temp() == {
            'processvalue': 23.4, 'setpoint': 25.0, 'enable': True, 'range': {'max': 180.0, 'min': -70.0}
        }
        scp220.close()
        assert espec_link.closed

    @pytest.mark.parametrize('number, kind', [(1, 'RAM'), (20, 'RAM'), (21, 'ROM'), (30, 'ROM')])
    def test_rom_pgm(self, scp220, number, kind):
        assert scp220.rom_pgm(number) == kind


class TestRead(object):
    @pytest.mark.parametrize('detail', [False, True])
    def test_read_mode_never_asks_for_detail(self, scp220, espec_link, detail):
        espec_link.answers['MODE?'] = 'RUN'
        assert scp220.read_mode(detail) == 'RUN'
        assert espec_link.commands == ['MODE?']

    @pytest.mark.parametrize('detail', [False, True])
    def test_read_mon_never_asks_for_detail(self, scp220, espec_link, detail):
        espec_link.answers['MON?'] = '23.4,45,CONSTANT,0'
        assert scp220.read_mon(detail) == {
            'temperature': 23.4, 'humidity': 45.0, 'mode': 'CONSTANT', 'alarms': 0
        }
        assert espec_link.commands == ['MON?']

    @pytest.mark.parametrize('answer, expected', [
        ('ON,5.0,2.5', {'enable': True, 'deviation': {'positive': 5.0, 'negative': -2.5}}),
        ('OFF,10.0,0.0', {'enable': False, 'deviation': {'positive': 10.0, 'negative': 0.0}}),
    ])
    def test_read_constant_ptc(self, scp220, espec_link, answer, expected):
        espec_link.answers['CONSTANT SET?,PTC'] = answer
        assert scp220.read_constant_ptc() == expected
        assert espec_link.commands == ['CONSTANT SET?,PTC']

    @pytest.mark.parametrize('answer, expected', [
        ('50', {'mode': 'manual', 'setpoint': 50.0}),
        ('OFF', {'mode': 'off', 'setpoint': 0}),
        ('AUTO', {'mode': 'auto', 'setpoint': 0}),
        ('REF9', {'mode': 'auto', 'setpoint': 0}),
    ])
    def test_read_constant_ref(self, scp220, espec_link, answer, expected):
        espec_link.answers['CONSTANT SET?,REF'] = answer
        assert scp220.read_constant_ref() == expected
        assert espec_link.commands == ['CONSTANT SET?,REF']

    def test_read_temp_ptc(self, scp220, espec_link):
        espec_link.answers['TEMP PTC?'] = 'ON,25.0,24.5,26.0,25.5,5.0,2.5'
        assert scp220.read_temp_ptc() == {
            'enable': True,
            'enable_cascade': True,
            'deviation': {'positive': 5.0, 'negative': -2.5},
            'processvalue': {'air': 24.5, 'product': 25.0},
            'setpoint': {'air': 25.5, 'product': 26.0},
        }
        assert espec_link.commands == ['TEMP PTC?']

    def test_read_prgm_data_ptc_step(self, scp220, espec_link):
        espec_link.answers['PRGM DATA PTC?,RAM:3,STEP1'] = STEP_PTC
        assert scp220.read_prgm_data_ptc_step(3, 1) == STEP_PTC_PARSED
        assert espec_link.commands == ['PRGM DATA PTC?,RAM:3,STEP1']

    def test_programs_above_20_are_rom_programs(self, scp220, espec_link):
        espec_link.answers['PRGM USE?,ROM:21'] = 'CYCLE 1,07.01/02'
        assert scp220.read_prgm_use_num(21) == {'name': 'CYCLE 1', 'date': {'year': 2007, 'month': 1, 'day': 2}}

    @pytest.mark.parametrize('method, args', [
        ('read_ip_set', ()),
        ('write_ip_set', ('192.168.0.10', '255.255.255.0', '192.168.0.1')),
        ('read_prgm_data_detail', (3,)),
        ('read_prgm_data_ptc_detail', (3,)),
    ])
    def test_not_available_on_the_scp220(self, scp220, espec_link, method, args):
        espec_link.default = 'OK'
        with pytest.raises(NotImplementedError):
            getattr(scp220, method)(*args)
        assert espec_link.commands == []


class TestWrite(object):
    @pytest.mark.parametrize('negative', [-2.5, 2.5])
    def test_write_temp_ptc_sends_the_negative_deviation_without_sign(self, scp220, espec_link, negative):
        espec_link.default = 'OK'
        scp220.write_temp_ptc(True, 5, negative)
        assert espec_link.commands == ['TEMP PTC, PTCON, DEVP5.0, DEVN2.5']

    @pytest.mark.parametrize('step, command', [
        ({'number': 1, 'temperature': {'setpoint': 25, 'ramp': False, 'enable_cascade': True,
                                       'deviation': {'positive': 5, 'negative': -2.5}}},
         'PRGM DATA WRITE, PGM3, STEP1,TEMP25.0,TRAMPOFF,PTCON,DEVP5.0,DEVN2.5'),
        ({'number': 1, 'temperature': {'setpoint': 25, 'ramp': True}},
         'PRGM DATA WRITE, PGM3, STEP1,TEMP25.0,TRAMPON'),
        ({'number': 2, 'time': {'hour': 1, 'minute': 30}, 'granty': False},
         'PRGM DATA WRITE, PGM3, STEP2,TIME1:30,GRANTY OFF'),
    ], ids=['deviation', 'temperature', 'time'])
    def test_write_prgm_data_step(self, scp220, espec_link, step, command):
        espec_link.default = 'OK'
        scp220.write_prgm_data_step(3, **copy.deepcopy(step))
        assert espec_link.commands == [command]


class TestProgram(object):
    def test_read_prgm_skips_the_detail_command(self, scp220, espec_link):
        espec_link.answers.update({'PRGM DATA?,RAM:3': PRGM_DATA, 'PRGM DATA?,RAM:3,STEP1': STEP})
        assert scp220.read_prgm(3) == dict(PRGM_PARSED, steps=[STEP_PARSED])
        assert espec_link.commands == ['PRGM DATA?,RAM:3', 'PRGM DATA?,RAM:3,STEP1']

    def test_read_prgm_with_ptc(self, scp220, espec_link):
        espec_link.answers.update({'PRGM DATA PTC?,RAM:3': PRGM_DATA, 'PRGM DATA PTC?,RAM:3,STEP1': STEP_PTC})
        assert scp220.read_prgm(3, with_ptc=True) == dict(PRGM_PARSED, steps=[STEP_PTC_PARSED])
        assert espec_link.commands == ['PRGM DATA PTC?,RAM:3', 'PRGM DATA PTC?,RAM:3,STEP1']

    def test_read_prgm_of_a_rom_program(self, scp220, espec_link):
        espec_link.answers.update({'PRGM DATA?,ROM:30': PRGM_DATA, 'PRGM DATA?,ROM:30,STEP1': STEP})
        assert scp220.read_prgm(30) == dict(PRGM_PARSED, steps=[STEP_PARSED])

    def test_read_prgm_0_is_an_empty_program_without_details(self, scp220, espec_link):
        espec_link.answers.update({'TEMP?': '23.4,25.0,180.0,-70.0', 'HUMI?': '45,50,95,10'})
        program = scp220.read_prgm(0)
        assert sorted(program) == ['counter_a', 'counter_b', 'end', 'name', 'next_prgm', 'steps']
        assert program['steps'] == [{
            'granty': False,
            'number': 1,
            'paused': False,
            'refrig': {'mode': 'auto', 'setpoint': 0},
            'time': {'hour': 1, 'minute': 0},
            'relay': [False] * 12,
            'temperature': {'ramp': False, 'setpoint': 0.0},
            'humidity': {'enable': False, 'ramp': False, 'setpoint': 0.0},
        }]

    def test_read_prgm_0_with_ptc(self, scp220, espec_link):
        espec_link.answers.update({
            'TEMP?': '23.4,25.0,180.0,-70.0', 'TEMP PTC?': 'ON,25.0,24.5,26.0,25.5,5.0,2.5'
        })
        assert scp220.read_prgm(0, with_ptc=True)['steps'][0]['temperature'] == {
            'ramp': False, 'setpoint': 0.0, 'deviation': {'positive': 5.0, 'negative': -2.5},
            'enable_cascade': False
        }

    @pytest.mark.parametrize('number', [-1, 31, 41])
    def test_read_prgm_rejects_other_numbers(self, scp220, espec_link, number):
        with pytest.raises(ValueError):
            scp220.read_prgm(number)
        assert espec_link.commands == []

    def test_error_of_the_controller_is_passed_on(self, scp220):
        with pytest.raises(EspecError):
            scp220.read_prgm(3)

    def test_write_prgm(self, scp220, espec_link):
        espec_link.default = 'OK'
        step = {'temperature': {'setpoint': 25, 'ramp': False, 'enable_cascade': True,
                                'deviation': {'positive': 5, 'negative': -2.5}}}
        scp220.write_prgm(20, {'name': 'MYPROG', 'end': 'STANDBY', 'steps': [step]})
        assert espec_link.commands == [
            'PRGM DATA WRITE, PGM20, EDIT START',
            'PRGM DATA WRITE, PGM20, STEP1,TEMP25.0,TRAMPOFF,PTCON,DEVP5.0,DEVN2.5',
            'PRGM DATA WRITE, PGM20, NAME,MYPROG',
            'PRGM DATA WRITE, PGM20, END,STANDBY',
            'PRGM DATA WRITE, PGM20, EDIT END',
        ]

    def test_write_prgm_rejects_rom_programs(self, scp220, espec_link):
        espec_link.default = 'OK'
        with pytest.raises(ValueError):
            scp220.write_prgm(21, {'steps': []})
        assert espec_link.commands == []
