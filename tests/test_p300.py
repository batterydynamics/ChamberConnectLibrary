'''
Tests for the command set of the P300 controller.

The answers of a real controller are not recorded anywhere: the answer strings used here
are derived from what the parsers in p300.py expect. Tests marked xfail describe the
documented behaviour that the library does not deliver.
'''
import copy

import pytest

from chamberconnectlibrary.especinteract import EspecError
from chamberconnectlibrary.p300 import P300, tryfloat


def relays(*switched_on):
    return [number in switched_on for number in range(1, 13)]


REF_OFF = {'mode': 'off', 'setpoint': 0}
REF_AUTO = {'mode': 'auto', 'setpoint': 0}

PRGM_DATA = '2,<MYPROG>,COUNT,A(1.2.3),B(0.0.0),END(STANDBY)'
PRGM_DATA_PARSED = {
    'steps': 2,
    'name': 'MYPROG',
    'end': 'STANDBY',
    'next_prgm': 0,
    'counter_a': {'start': 1, 'end': 2, 'cycles': 3},
    'counter_b': {'start': 0, 'end': 0, 'cycles': 0},
}
PRGM_DATA_NEXT = '9,<CYCLE 1>,COUNT,A(1.4.10),B(5.9.2),END(RUN:7)'
PRGM_DATA_NEXT_PARSED = {
    'steps': 9,
    'name': 'CYCLE 1',
    'end': 'RUN',
    'next_prgm': 7,
    'counter_a': {'start': 1, 'end': 4, 'cycles': 10},
    'counter_b': {'start': 5, 'end': 9, 'cycles': 2},
}

DETAIL_TEMP = '180.0,-70.0,TEMPOFF'
DETAIL_TEMP_PARSED = {
    'tempDetail': {'range': {'max': 180.0, 'min': -70.0}, 'mode': 'OFF', 'setpoint': None},
}
DETAIL_HUMI = '180.0,-70.0,100,0,TEMPOFF,HUMIOFF'
DETAIL_HUMI_PARSED = {
    'tempDetail': {'range': {'max': 180.0, 'min': -70.0}, 'mode': 'OFF', 'setpoint': None},
    'humiDetail': {'range': {'max': 100.0, 'min': 0.0}, 'mode': 'OFF', 'setpoint': None},
}
DETAIL_SETPOINTS = '180.0,-70.0,100,0,TEMPSV,25.0,HUMISV,50'

STEP_TEMP = '1,TEMP25.0,TEMP RAMP OFF,TIME1:30,GRANTY OFF,REF9'
STEP_TEMP_PARSED = {
    'number': 1,
    'time': {'hour': 1, 'minute': 30},
    'paused': False,
    'granty': False,
    'refrig': REF_AUTO,
    'temperature': {'setpoint': 25.0, 'ramp': False},
    'relay': relays(),
}
STEP_HUMI = '2,TEMP85.0,TEMP RAMP ON,HUMI85,HUMI RAMP ON,TIME0:45,GRANTY ON,REF6,RELAY ON1.5,PAUSE ON'
STEP_HUMI_PARSED = {
    'number': 2,
    'time': {'hour': 0, 'minute': 45},
    'paused': True,
    'granty': True,
    'refrig': {'mode': 'manual', 'setpoint': 100},
    'temperature': {'setpoint': 85.0, 'ramp': True},
    'humidity': {'setpoint': 85.0, 'enable': True, 'ramp': True},
    'relay': relays(1, 5),
}
STEP_HUMI_OFF = '3,TEMP-40.0,TEMP RAMP OFF,HUMI OFF,TIME2:15,GRANTY OFF,REF0,PAUSE OFF'
STEP_HUMI_OFF_PARSED = {
    'number': 3,
    'time': {'hour': 2, 'minute': 15},
    'paused': False,
    'granty': False,
    'refrig': REF_OFF,
    'temperature': {'setpoint': -40.0, 'ramp': False},
    'humidity': {'setpoint': 0.0, 'enable': False, 'ramp': False},
    'relay': relays(),
}
STEP_PTC = '1,TEMP25.0,TEMP RAMP OFF,PTC ON,TIME1:30,GRANTY OFF,REF9,PAUSE OFF,DEVP5.0,DEVN-5.0'
STEP_PTC_PARSED = {
    'number': 1,
    'time': {'hour': 1, 'minute': 30},
    'paused': False,
    'granty': False,
    'refrig': REF_AUTO,
    'temperature': {
        'setpoint': 25.0,
        'ramp': False,
        'enable_cascade': True,
        'deviation': {'positive': 5.0, 'negative': -5.0},
    },
    'relay': relays(),
}

STEP_TEMP_WRITTEN = (
    'PRGM DATA WRITE, PGM4, STEP1,TIME1:30,PAUSE OFF,REF9,GRANTY OFF,TEMP25.0,TRAMPOFF'
    ',RELAY OFF1.2.3.4.5.6.7.8.9.10.11.12'
)
STEP_HUMI_WRITTEN = (
    'PRGM DATA WRITE, PGM4, STEP2,TIME0:45,PAUSE ON,REF6,GRANTY ON,TEMP85.0,TRAMPON,HUMI85,HRAMPON'
    ',RELAY ON1.5,RELAY OFF2.3.4.6.7.8.9.10.11.12'
)

