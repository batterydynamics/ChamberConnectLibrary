'''
Every module of the library has to be importable on all supported python versions,
also the ones that have no further tests (Watlow F4 and F4T).
'''
import importlib

import pytest


@pytest.mark.parametrize('module', [
    'controllerinterface', 'espec', 'especinteract', 'modbus', 'p300', 'scp220', 'watlowf4', 'watlowf4t',
])
def test_module_can_be_imported(module):
    importlib.import_module('chamberconnectlibrary.' + module)
