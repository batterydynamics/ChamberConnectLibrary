'''
Checks the line coverage of the tested modules against their minimum.

    pytest --cov --cov-report=json
    python tests/check_coverage.py coverage.json

The Watlow controllers (watlowf4, watlowf4t) have no tests of their own, their coverage
is only reported.
'''
import json
import sys

MINIMUM = {
    'especinteract.py': 95,
    'modbus.py': 95,
    'controllerinterface.py': 85,
    'espec.py': 85,
    'p300.py': 85,
    'scp220.py': 85,
}


def main(report):
    with open(report) as handle:
        files = json.load(handle)['files']
    covered = dict(
        (path.replace('\\', '/').split('/')[-1], data['summary']['percent_covered']) for path, data in files.items()
    )
    failed = False
    for name in sorted(covered):
        minimum = MINIMUM.get(name)
        if minimum is None:
            status = 'not checked'
        elif covered[name] >= minimum:
            status = 'ok (minimum %d %%)' % minimum
        else:
            status = 'TOO LOW (minimum %d %%)' % minimum
            failed = True
        print('%-26s %5.1f %%  %s' % (name, covered[name], status))
    for name in sorted(set(MINIMUM) - set(covered)):
        print('%-26s   no coverage data' % name)
        failed = True
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'coverage.json'))