# method, arguments, command sent, answer of the controller, parsed result
READS = [
    ('read_rom', (), 'ROM?', 'P3001.05', 'P3001.05'),
    ('read_rom', (True,), 'ROM?,DISP', 'P300D2.10', 'P300D2.10'),
    ('read_date', (), 'DATE?', '26.11/13', {'year': 2026, 'month': 11, 'day': 13}),
    ('read_time', (), 'TIME?', '14:05:09', {'hour': 14, 'minute': 5, 'second': 9}),
    ('read_srq', (), 'SRQ?', '01000010',
     {'alarm': True, 'single_step_done': False, 'state_change': False, 'GPIB': True}),
    ('read_srq', (), 'SRQ?', '00110000',
     {'alarm': False, 'single_step_done': True, 'state_change': True, 'GPIB': False}),
    ('read_mask', (), 'MASK?', '01000010',
     {'alarm': True, 'single_step_done': False, 'state_change': False, 'GPIB': True}),
    ('read_mask', (), 'MASK?', '00110000',
     {'alarm': False, 'single_step_done': True, 'state_change': True, 'GPIB': False}),
    ('read_timer_on', (), 'TIMER ON?', '0', []),
    ('read_timer_on', (), 'TIMER ON?', '2,0,2', [0, 2]),
    ('read_timer_use', (), 'TIMER USE?', '0', []),
    ('read_timer_use', (), 'TIMER USE?', '3,0,1,2', [0, 1, 2]),
    ('read_timer_list_quick', (), 'TIMER LIST?,0', '0,STANDBY,1:30',
     {'mode': 'STANDBY', 'time': {'hour': 1, 'minute': 30}}),
    ('read_timer_list_quick', (), 'TIMER LIST?,0', '0,RUN,RAM:3,STEP2,0:45',
     {'mode': 'RUN', 'time': {'hour': 0, 'minute': 45}, 'pgmnum': 3, 'pgmstep': 2}),
    ('read_timer_list_quick', (), 'TIMER LIST?,0', '0,RUN,ROM:41,STEP1,12:00',
     {'mode': 'RUN', 'time': {'hour': 12, 'minute': 0}, 'pgmnum': 41, 'pgmstep': 1}),
    ('read_timer_list_start', (), 'TIMER LIST?,1', '1,MODE1,26.11/13,8:30,CONSTANT',
     {'repeat': 'once', 'time': {'hour': 8, 'minute': 30}, 'mode': 'CONSTANT',
      'date': {'year': 2026, 'month': 11, 'day': 13}}),
    ('read_timer_list_start', (), 'TIMER LIST?,1', '1,MODE2,MON/WED,8:30,RUN,RAM:3,STEP2',
     {'repeat': 'weekly', 'time': {'hour': 8, 'minute': 30}, 'mode': 'RUN', 'days': ['MON', 'WED'],
      'pgmnum': 3, 'pgmstep': 2}),
    ('read_timer_list_start', (), 'TIMER LIST?,1', '1,MODE3,8:30,CONSTANT',
     {'repeat': 'daily', 'time': {'hour': 8, 'minute': 30}, 'mode': 'CONSTANT'}),
    ('read_timer_list_stop', (), 'TIMER LIST?,2', '2,MODE1,26.11/13,17:45,OFF',
     {'repeat': 'once', 'time': {'hour': 17, 'minute': 45}, 'mode': 'OFF',
      'date': {'year': 2026, 'month': 11, 'day': 13}}),
    ('read_timer_list_stop', (), 'TIMER LIST?,2', '2,MODE2,FRI,17:45,STANDBY',
     {'repeat': 'weekly', 'time': {'hour': 17, 'minute': 45}, 'mode': 'STANDBY', 'days': ['FRI']}),
    ('read_timer_list_stop', (), 'TIMER LIST?,2', '2,MODE3,17:45,STANDBY',
     {'repeat': 'daily', 'time': {'hour': 17, 'minute': 45}, 'mode': 'STANDBY'}),
    ('read_alarm', (), 'ALARM?', '0', []),
    ('read_alarm', (), 'ALARM?', '2,12,25', [12, 25]),
    ('read_keyprotect', (), 'KEYPROTECT?', 'ON', True),
    ('read_keyprotect', (), 'KEYPROTECT?', 'OFF', False),
    ('read_type', (), 'TYPE?', 'T,T,P300,180.0',
     {'drybulb': 'T', 'wetbulb': 'T', 'controller': 'P300', 'tempmax': 180.0}),
    ('read_type', (), 'TYPE?', 'T,P300,180.0', {'drybulb': 'T', 'controller': 'P300', 'tempmax': 180.0}),
    ('read_mode', (), 'MODE?', 'STANDBY', 'STANDBY'),
    ('read_mode', (False,), 'MODE?', 'CONSTANT', 'CONSTANT'),
    ('read_mode', (True,), 'MODE?,DETAIL', 'RUN PAUSE', 'RUN PAUSE'),
    ('read_mon', (), 'MON?', '23.4,,STANDBY,0', {'temperature': 23.4, 'mode': 'STANDBY', 'alarms': 0}),
    ('read_mon', (), 'MON?', '-40.1,,CONSTANT,2', {'temperature': -40.1, 'mode': 'CONSTANT', 'alarms': 2}),
    ('read_mon', (), 'MON?', '23.4,45,CONSTANT,0',
     {'temperature': 23.4, 'humidity': 45.0, 'mode': 'CONSTANT', 'alarms': 0}),
    ('read_mon', (True,), 'MON?,DETAIL', '23.4,,RUN END HOLD,1',
     {'temperature': 23.4, 'mode': 'RUN END HOLD', 'alarms': 1}),
    ('read_temp', (), 'TEMP?', '23.4,25.0,180.0,-70.0',
     {'processvalue': 23.4, 'setpoint': 25.0, 'enable': True, 'range': {'max': 180.0, 'min': -70.0}}),
    ('read_temp', (), 'TEMP?', '-39.8,-40.0,100.0,-45.0',
     {'processvalue': -39.8, 'setpoint': -40.0, 'enable': True, 'range': {'max': 100.0, 'min': -45.0}}),
    ('read_humi', (), 'HUMI?', '45,50,100,0',
     {'processvalue': 45.0, 'setpoint': 50.0, 'enable': True, 'range': {'max': 100.0, 'min': 0.0}}),
    ('read_humi', (), 'HUMI?', '45,OFF,100,0',
     {'processvalue': 45.0, 'setpoint': 0, 'enable': False, 'range': {'max': 100.0, 'min': 0.0}}),
    ('read_set', (), 'SET?', 'REF0', {'mode': 'off', 'setpoint': 0}),
    ('read_set', (), 'SET?', 'REF1', {'mode': 'manual', 'setpoint': 20}),
    ('read_set', (), 'SET?', 'REF3', {'mode': 'manual', 'setpoint': 50}),
    ('read_set', (), 'SET?', 'REF6', {'mode': 'manual', 'setpoint': 100}),
    ('read_set', (), 'SET?', 'REF9', {'mode': 'auto', 'setpoint': 0}),
    ('read_ref', (), 'REF?', '2,ON1,OFF2', [True, False]),
    ('read_ref', (), 'REF?', '2,OFF1,ON2', [False, True]),
    ('read_ref', (), 'REF?', '1,ON1', [True]),
    ('read_ref', (), 'REF?', '1,OFF1', [False]),
    ('read_relay', (), 'RELAY?', '0', relays()),
    ('read_relay', (), 'RELAY?', '3,1,5,12', relays(1, 5, 12)),
    ('read_htr', (), '%?', '2,45.5,10.0', {'dry': 45.5, 'wet': 10.0}),
    ('read_htr', (), '%?', '1,45.5', {'dry': 45.5}),
    ('read_constant_temp', (), 'CONSTANT SET?,TEMP', '25.0,ON', {'setpoint': 25.0, 'enable': True}),
    ('read_constant_humi', (), 'CONSTANT SET?,HUMI', '50,ON', {'setpoint': 50.0, 'enable': True}),
    ('read_constant_humi', (), 'CONSTANT SET?,HUMI', '50,OFF', {'setpoint': 50.0, 'enable': False}),
    ('read_constant_ref', (), 'CONSTANT SET?,REF', '50', {'mode': 'manual', 'setpoint': 50.0}),
    ('read_constant_ref', (), 'CONSTANT SET?,REF', 'AUTO', {'mode': 'auto', 'setpoint': 0}),
    ('read_constant_ref', (), 'CONSTANT SET?,REF', 'OFF', {'mode': 'off', 'setpoint': 0}),
    ('read_constant_relay', (), 'CONSTANT SET?,RELAY', '0', relays()),
    ('read_constant_relay', (), 'CONSTANT SET?,RELAY', '2,2,11', relays(2, 11)),
    ('read_constant_ptc', (), 'CONSTANT SET?,PTC', 'ON,5.0,-5.0',
     {'enable': True, 'deviation': {'positive': 5.0, 'negative': -5.0}}),
    ('read_constant_ptc', (), 'CONSTANT SET?,PTC', 'OFF,10.0,-2.5',
     {'enable': False, 'deviation': {'positive': 10.0, 'negative': -2.5}}),
    ('read_prgm_mon', (), 'PRGM MON?', '3,25.0,50,1:30,2,0',
     {'pgmstep': 3, 'temperature': 25.0, 'humidity': 50.0, 'time': {'hour': 1, 'minute': 30},
      'counter_a': 2, 'counter_b': 0}),
    ('read_prgm_mon', (), 'PRGM MON?', '3,25.0,OFF,1:30,2,0',
     {'pgmstep': 3, 'temperature': 25.0, 'humidity': 0, 'time': {'hour': 1, 'minute': 30},
      'counter_a': 2, 'counter_b': 0}),
    ('read_prgm_mon', (), 'PRGM MON?', '3,25.0,1:30,2,4',
     {'pgmstep': 3, 'temperature': 25.0, 'time': {'hour': 1, 'minute': 30}, 'counter_a': 2, 'counter_b': 4}),
    ('read_prgm_set', (), 'PRGM SET?', 'RAM:3,MYPROG,END(STANDBY)',
     {'number': 3, 'name': 'MYPROG', 'end': 'STANDBY'}),
    ('read_prgm_set', (), 'PRGM SET?', 'ROM:41,CYCLE 1,END(HOLD)',
     {'number': 41, 'name': 'CYCLE 1', 'end': 'HOLD'}),
    ('read_prgm_use_num', (3,), 'PRGM USE?,RAM:3', 'MYPROG,26.11/13',
     {'name': 'MYPROG', 'date': {'year': 2026, 'month': 11, 'day': 13}}),
    ('read_prgm_use_num', (41,), 'PRGM USE?,ROM:41', 'CYCLE 1,07.01/02',
     {'name': 'CYCLE 1', 'date': {'year': 2007, 'month': 1, 'day': 2}}),
    ('read_prgm_data', (3,), 'PRGM DATA?,RAM:3', PRGM_DATA, PRGM_DATA_PARSED),
    ('read_prgm_data', (41,), 'PRGM DATA?,ROM:41', PRGM_DATA_NEXT, PRGM_DATA_NEXT_PARSED),
    ('read_prgm_data_detail', (3,), 'PRGM DATA?,RAM:3,DETAIL', DETAIL_TEMP, DETAIL_TEMP_PARSED),
    ('read_prgm_data_detail', (41,), 'PRGM DATA?,ROM:41,DETAIL', DETAIL_HUMI, DETAIL_HUMI_PARSED),
    ('read_prgm_data_step', (3, 1), 'PRGM DATA?,RAM:3,STEP1', STEP_TEMP, STEP_TEMP_PARSED),
    ('read_prgm_data_step', (3, 2), 'PRGM DATA?,RAM:3,STEP2', STEP_HUMI, STEP_HUMI_PARSED),
    ('read_prgm_data_step', (41, 3), 'PRGM DATA?,ROM:41,STEP3', STEP_HUMI_OFF, STEP_HUMI_OFF_PARSED),
    ('read_system_set', (), 'SYSTEM SET?,PTCOPT', 'ON', 'ON'),
    ('read_system_set', ('PTC',), 'SYSTEM SET?,PTC', 'OFF', 'OFF'),
    ('read_mon_ptc', (), 'MON PTC?', '25.0,24.5,50,CONSTANT,0',
     {'temperature': {'product': 25.0, 'air': 24.5}, 'humidity': 50.0, 'mode': 'CONSTANT', 'alarms': 0}),
    ('read_mon_ptc', (), 'MON PTC?', '25.0,24.5,STANDBY,1',
     {'temperature': {'product': 25.0, 'air': 24.5}, 'mode': 'STANDBY', 'alarms': 1}),
    ('read_temp_ptc', (), 'TEMP PTC?', 'ON,25.0,24.5,26.0,25.5,5.0,-5.0',
     {'enable': True, 'enable_cascade': True, 'deviation': {'positive': 5.0, 'negative': -5.0},
      'processvalue': {'air': 24.5, 'product': 25.0}, 'setpoint': {'air': 26.0, 'product': 25.5}}),
    ('read_temp_ptc', (), 'TEMP PTC?', 'OFF,25.0,24.5,26.0,OFF,OFF,OFF',
     {'enable': True, 'enable_cascade': False, 'deviation': {'positive': 0, 'negative': 0},
      'processvalue': {'air': 24.5, 'product': 25.0}, 'setpoint': {'air': 26.0, 'product': 0}}),
    ('read_set_ptc', (), 'SET PTC?', 'ON,5.0,-5.0',
     {'enable_cascade': True, 'deviation': {'positive': 5.0, 'negative': -5.0}}),
    ('read_set_ptc', (), 'SET PTC?', 'OFF,10.0,-2.5',
     {'enable_cascade': False, 'deviation': {'positive': 10.0, 'negative': -2.5}}),
    ('read_ptc', (), 'PTC?', '180.0,-70.0,1.5,2.5,3.5,0.0,0.0',
     {'range': {'max': 180.0, 'min': -70.0}, 'p': 1.5, 'filter': 2.5, 'i': 3.5, 'opt1': 0.0, 'opt2': 0.0}),
    ('read_prgm_data_ptc', (3,), 'PRGM DATA PTC?,RAM:3', PRGM_DATA, PRGM_DATA_PARSED),
    ('read_prgm_data_ptc', (41,), 'PRGM DATA PTC?,ROM:41', PRGM_DATA_NEXT, PRGM_DATA_NEXT_PARSED),
    ('read_prgm_data_ptc_detail', (3,), 'PRGM DATA PTC?,RAM:3,DETAIL', DETAIL_TEMP, DETAIL_TEMP_PARSED),
    ('read_prgm_data_ptc_step', (3, 1), 'PRGM DATA PTC?,RAM:3,STEP1', STEP_PTC, STEP_PTC_PARSED),
    ('read_prgm_data_ptc_step', (41, 2), 'PRGM DATA PTC?,ROM:41,STEP2', STEP_HUMI, STEP_HUMI_PARSED),
    ('read_run_prgm_mon', (), 'RUN PRGM MON?', '1,25.0,50,1:30,3',
     {'pgmstep': 1, 'temperature': 25.0, 'humidity': 50.0, 'time': {'hours': 1, 'minutes': 30}, 'counter': 3}),
    ('read_ip_set', (), 'IPSET?', '192.168.0.10,255.255.255.0,192.168.0.1',
     {'address': '192.168.0.10', 'mask': '255.255.255.0', 'gateway': '192.168.0.1'}),
]

