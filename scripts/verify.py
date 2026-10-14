"""Run required local suites; missing dependencies and skips cannot pass."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    'regular': ['tests/', '-m', 'not browser and not http and not module_contract'],
    'modules': ['tests/', '-m', 'module_contract'],
    'http': ['tests/', '-m', 'http'],
    'browser': ['tests/', '-m', 'browser'],
}


def missing_environment(suite):
    if importlib.util.find_spec('pytest') is None:
        return 'Install development dependencies: uv sync --locked --extra dev --extra browser'
    if suite == 'modules' and not shutil.which('node'):
        return 'Install Node.js and make node available on PATH'
    if suite in ('http', 'browser'):
        try:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
        except OSError:
            return 'Allow this process to bind a loopback port for isolated HTTP fixtures'
    if suite == 'browser':
        if importlib.util.find_spec('playwright') is None:
            return 'Install the browser extra: uv sync --locked --extra dev --extra browser'
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as playwright:
                options = {'headless': True}
                if os.environ.get('PICSHELF_TEST_BROWSER'):
                    options['executable_path'] = os.environ['PICSHELF_TEST_BROWSER']
                browser = playwright.chromium.launch(**options)
                browser.close()
        except Exception:
            return 'Install a usable Chromium: python -m playwright install --with-deps chromium; check PICSHELF_TEST_BROWSER'
    return None


def assess_report(path, exit_code):
    try:
        cases = list(ET.parse(path).getroot().iter('testcase'))
    except (OSError, ET.ParseError):
        return {'status': 'failed' if exit_code else 'incomplete', 'reason': 'No readable test report'}
    counts = {'passed': 0, 'failed': 0, 'skipped': 0}
    for case in cases:
        key = ('failed' if case.find('failure') is not None or case.find('error') is not None
               else 'skipped' if case.find('skipped') is not None else 'passed')
        counts[key] += 1
    status = ('failed' if counts['failed'] or exit_code not in (0, 5)
              else 'incomplete' if counts['skipped'] or not cases or exit_code else 'passed')
    return {'status': status, **counts,
            'reason': 'Required tests were skipped or not collected' if status == 'incomplete' else ''}


def run_suite(suite, output):
    missing = missing_environment(suite)
    if missing:
        return {'status': 'missing_environment', 'reason': missing}
    report = output / f'{suite}.xml'
    command = [sys.executable, '-m', 'pytest', *SUITES[suite], '-q', '-ra',
               f'--junitxml={report.relative_to(ROOT)}',
               f'--basetemp={(output / (suite + "-fixtures")).relative_to(ROOT)}']
    env = dict(os.environ, PICSHELF_LANG='zh', PICSHELF_BROWSER_TESTS='1', PICSHELF_HTTP_TESTS='1')
    started = time.monotonic()
    print(f'[{suite}] python -m pytest {" ".join(SUITES[suite])} -q -ra', flush=True)
    with (output / f'{suite}.log').open('w') as log:
        process = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    result = assess_report(report, process.returncode)
    result.update(command=['python', *command[1:]], exit_code=process.returncode,
                  elapsed_seconds=round(time.monotonic() - started, 3))
    print('\n'.join((output / f'{suite}.log').read_text().splitlines()[-8:]), flush=True)
    return result


def code_identity():
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT)
    files = git('ls-files', '--others', '--exclude-standard').decode().splitlines()
    return {'head': git('rev-parse', 'HEAD').decode().strip(),
            'diff_sha256': hashlib.sha256(git('diff', '--binary', 'HEAD')).hexdigest(),
            'untracked': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}}


def result_code(results):
    if any(item['status'] == 'failed' for item in results.values()):
        return 1
    return 0 if all(item['status'] == 'passed' for item in results.values()) else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=SUITES, action='append', help='Run only a named suite; default: all')
    args = parser.parse_args(argv)
    output = ROOT / 'data/verification' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output.mkdir(parents=True)
    versions = {}
    for name in ('pytest', 'fastapi', 'pydantic', 'pillow', 'textual', 'playwright'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    summary = {'code': code_identity(), 'python': platform.python_version(),
               'platform': platform.system() + ' ' + platform.machine(), 'dependencies': versions, 'suites': {}}
    for suite in dict.fromkeys(args.suite or SUITES):
        summary['suites'][suite] = run_suite(suite, output)
        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    whitespace = subprocess.run(['git', 'diff', '--check'], cwd=ROOT, capture_output=True, text=True)
    (output / 'diff-check.log').write_text(whitespace.stdout + whitespace.stderr)
    summary['suites']['diff'] = {'status': 'failed' if whitespace.returncode else 'passed'}
    changed = code_identity() != summary['code']
    summary['suites']['code'] = {'status': 'incomplete' if changed else 'passed',
                                'reason': 'Code changed during verification; rerun on stable code' if changed else ''}
    code = result_code(summary['suites'])
    summary['exit_code'] = code
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    for name, result in summary['suites'].items():
        print(f'{name}: {result["status"]}; passed={result.get("passed", 0)} failed={result.get("failed", 0)} skipped={result.get("skipped", 0)} {result.get("reason", "")}')
    print(f'Evidence: {(output / "summary.json").relative_to(ROOT)}')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
