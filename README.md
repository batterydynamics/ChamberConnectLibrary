# ChamberConnectLibrary
Python library for interfacing with Espec North America chambers with P300, SCP-220, Watlow F4T &amp; Watlow F4S/D controllers.

## Requirements
python 3.x

## Installation
```pip install chamberconnectlibrary```

## Updating
Do to some renaming to make the library pep8 compliant some files have been renamed from version 1.x to 2.0.0.
To ensure that the current version is used uninstall and then reinstall the library:
```pip uninstall chamberconnectlibrary```
```pip install chamberconnectlibrary```

## Testing

To test run chamberconnectlibrary-test.py(on windows using COM port #3, test script is located in Python3.x\Scripts directory)

P300: ```chamberconnectlibrary-test.py Espec Serial \\.\COM3 19200```

SCP-220: ```chamberconnectlibrary-test.py EspecSCP220 Serial \\.\COM3 9600```

Watlow F4T: ```chamberconnectlibrary-test.py WatlowF4T RTU \\.\COM3 38400```

Watlow F4: ```chamberconnectlibrary-test.py WatlowF4 RTU \\.\COM3 19200```

## Documentation
See [controllerinterface.md](controllerinterface.md)

## Tests
The tests need no controller, the physical interfaces are replaced by mocks:

```
pip install -e . -r requirements-test.txt
pytest --cov --cov-report=term-missing --cov-report=json
python tests/check_coverage.py coverage.json
```

They run on GitHub for every pull request with Python 3.8 and 3.14, see [tests.yml](.github/workflows/tests.yml).