TIME_0830 = {'hour': 8, 'minute': 30}
TIME_1745 = {'hour': 17, 'minute': 45}
DATE = {'year': 2026, 'month': 11, 'day': 13}

# method, arguments, keyword arguments, commands sent
WRITES = [
    ('write_date', (2026, 11, 13, 'FRI'), {}, ['DATE,26.11/13. FRI']),
    ('write_time', (14, 35, 59), {}, ['TIME,14:35:59']),
    ('write_mask', (), {}, ['MASK,00000000']),
    ('write_mask', (), {'alarm': True}, ['MASK,01000000']),
    ('write_mask', (), {'single_step_done': True}, ['MASK,00100000']),
    ('write_mask', (), {'state_change': True}, ['MASK,00010000']),
    ('write_mask', (), {'gpib': True}, ['MASK,00000010']),
    ('write_mask', (True, True, True, True), {}, ['MASK,01110010']),
    ('write_srq', (), {}, ['SRQ,RESET']),
    ('write_timer_quick', ('STANDBY', TIME_0830), {}, ['TIMER WRITE,NO0,8:30,STANDBY']),
    ('write_timer_quick', ('RUN', TIME_0830), {'pgmnum': 3, 'pgmstep': 2},
     ['TIMER WRITE,NO0,8:30,RUN,RAM:3,STEP2']),
    ('write_timer_quick', ('RUN', TIME_0830, 41, 1), {}, ['TIMER WRITE,NO0,8:30,RUN,ROM:41,STEP1']),
    ('write_timer_start', ('once', TIME_0830, 'CONSTANT'), {'date': DATE},
     ['TIMER WRITE,NO1,MODE1,26.11/13,8:30,CONSTANT']),
    ('write_timer_start', ('weekly', TIME_0830, 'RUN'), {'days': ['MON', 'WED'], 'pgmnum': 3, 'pgmstep': 2},
     ['TIMER WRITE,NO1,MODE2,MON/WED,8:30,RUN,RAM:3,STEP2']),
    ('write_timer_start', ('daily', TIME_0830, 'CONSTANT'), {}, ['TIMER WRITE,NO1,MODE3,8:30,CONSTANT']),
    ('write_timer_start', ('daily', TIME_0830, 'RUN'), {'pgmnum': 41, 'pgmstep': 1},
     ['TIMER WRITE,NO1,MODE3,8:30,RUN,ROM:41,STEP1']),
    ('write_timer_stop', ('once', TIME_1745, 'OFF'), {'date': DATE},
     ['TIMER WRITE,NO2,MODE1,26.11/13,17:45,OFF']),
    ('write_timer_stop', ('weekly', TIME_1745, 'STANDBY'), {'days': ['MON', 'WED']},
     ['TIMER WRITE,NO2,MODE2,MON/WED,17:45,STANDBY']),
    ('write_timer_stop', ('daily', TIME_1745, 'STANDBY'), {}, ['TIMER WRITE,NO2,MODE3,17:45,STANDBY']),
    ('write_timer_erase', ('quick',), {}, ['TIMER ERASE,NO0']),
    ('write_timer_erase', ('start',), {}, ['TIMER ERASE,NO1']),
    ('write_timer_erase', ('stop',), {}, ['TIMER ERASE,NO2']),
    ('write_timer', ('quick', True), {}, ['TIMER,ON,0']),
    ('write_timer', ('start', True), {}, ['TIMER,ON,1']),
    ('write_timer', ('stop', False), {}, ['TIMER,OFF,2']),
    ('write_keyprotect', (True,), {}, ['KEYPROTECT,ON']),
    ('write_power', (True,), {}, ['POWER,ON']),
    ('write_temp', (), {'setpoint': 25}, ['TEMP, S25.0']),
    ('write_temp', (), {'setpoint': 0}, ['TEMP, S0.0']),
    ('write_temp', (), {'setpoint': -40.5}, ['TEMP, S-40.5']),
    ('write_temp', (), {'setpoint': 25, 'enable': True}, ['TEMP, S25.0']),
    ('write_temp', (), {'min': -40, 'max': 100}, ['TEMP, L-40.0', 'TEMP, H100.0']),
    ('write_temp', (), {'setpoint': 25, 'max': 100}, ['TEMP, S25.0', 'TEMP, H100.0']),
    ('write_temp', (), {'setpoint': 25, 'max': 100, 'min': -40}, ['TEMP, S25.0 H100.0 L-40.0']),
    ('write_temp', (), {}, []),
    ('write_humi', (), {'setpoint': 50}, ['HUMI, S50.0']),
    ('write_humi', (), {'setpoint': 50, 'enable': True}, ['HUMI, S50.0']),
    ('write_humi', (), {'enable': False}, ['HUMI,SOFF']),
    ('write_humi', (), {'setpoint': 50, 'enable': False}, ['HUMI,SOFF']),
    ('write_humi', (), {'min': 10, 'max': 95}, ['HUMI, L10.0', 'HUMI, H95.0']),
    ('write_humi', (), {'setpoint': 50, 'min': 10}, ['HUMI, S50.0', 'HUMI, L10.0']),
    ('write_humi', (), {'setpoint': 50, 'max': 95, 'min': 10}, ['HUMI, S50.0 H95.0 L10.0']),
    ('write_humi', (), {'enable': False, 'max': 95, 'min': 10}, ['HUMI,SOFF H95.0 L10.0']),
    ('write_set', ('off',), {}, ['SET,REF0']),
    ('write_set', ('Off',), {}, ['SET,REF0']),
    ('write_set', ('auto',), {}, ['SET,REF9']),
    ('write_set', ('Auto', 50), {}, ['SET,REF9']),
    ('write_set', ('manual',), {}, ['SET,REF0']),
    ('write_set', ('manual', 20), {}, ['SET,REF1']),
    ('write_set', ('Manual', 50), {}, ['SET,REF3']),
    ('write_set', (), {'mode': 'manual', 'setpoint': 100}, ['SET,REF6']),
    ('write_relay', ([True, None, False] + [None] * 9,), {}, ['RELAY,ON,1', 'RELAY,OFF,3']),
    ('write_relay', ([None, True, None, None, True],), {}, ['RELAY,ON,2,5']),
    ('write_relay', ([False] * 12,), {}, ['RELAY,OFF,1,2,3,4,5,6,7,8,9,10,11,12']),
    ('write_relay', ([None] * 12,), {}, []),
    ('write_prgm_run', (3, 1), {}, ['PRGM,RUN,RAM:3,STEP1']),
    ('write_prgm_run', (40, 12), {}, ['PRGM,RUN,RAM:40,STEP12']),
    ('write_prgm_run', (41, 2), {}, ['PRGM,RUN,ROM:41,STEP2']),
    ('write_prgm_pause', (), {}, ['PRGM,PAUSE']),
    ('write_prgm_continue', (), {}, ['PRGM,CONTINUE']),
    ('write_prgm_advance', (), {}, ['PRGM,ADVANCE']),
    ('write_prgm_end', (), {}, ['PRGM,END,STANDBY']),
    ('write_prgm_end', ('HOLD',), {}, ['PRGM,END,HOLD']),
    ('write_prgm_end', ('CONST',), {}, ['PRGM,END,CONST']),
    ('write_prgm_end', ('OFF',), {}, ['PRGM,END,OFF']),
    ('write_mode_off', (), {}, ['MODE,OFF']),
    ('write_mode_standby', (), {}, ['MODE,STANDBY']),
    ('write_mode_constant', (), {}, ['MODE,CONSTANT']),
    ('write_mode_run', (3,), {}, ['MODE,RUN3']),
    ('write_prgm_data_edit', (3, 'START'), {}, ['PRGM DATA WRITE, PGM3, EDIT START']),
    ('write_prgm_data_edit', (3, 'END'), {}, ['PRGM DATA WRITE, PGM3, EDIT END']),
    ('write_prgm_data_edit', (3, 'CANCEL'), {}, ['PRGM DATA WRITE, PGM3, EDIT CANCEL']),
    ('write_prgm_data_edit', (3, 'START', True), {}, ['PRGM DATA WRITE, PGM3, OVER WRITE START']),
    ('write_prgm_erase', (3,), {}, ['PRGM ERASE,RAM:3']),
    ('write_prgm_erase', (41,), {}, ['PRGM ERASE,ROM:41']),
    ('write_run_prgm', (25, 1, 30), {}, ['RUN PRGM, TEMP25.0 TIME1:30']),
    ('write_run_prgm', (25, 1, 30), {'gotemp': -40}, ['RUN PRGM, TEMP25.0 TIME1:30 GOTEMP-40.0']),
    ('write_run_prgm', (25, 1, 30), {'humi': 50, 'gohumi': 60}, ['RUN PRGM, TEMP25.0 TIME1:30 HUMI50 GOHUMI60']),
    ('write_run_prgm', (25, 1, 30, 50, 50, 60, [True, True, False] + [None] * 9), {},
     ['RUN PRGM, TEMP25.0 TIME1:30 GOTEMP50.0 HUMI50 GOHUMI60 RELAYON,1,2 RELAYOFF,3']),
    ('write_run_prgm', (25, 1, 30), {'relays': [None] * 12}, ['RUN PRGM, TEMP25.0 TIME1:30']),
    ('write_temp_ptc', (True, 5, -5), {}, ['TEMP PTC, PTCON, DEVP5.0, DEVN-5.0']),
    ('write_temp_ptc', (), {'enable': False, 'positive': 10, 'negative': -2.5},
     ['TEMP PTC, PTCOFF, DEVP10.0, DEVN-2.5']),
    ('write_ptc', ({'max': 180, 'min': -70}, 1.5, 2.5, 3.5), {}, ['PTC,180.0,-70.0,1.5,2.5,3.5,0.0,0.0']),
    ('write_ptc', ({'max': 180, 'min': -70}, 1.5, 2.5, 3.5), {'opt1': 4, 'opt2': 5},
     ['PTC,180.0,-70.0,1.5,2.5,3.5,4.0,5.0']),
    ('write_ip_set', ('192.168.0.10', '255.255.255.0', '192.168.0.1'), {},
     ['IPSET,192.168.0.10,255.255.255.0,192.168.0.1']),
]

