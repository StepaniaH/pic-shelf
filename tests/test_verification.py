"""The complete verification gate must not hide untested requirements."""
import pytest

from scripts import verify


@pytest.mark.parametrize(('body', 'exit_code', 'status'), [
    ('<testcase name="works"/>', 0, 'passed'),
    ('<testcase name="skips"><skipped message="runtime missing"/></testcase>', 0, 'incomplete'),
    ('<testcase name="fails"><failure/></testcase>', 1, 'failed'),
    ('<testcase name="errors"><error/></testcase>', 1, 'failed'),
    ('', 5, 'incomplete'),
    ('<testcase name="works"/>', 1, 'failed'),
])
def test_required_report_outcomes(tmp_path, body, exit_code, status):
    report = tmp_path / 'report.xml'
    report.write_text('<testsuites><testsuite>' + body + '</testsuite></testsuites>')
    assert verify.assess_report(report, exit_code)['status'] == status


def test_missing_reports_cannot_pass(tmp_path):
    assert verify.assess_report(tmp_path / 'missing.xml', 0)['status'] == 'incomplete'


def test_missing_node_is_actionable_and_does_not_start_tests(tmp_path, monkeypatch):
    monkeypatch.setattr(verify.shutil, 'which', lambda _: None)
    monkeypatch.setattr(verify.subprocess, 'run', lambda *a, **k: pytest.fail('A missing runtime must not run the suite'))
    result = verify.run_suite('modules', tmp_path)
    assert result['status'] == 'missing_environment' and 'Node.js' in result['reason']
    assert verify.result_code({'modules': result}) == 2


def test_failure_takes_precedence_over_incomplete_environment():
    assert verify.result_code({'browser': {'status': 'missing_environment'},
                               'regular': {'status': 'failed'}}) == 1


def test_loopback_restriction_is_not_a_success(monkeypatch):
    def blocked(*args):
        raise PermissionError('fixture restriction')
    monkeypatch.setattr(verify.socket, 'socket', blocked)
    result = verify.missing_environment('http')
    assert 'loopback' in result
