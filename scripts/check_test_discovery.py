"""Fail CI if an expected suite silently disappears."""
import argparse
import json
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--build', default='build')
args = parser.parse_args()
report = json.loads(subprocess.run(['ctest','--test-dir',args.build,'-C','Release','--show-only=json-v1'],check=True,capture_output=True,text=True).stdout)
found = {test['name'] for test in report['tests']}
expected = {'engine','strategies','config','service','cli','chart_data','import_data','tradingview','questrade','provider_jobs'}
if not expected <= found:
    raise SystemExit('Missing test suites: '+', '.join(sorted(expected-found)))
print(f'{len(found)} test suites discovered: '+', '.join(sorted(found)))