COUNTER_A = {'start': 1, 'end': 3, 'cycles': 2}
COUNTER_B = {'start': 2, 'end': 4, 'cycles': 1}
COUNTER_UNUSED = {'start': 0, 'end': 0, 'cycles': 0}
PGM3 = 'PRGM DATA WRITE, PGM3, '

# program details, commands sent
PRGM_DETAILS = [
    ({}, []),
    ({'counter_a': COUNTER_A}, ['PRGM DATA WRITE,PGM3,COUNT,A(1.3.2)']),
    ({'counter_a': COUNTER_A, 'counter_b': COUNTER_UNUSED}, ['PRGM DATA WRITE,PGM3,COUNT,A(1.3.2)']),
    ({'counter_a': COUNTER_A, 'counter_b': COUNTER_B}, ['PRGM DATA WRITE,PGM3,COUNT,A(1.3.2),B(2.4.1)']),
    ({'counter_a': COUNTER_UNUSED, 'counter_b': COUNTER_B}, ['PRGM DATA WRITE,PGM3,COUNT,B(2.4.1)']),
    ({'counter_b': COUNTER_B}, ['PRGM DATA WRITE,PGM3,COUNT,B(2.4.1)']),
    ({'counter_a': COUNTER_UNUSED, 'counter_b': COUNTER_UNUSED}, []),
    ({'name': 'MYPROG'}, [PGM3 + 'NAME,MYPROG']),
    ({'end': 'STANDBY'}, [PGM3 + 'END,STANDBY']),
    ({'end': 'HOLD', 'next_prgm': 0}, [PGM3 + 'END,HOLD']),
    ({'end': 'RUN', 'next_prgm': 7}, [PGM3 + 'END,RUN,PTN7']),
    ({'tempDetail': {'range': {'max': 180, 'min': -70}, 'mode': 'SV', 'setpoint': 25}},
     [PGM3 + 'HTEMP,180.0', PGM3 + 'LTEMP,-70.0', PGM3 + 'PRE MODE, TEMP,SV', PGM3 + 'PRE TSV,25.0']),
    ({'tempDetail': {'mode': 'OFF', 'setpoint': None}}, [PGM3 + 'PRE MODE, TEMP,OFF']),
    ({'tempDetail': {'range': {'max': 100.5, 'min': -40.5}}}, [PGM3 + 'HTEMP,100.5', PGM3 + 'LTEMP,-40.5']),
    ({'humiDetail': {'range': {'max': 100, 'min': 0}, 'mode': 'SV', 'setpoint': 50}},
     [PGM3 + 'HHUMI,100', PGM3 + 'LHUMI,0', PGM3 + 'PRE MODE, HUMI,SV', PGM3 + 'PRE HSV,50']),
    ({'humiDetail': {'mode': 'OFF', 'setpoint': None}}, [PGM3 + 'PRE MODE, HUMI,OFF']),
    ({'humiDetail': {'range': {'max': 95, 'min': 10}}}, [PGM3 + 'HHUMI,95', PGM3 + 'LHUMI,10']),
    ({'counter_a': COUNTER_A, 'name': 'MYPROG', 'end': 'OFF', 'steps': []},
     ['PRGM DATA WRITE,PGM3,COUNT,A(1.3.2)', PGM3 + 'NAME,MYPROG', PGM3 + 'END,OFF']),
]

# program step, command sent
PRGM_STEPS = [
    ({'number': 1}, 'STEP1'),
    ({'number': 12, 'time': {'hour': 10, 'minute': 15}}, 'STEP12,TIME10:15'),
    ({'number': 1, 'paused': True}, 'STEP1,PAUSE ON'),
    ({'number': 1, 'paused': False}, 'STEP1,PAUSE OFF'),
    ({'number': 1, 'refrig': {'mode': 'manual', 'setpoint': 50}}, 'STEP1,REF3'),
    ({'number': 1, 'granty': True}, 'STEP1,GRANTY ON'),
    ({'number': 1, 'granty': False}, 'STEP1,GRANTY OFF'),
    ({'number': 1, 'temperature': {'setpoint': -40}}, 'STEP1,TEMP-40.0'),
    ({'number': 1, 'temperature': {'ramp': True}}, 'STEP1,TRAMPON'),
    ({'number': 1, 'temperature': {'setpoint': 25, 'ramp': False}}, 'STEP1,TEMP25.0,TRAMPOFF'),
    ({'number': 1, 'temperature': {'setpoint': 25, 'ramp': False, 'enable_cascade': True,
                                   'deviation': {'positive': 5, 'negative': -5}}},
     'STEP1,TEMP25.0,TRAMPOFF,PTCON,DEVP5.0,DEVN-5.0'),
    ({'number': 1, 'temperature': {'enable_cascade': False}}, 'STEP1,PTCOFF'),
    ({'number': 1, 'humidity': {'setpoint': 85, 'enable': True, 'ramp': True}}, 'STEP1,HUMI85,HRAMPON'),
    ({'number': 1, 'humidity': {'setpoint': 85, 'enable': True, 'ramp': False}}, 'STEP1,HUMI85,HRAMPOFF'),
    ({'number': 1, 'humidity': {'setpoint': 85, 'enable': True}}, 'STEP1,HUMI85'),
    ({'number': 1, 'humidity': {'setpoint': 0.0, 'enable': False, 'ramp': False}}, 'STEP1,HUMIOFF'),
    ({'number': 1, 'relay': [True, False] + [None] * 10}, 'STEP1,RELAY ON1,RELAY OFF2'),
    ({'number': 1, 'relay': relays(1, 5)}, 'STEP1,RELAY ON1.5,RELAY OFF2.3.4.6.7.8.9.10.11.12'),
    ({'number': 1, 'relay': [None] * 12}, 'STEP1'),
    ({'number': 2, 'time': {'hour': 1, 'minute': 30}, 'paused': False, 'refrig': {'mode': 'auto', 'setpoint': 0},
      'granty': False, 'temperature': {'setpoint': 25, 'ramp': False}},
     'STEP2,TIME1:30,PAUSE OFF,REF9,GRANTY OFF,TEMP25.0,TRAMPOFF'),
]


def read_ids(table):
    return ['%s <- %s' % (row[0], row[3]) for row in table]


def write_ids(table):
    return ['%s -> %s' % (row[0], ' + '.join(row[3]) or 'nothing') for row in table]


@pytest.fixture
def p300(espec_link):
    return P300('TCP', host='chamber')


class TestConnection(object):
    def test_tcp_parameters(self, espec_link):
        P300('TCP', host='chamber', port=10001, timeout=5, address=2)
        assert espec_link.created_with == [{'host': 'chamber', 'address': 2, 'port': 10001, 'timeout': 5}]

    def test_serial_parameters(self, espec_link):
        P300('Serial', serialport='/dev/ttyUSB0', baudrate=19200, address=3)
        assert espec_link.created_with == [{'port': '/dev/ttyUSB0', 'baud': 19200, 'address': 3}]

    def test_interact_passes_the_command_through(self, p300, espec_link):
        espec_link.answers['MODE?'] = 'STANDBY'
        assert p300.interact('MODE?') == 'STANDBY'
        assert espec_link.commands == ['MODE?']

    def test_error_of_the_controller_is_passed_on(self, p300, espec_link):
        espec_link.answers['TEMP?'] = EspecError('The chamber did not respond in time')
        with pytest.raises(EspecError):
            p300.read_temp()

    def test_close(self, p300, espec_link):
        p300.close()
        assert espec_link.closed

    def test_delete_closes_the_connection(self, p300, espec_link):
        p300.__del__()
        assert espec_link.closed

    def test_close_without_connection(self):
        P300.__new__(P300).close()

    @pytest.mark.parametrize('number, kind', [(1, 'RAM'), (40, 'RAM'), (41, 'ROM')])
    def test_rom_pgm(self, p300, number, kind):
        assert p300.rom_pgm(number) == kind

    @pytest.mark.parametrize('value, expected', [('1.5', 1.5), (' 85', 85.0), ('OFF', -1), (None, -1)])
    def test_tryfloat(self, value, expected):
        assert tryfloat(value, -1) == expected


class TestOverTcpSocket(object):
    '''P300 on top of the real EspecTCP class, only the socket is a fake.'''

    def test_connection_parameters(self, fake_socket):
        P300('TCP', host='chamber', port=10001, timeout=5)
        assert fake_socket.address == ('chamber', 10001)
        assert fake_socket.timeout == 5

    def test_connection_defaults(self, fake_socket):
        P300('TCP', host='chamber')
        assert fake_socket.address == ('chamber', 57732)
        assert fake_socket.timeout == 3

    def test_monitoring(self, fake_socket):
        fake_socket.reply(
            b'23.4,,CONSTANT,1\r\n', b'CONSTANT\r\n', b'23.4,25.0,180.0,-70.0\r\n', b'25.0,ON\r\n',
            b'1,12.5\r\n', b'1,12\r\n'
        )
        chamber = P300('TCP', host='chamber')
        assert chamber.read_mon() == {'temperature': 23.4, 'mode': 'CONSTANT', 'alarms': 1}
        assert chamber.read_mode() == 'CONSTANT'
        assert chamber.read_temp() == {
            'processvalue': 23.4, 'setpoint': 25.0, 'enable': True, 'range': {'max': 180.0, 'min': -70.0}
        }
        assert chamber.read_constant_temp() == {'setpoint': 25.0, 'enable': True}
        assert chamber.read_htr() == {'dry': 12.5}
        assert chamber.read_alarm() == [12]
        assert fake_socket.sent == [
            b'MON?\r\n', b'MODE?\r\n', b'TEMP?\r\n', b'CONSTANT SET?,TEMP\r\n', b'%?\r\n', b'ALARM?\r\n'
        ]

    def test_control(self, fake_socket):
        fake_socket.reply(b'OK:TEMP,S40.0\r\n', b'OK:MODE,CONSTANT\r\n', b'OK:MODE,STANDBY\r\n')
        chamber = P300('TCP', host='chamber')
        chamber.write_temp(setpoint=40)
        chamber.write_mode_constant()
        chamber.write_mode_standby()
        assert fake_socket.sent == [b'TEMP, S40.0\r\n', b'MODE,CONSTANT\r\n', b'MODE,STANDBY\r\n']

    def test_rejected_setpoint_raises_especerror(self, fake_socket):
        fake_socket.reply(b'NA:DATA OUT OF RANGE\r\n')
        with pytest.raises(EspecError) as error:
            P300('TCP', host='chamber').write_temp(setpoint=500)
        assert 'DATA OUT OF RANGE' in str(error.value)

    def test_close(self, fake_socket):
        P300('TCP', host='chamber').close()
        assert fake_socket.closed


class TestOverSerialPort(object):
    '''P300 on top of the real EspecSerial class, only the serial port is a fake.'''

    def test_connection_parameters(self, fake_serial):
        P300('Serial', serialport='/dev/ttyUSB0', baudrate=19200)
        assert fake_serial.kwargs['port'] == '/dev/ttyUSB0'
        assert fake_serial.kwargs['baudrate'] == 19200

    def test_commands_carry_the_rs485_address(self, fake_serial):
        fake_serial.reply(b'STANDBY\r\n')
        chamber = P300('Serial', serialport='/dev/ttyUSB0', baudrate=19200, address=3)
        assert chamber.read_mode() == 'STANDBY'
        assert fake_serial.sent == [b'3,MODE?\r\n']

    @pytest.mark.xfail(strict=True, reason='P300 hands baud=None to EspecSerial when no baudrate is given, '
                                           'which overrides the default of 9600')
    def test_baudrate_default(self, fake_serial):
        P300('Serial', serialport='/dev/ttyUSB0')
        assert fake_serial.kwargs['baudrate'] == 9600


class TestRead(object):
    @pytest.mark.parametrize('method, args, command, answer, expected', READS, ids=read_ids(READS))
    def test_read(self, p300, espec_link, method, args, command, answer, expected):
        espec_link.answers[command] = answer
        assert getattr(p300, method)(*args) == expected
        assert espec_link.commands == [command]

    def test_read_humi_without_humidity_raises_especerror(self, p300):
        with pytest.raises(EspecError):
            p300.read_humi()

    def test_read_system_set_rejects_unknown_argument(self, p300, espec_link):
        with pytest.raises(ValueError):
            p300.read_system_set('TEMP')
        assert espec_link.commands == []

    @pytest.mark.xfail(strict=True, reason='read_system_set() lists "PTC" twice instead of "PTS" and '
                                           'rejects the documented argument "PTS"')
    def test_read_system_set_pts(self, p300, espec_link):
        espec_link.answers['SYSTEM SET?,PTS'] = 'ON'
        assert p300.read_system_set('PTS') == 'ON'

    @pytest.mark.xfail(strict=True, reason='the pattern of read_timer_list_stop() does not allow "/" between '
                                           'the days (read_timer_list_start() does), several days end in '
                                           'AttributeError')
    def test_read_timer_list_stop_with_several_days(self, p300, espec_link):
        espec_link.answers['TIMER LIST?,2'] = '2,MODE2,MON/WED,17:45,STANDBY'
        assert p300.read_timer_list_stop() == {
            'repeat': 'weekly', 'time': {'hour': 17, 'minute': 45}, 'mode': 'STANDBY', 'days': ['MON', 'WED']
        }

    @pytest.mark.xfail(strict=True, reason='read_prgm_use() returns 40 booleans, not the numbers of the '
                                           'programs as documented')
    def test_read_prgm_use(self, p300, espec_link):
        espec_link.answers['PRGM USE?,RAM'] = '3,1,5,40'
        assert p300.read_prgm_use() == [1, 5, 40]

    @pytest.mark.xfail(strict=True, reason='read_run_prgm_mon() names the key "minuets" instead of "minutes" '
                                           'on chambers without humidity')
    def test_read_run_prgm_mon_without_humidity(self, p300, espec_link):
        espec_link.answers['RUN PRGM MON?'] = '1,25.0,1:30,3'
        assert p300.read_run_prgm_mon() == {
            'pgmstep': 1, 'temperature': 25.0, 'time': {'hours': 1, 'minutes': 30}, 'counter': 3
        }

    @pytest.mark.xfail(strict=True, reason='read_run_prgm() builds the result but has no return statement')
    @pytest.mark.parametrize('answer, expected', [
        ('TEMP25.0 GOTEMP-40.0 TIME1:30 REF9',
         {'temperature': {'start': 25.0, 'end': -40.0}, 'time': {'hours': 1, 'minutes': 30},
          'refrig': REF_AUTO, 'relay': relays()}),
        ('TEMP25.0 GOTEMP50.0 HUMI50 GOHUMI60 TIME1:30 REF3 RELAYON,1,5',
         {'temperature': {'start': 25.0, 'end': 50.0}, 'humidity': {'start': 50.0, 'end': 60.0},
          'time': {'hours': 1, 'minutes': 30}, 'refrig': {'mode': 'manual', 'setpoint': 50},
          'relay': relays(1, 5)}),
    ], ids=['temperature', 'humidity and relays'])
    def test_read_run_prgm(self, p300, espec_link, answer, expected):
        espec_link.answers['RUN PRGM?'] = answer
        assert p300.read_run_prgm() == expected


class TestWrite(object):
    @pytest.mark.parametrize('method, args, kwargs, commands', WRITES, ids=write_ids(WRITES))
    def test_write(self, p300, espec_link, method, args, kwargs, commands):
        espec_link.default = 'OK'
        assert getattr(p300, method)(*args, **kwargs) is None
        assert espec_link.commands == commands

    @pytest.mark.parametrize('method, command', [('write_keyprotect', 'KEYPROTECT,OFF'), ('write_power', 'POWER,OFF')])
    def test_switch_off(self, p300, espec_link, method, command):
        # the library sends "off" in lower case, whether the controller cares is not known
        espec_link.default = 'OK'
        getattr(p300, method)(False)
        assert [sent.upper() for sent in espec_link.commands] == [command]

    def test_error_of_the_controller_is_passed_on(self, p300, espec_link):
        with pytest.raises(EspecError):
            p300.write_temp(setpoint=500)
        assert espec_link.commands == ['TEMP, S500.0']

    @pytest.mark.parametrize('method, args', [
        ('write_set', ('manual', 30)),
        ('write_set', ('turbo',)),
        ('write_prgm_end', ('CONSTANT',)),
    ])
    def test_invalid_argument_raises_valueerror(self, p300, espec_link, method, args):
        espec_link.default = 'OK'
        with pytest.raises(ValueError):
            getattr(p300, method)(*args)
        assert espec_link.commands == []

    @pytest.mark.xfail(strict=True, reason='write_temp() ignores the documented argument "range"')
    def test_write_temp_range(self, p300, espec_link):
        espec_link.default = 'OK'
        p300.write_temp(range={'max': 100, 'min': -40})
        assert sorted(espec_link.commands) == ['TEMP, H100.0', 'TEMP, L-40.0']

    @pytest.mark.xfail(strict=True, reason='write_humi() ignores the documented argument "range"')
    def test_write_humi_range(self, p300, espec_link):
        espec_link.default = 'OK'
        p300.write_humi(range={'max': 95, 'min': 10})
        assert sorted(espec_link.commands) == ['HUMI, H95.0', 'HUMI, L10.0']

    @pytest.mark.xfail(strict=True, reason='write_ptc() documents the arguments range, p, i and filter (the '
                                           'keys of read_ptc()) but is defined with op_range, pid_p, pid_i '
                                           'and pid_filter')
    def test_write_ptc_with_documented_argument_names(self, p300, espec_link):
        espec_link.default = 'OK'
        p300.write_ptc(range={'max': 180, 'min': -70}, p=1.5, i=3.5, filter=2.5)
        assert espec_link.commands == ['PTC,180.0,-70.0,1.5,2.5,3.5,0.0,0.0']


class TestProgram(object):
    @pytest.mark.parametrize('details, commands', PRGM_DETAILS)
    def test_write_prgm_data_details(self, p300, espec_link, details, commands):
        espec_link.default = 'OK'
        p300.write_prgm_data_details(3, **copy.deepcopy(details))
        assert espec_link.commands == commands

    @pytest.mark.parametrize('step, command', PRGM_STEPS, ids=[row[1] for row in PRGM_STEPS])
    def test_write_prgm_data_step(self, p300, espec_link, step, command):
        espec_link.default = 'OK'
        p300.write_prgm_data_step(3, **copy.deepcopy(step))
        assert espec_link.commands == [PGM3 + command]

    def test_write_prgm_data_step_rejects_unknown_refrig_setting(self, p300, espec_link):
        espec_link.default = 'OK'
        with pytest.raises(ValueError):
            p300.write_prgm_data_step(3, number=1, refrig={'mode': 'manual', 'setpoint': 75})
        assert espec_link.commands == []

    def test_read_prgm(self, p300, espec_link):
        espec_link.answers.update({
            'PRGM DATA?,RAM:3': PRGM_DATA,
            'PRGM DATA?,RAM:3,DETAIL': DETAIL_HUMI,
            'PRGM DATA?,RAM:3,STEP1': STEP_TEMP,
            'PRGM DATA?,RAM:3,STEP2': STEP_HUMI,
        })
        expected = dict(PRGM_DATA_PARSED, steps=[STEP_TEMP_PARSED, STEP_HUMI_PARSED], **DETAIL_HUMI_PARSED)
        assert p300.read_prgm(3) == expected
        assert espec_link.commands == [
            'PRGM DATA?,RAM:3', 'PRGM DATA?,RAM:3,DETAIL', 'PRGM DATA?,RAM:3,STEP1', 'PRGM DATA?,RAM:3,STEP2'
        ]

    def test_read_prgm_with_ptc(self, p300, espec_link):
        espec_link.answers.update({
            'PRGM DATA PTC?,RAM:40': '1,<MYPROG>,COUNT,A(0.0.0),B(0.0.0),END(OFF)',
            'PRGM DATA PTC?,RAM:40,DETAIL': DETAIL_TEMP,
            'PRGM DATA PTC?,RAM:40,STEP1': STEP_PTC,
        })
        assert p300.read_prgm(40, with_ptc=True) == {
            'steps': [STEP_PTC_PARSED],
            'name': 'MYPROG',
            'end': 'OFF',
            'next_prgm': 0,
            'counter_a': COUNTER_UNUSED,
            'counter_b': COUNTER_UNUSED,
            'tempDetail': DETAIL_TEMP_PARSED['tempDetail'],
        }
        assert espec_link.commands == [
            'PRGM DATA PTC?,RAM:40', 'PRGM DATA PTC?,RAM:40,DETAIL', 'PRGM DATA PTC?,RAM:40,STEP1'
        ]

    def test_read_prgm_0_is_an_empty_program(self, p300, espec_link):
        espec_link.answers['TEMP?'] = '23.4,25.0,180.0,-70.0'
        assert p300.read_prgm(0) == {
            'counter_a': COUNTER_UNUSED,
            'counter_b': COUNTER_UNUSED,
            'end': 'OFF',
            'name': '',
            'next_prgm': 0,
            'tempDetail': {'mode': 'OFF', 'setpoint': None, 'range': {'max': 180.0, 'min': -70.0}},
            'steps': [{
                'granty': False,
                'number': 1,
                'paused': False,
                'refrig': REF_AUTO,
                'time': {'hour': 1, 'minute': 0},
                'relay': relays(),
                'temperature': {'ramp': False, 'setpoint': 0.0},
            }],
        }

    def test_read_prgm_0_with_humidity(self, p300, espec_link):
        espec_link.answers.update({'TEMP?': '23.4,25.0,180.0,-70.0', 'HUMI?': '45,50,95,10'})
        program = p300.read_prgm(0)
        assert program['humiDetail'] == {'mode': 'OFF', 'setpoint': None, 'range': {'max': 95.0, 'min': 10.0}}
        assert program['steps'][0]['humidity'] == {'enable': False, 'ramp': False, 'setpoint': 0.0}

    def test_read_prgm_0_with_ptc(self, p300, espec_link):
        espec_link.answers.update({
            'TEMP?': '23.4,25.0,180.0,-70.0', 'TEMP PTC?': 'ON,25.0,24.5,26.0,25.5,5.0,-5.0'
        })
        assert p300.read_prgm(0, with_ptc=True)['steps'][0]['temperature'] == {
            'ramp': False, 'setpoint': 0.0, 'deviation': {'positive': 5.0, 'negative': -5.0},
            'enable_cascade': False
        }

    @pytest.mark.parametrize('number', [-1, 41])
    def test_read_prgm_rejects_other_numbers(self, p300, espec_link, number):
        with pytest.raises(ValueError):
            p300.read_prgm(number)
        assert espec_link.commands == []

    def test_write_prgm(self, p300, espec_link):
        espec_link.default = 'OK'
        program = dict(
            PRGM_DATA_PARSED,
            steps=copy.deepcopy([STEP_TEMP_PARSED, STEP_HUMI_PARSED]),
            **copy.deepcopy(DETAIL_HUMI_PARSED)
        )
        p300.write_prgm(4, program)
        assert espec_link.commands == [
            'PRGM DATA WRITE, PGM4, EDIT START',
            STEP_TEMP_WRITTEN,
            STEP_HUMI_WRITTEN,
            'PRGM DATA WRITE,PGM4,COUNT,A(1.2.3)',
            'PRGM DATA WRITE, PGM4, NAME,MYPROG',
            'PRGM DATA WRITE, PGM4, END,STANDBY',
            'PRGM DATA WRITE, PGM4, HTEMP,180.0',
            'PRGM DATA WRITE, PGM4, LTEMP,-70.0',
            'PRGM DATA WRITE, PGM4, PRE MODE, TEMP,OFF',
            'PRGM DATA WRITE, PGM4, HHUMI,100',
            'PRGM DATA WRITE, PGM4, LHUMI,0',
            'PRGM DATA WRITE, PGM4, PRE MODE, HUMI,OFF',
            'PRGM DATA WRITE, PGM4, EDIT END',
        ]

    def test_write_prgm_numbers_the_steps(self, p300, espec_link):
        espec_link.default = 'OK'
        steps = [{'number': 7, 'granty': False}, {'number': 7, 'granty': True}, {'granty': False}]
        p300.write_prgm(40, {'steps': steps})
        assert espec_link.commands == [
            'PRGM DATA WRITE, PGM40, EDIT START',
            'PRGM DATA WRITE, PGM40, STEP1,GRANTY OFF',
            'PRGM DATA WRITE, PGM40, STEP2,GRANTY ON',
            'PRGM DATA WRITE, PGM40, STEP3,GRANTY OFF',
            'PRGM DATA WRITE, PGM40, EDIT END',
        ]

    def test_write_prgm_without_humidity_step_skips_the_humidity_range(self, p300, espec_link):
        espec_link.default = 'OK'
        program = {
            'steps': copy.deepcopy([STEP_HUMI_OFF_PARSED]),
            'humiDetail': {'range': {'max': 100.0, 'min': 0.0}, 'mode': 'OFF', 'setpoint': None},
        }
        p300.write_prgm(4, program)
        assert espec_link.commands == [
            'PRGM DATA WRITE, PGM4, EDIT START',
            'PRGM DATA WRITE, PGM4, STEP1,TIME2:15,PAUSE OFF,REF0,GRANTY OFF,TEMP-40.0,TRAMPOFF,HUMIOFF'
            ',RELAY OFF1.2.3.4.5.6.7.8.9.10.11.12',
            'PRGM DATA WRITE, PGM4, PRE MODE, HUMI,OFF',
            'PRGM DATA WRITE, PGM4, EDIT END',
        ]

    def test_write_prgm_cancels_the_edit_on_an_error(self, p300, espec_link):
        espec_link.default = 'OK'
        espec_link.answers['PRGM DATA WRITE, PGM4, STEP2,GRANTY ON'] = EspecError('DATA OUT OF RANGE')
        with pytest.raises(EspecError):
            p300.write_prgm(4, {'steps': [{'granty': False}, {'granty': True}, {'granty': False}]})
        assert espec_link.commands == [
            'PRGM DATA WRITE, PGM4, EDIT START',
            'PRGM DATA WRITE, PGM4, STEP1,GRANTY OFF',
            'PRGM DATA WRITE, PGM4, STEP2,GRANTY ON',
            'PRGM DATA WRITE, PGM4, EDIT CANCEL',
        ]

    def test_write_prgm_rejects_rom_programs(self, p300, espec_link):
        espec_link.default = 'OK'
        with pytest.raises(ValueError):
            p300.write_prgm(41, {'steps': []})
        assert espec_link.commands == []

    def test_program_can_be_read_and_written_back(self, p300, espec_link):
        espec_link.default = 'OK'
        espec_link.answers.update({
            'PRGM DATA?,RAM:3': PRGM_DATA,
            'PRGM DATA?,RAM:3,DETAIL': DETAIL_TEMP,
            'PRGM DATA?,RAM:3,STEP1': STEP_TEMP,
            'PRGM DATA?,RAM:3,STEP2': STEP_HUMI,
        })
        p300.write_prgm(4, p300.read_prgm(3))
        assert espec_link.commands[4:] == [
            'PRGM DATA WRITE, PGM4, EDIT START',
            STEP_TEMP_WRITTEN,
            STEP_HUMI_WRITTEN,
            'PRGM DATA WRITE,PGM4,COUNT,A(1.2.3)',
            'PRGM DATA WRITE, PGM4, NAME,MYPROG',
            'PRGM DATA WRITE, PGM4, END,STANDBY',
            'PRGM DATA WRITE, PGM4, HTEMP,180.0',
            'PRGM DATA WRITE, PGM4, LTEMP,-70.0',
            'PRGM DATA WRITE, PGM4, PRE MODE, TEMP,OFF',
            'PRGM DATA WRITE, PGM4, EDIT END',
        ]

    @pytest.mark.xfail(strict=True, reason='parse_prgm_data_detail() returns the start setpoints as str, '
                                           'documented is float')
    def test_read_prgm_data_detail_with_start_setpoints(self, p300, espec_link):
        espec_link.answers['PRGM DATA?,RAM:3,DETAIL'] = DETAIL_SETPOINTS
        assert p300.read_prgm_data_detail(3) == {
            'tempDetail': {'range': {'max': 180.0, 'min': -70.0}, 'mode': 'SV', 'setpoint': 25.0},
            'humiDetail': {'range': {'max': 100.0, 'min': 0.0}, 'mode': 'SV', 'setpoint': 50.0},
        }

    @pytest.mark.xfail(strict=True, reason='a program with a start setpoint cannot be written back: read_prgm() '
                                           'returns the setpoint as str, write_prgm_data_details() formats it '
                                           'with %f (TypeError)')
    def test_program_with_start_setpoint_can_be_read_and_written_back(self, p300, espec_link):
        espec_link.default = 'OK'
        espec_link.answers.update({
            'PRGM DATA?,RAM:3': '1,<MYPROG>,COUNT,A(0.0.0),B(0.0.0),END(STANDBY)',
            'PRGM DATA?,RAM:3,DETAIL': '180.0,-70.0,TEMPSV,25.0',
            'PRGM DATA?,RAM:3,STEP1': STEP_TEMP,
        })
        p300.write_prgm(4, p300.read_prgm(3))
        assert 'PRGM DATA WRITE, PGM4, PRE TSV,25.0' in espec_link.commands
        assert espec_link.commands[-1] == 'PRGM DATA WRITE, PGM4, EDIT END'
