"""Provider-reported consumption, without monetary estimates."""
import json
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from src.processors import claude_cli_analyzer as analyzer_mod, llm_health
from src.processors.claude_cli_analyzer import ClaudeCliAnalyzer
from src.processors.llm_provider import get_provider, normalize_output
from src.storage.json_storage import append_llm_usage_log, load_llm_usage_log


@pytest.mark.parametrize('costs,expected', [
    ([0], 0), ([0.13, 0.2], 0.33), ([None], None),
    ([0.13, None], None), ([None, 0.13], None),
    ([True], None), ([-1], None), ([float('nan')], None),
])
def test_optional_cost_preserves_zero_and_requires_every_call(costs, expected):
    analyzer = ClaudeCliAnalyzer(provider='claude', model='test')
    for cost in costs:
        payload = {'usage': {'input_tokens': 10, 'output_tokens': 3}}
        if cost is not None:
            payload['total_cost_usd'] = cost
        analyzer._accumulate_usage(json.dumps(payload))
    usage = analyzer.usage_summary()
    assert usage['total_cost_usd'] == expected
    assert usage['input_tokens'] == 10 * len(costs)
    assert usage['output_tokens'] == 3 * len(costs)
    assert usage['calls'] == len(costs)
    assert 'cost_estimated' not in usage


def test_codex_token_totals_do_not_double_count_details():
    events = [
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}'}},
        {'type': 'turn.completed', 'usage': {
            'input_tokens': 100, 'cached_input_tokens': 80,
            'output_tokens': 30, 'reasoning_output_tokens': 20,
        }},
        {'type': 'turn.completed', 'usage': {
            'input_tokens': 50, 'cached_input_tokens': 40,
            'output_tokens': 10, 'reasoning_output_tokens': 5,
        }},
    ]
    cp = subprocess.CompletedProcess([], 0, '\n'.join(map(json.dumps, events)), '')
    normalized = normalize_output(get_provider('codex'), cp)
    analyzer = ClaudeCliAnalyzer(provider='codex', model='test')
    analyzer._accumulate_usage(normalized)
    usage = analyzer.usage_summary()
    assert usage['input_tokens'] == 150
    assert usage['output_tokens'] == 40
    assert usage['cached_input_tokens'] == 120
    assert usage['reasoning_output_tokens'] == 25
    assert usage['total_cost_usd'] is None


def test_claude_cache_breakdown_and_nullable_cost_roundtrip(tmp_path):
    analyzer = ClaudeCliAnalyzer(provider='claude', model='test')
    analyzer._accumulate_usage(json.dumps({'usage': {
        'input_tokens': 10, 'cache_read_input_tokens': 50,
        'cache_creation_input_tokens': 20, 'output_tokens': 5,
    }}))
    usage = analyzer.usage_summary()
    path = tmp_path / 'usage.json'
    append_llm_usage_log(usage, path)
    assert load_llm_usage_log(path) == [usage]
    assert usage['input_tokens'] == 80
    assert usage['cached_input_tokens'] == 50
    assert usage['cache_write_input_tokens'] == 20
    assert usage['auth_mode'] == 'unknown'
    assert usage['model'] == 'test'
    assert usage['total_cost_usd'] is None


@pytest.mark.parametrize('provider,stdout,stderr,expected', [
    ('claude', '{"loggedIn":true,"authMethod":"claude.ai"}', '', 'account'),
    ('claude', '{"loggedIn":true,"authMethod":"api_key"}', '', 'api_key'),
    ('claude', '{"loggedIn":true}', '', 'unknown'),
    ('claude', '{"loggedIn":true,"authMethod":"other"}', '', 'unknown'),
    ('claude', '{"loggedIn":false,"authMethod":"api_key"}', '', 'unknown'),
    ('codex', '', 'Logged in using ChatGPT', 'account'),
    ('codex', '', 'Logged in using an API key - SECRET', 'api_key'),
    ('codex', '', 'Logged in using ChatGPT; Logged in using an API key', 'unknown'),
    ('codex', '', 'Logged in using something else', 'unknown'),
])
def test_auth_probe_classifies_only_explicit_status(monkeypatch, provider, stdout, stderr, expected):
    monkeypatch.setattr(llm_health.shutil, 'which', lambda _: '/mock/cli')
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout, stderr))
    monkeypatch.setattr(llm_health, '_real_run', run)
    env = {'PATH': '/mock'}
    assert llm_health.detect_auth_mode(provider, '/mock/custom-cli', env) == expected
    assert run.call_args.args[0][0] == '/mock/custom-cli'
    assert run.call_args.kwargs['env'] is env


def test_ambiguous_environment_override_is_unknown(monkeypatch):
    monkeypatch.setattr(llm_health.shutil, 'which', lambda _: '/mock/codex')
    monkeypatch.setattr(llm_health, '_real_run', lambda *a, **k:
        subprocess.CompletedProcess([], 0, '', 'Logged in using ChatGPT'))
    assert llm_health.detect_auth_mode('codex', '', {'CODEX_API_KEY': 'SECRET'}) == 'unknown'


def test_probe_failure_does_not_log_secrets(monkeypatch, caplog):
    monkeypatch.setattr(llm_health.shutil, 'which', lambda _: '/mock/claude')
    run = Mock(side_effect=OSError('SECRET'))
    monkeypatch.setattr(llm_health, '_real_run', run)
    assert llm_health.detect_auth_mode('claude', '', {}) == 'unknown'
    assert 'SECRET' not in caplog.text


def test_execution_snapshots_auth_once_using_same_environment(monkeypatch):
    env = {'PATH': '/mock'}
    monkeypatch.setattr(analyzer_mod, '_clean_subprocess_env', lambda: env)
    probe = Mock(return_value='api_key')
    monkeypatch.setattr(analyzer_mod, '_detect_auth_mode', probe)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, '{"structured_output":{}}', ''))
    monkeypatch.setattr(analyzer_mod.subprocess, 'run', run)
    analyzer = ClaudeCliAnalyzer(provider='claude', model='test', binary='/mock/custom')
    analyzer.run_structured('a', {})
    analyzer.run_structured('b', {})
    probe.assert_called_once_with('claude', '/mock/custom', env)
    assert run.call_args.kwargs['env'] is env
    assert analyzer.usage_summary()['auth_mode'] == 'api_key'


@pytest.mark.skipif(shutil.which('node') is None, reason='Node needed to execute dashboard JavaScript')
def test_dashboard_cost_and_access_rendering():
    from src.outputs.dashboard_output import DashboardOutput
    html = DashboardOutput().render_html([])
    script = 'const KIND_LABELS' + html.split('const KIND_LABELS', 1)[1].split('  function isoDay', 1)[0]
    # Execute the production renderer with minimal DOM targets, not a copy.
    harness = '''
const assert = require('node:assert/strict');
const esc = value => String(value);
const metric = (label, value) => label + ':' + value + ';';
const usageSummary = {}, usageTableWrap = {};
let usageData = [];
'''
    checks = '''
usageData = [
  {total_cost_usd: null, auth_mode: 'account', input_tokens: 10},
  {total_cost_usd: 0, auth_mode: 'api_key'},
  {total_cost_usd: 50, cost_estimated: true},
  {total_cost_usd: 0.25}
];
renderUsage();
assert.ok(usageSummary.innerHTML.includes('Costi disponibili:$0.25;'));
assert.ok(usageTableWrap.innerHTML.includes('Account Claude/ChatGPT'));
assert.ok(usageTableWrap.innerHTML.includes('API key'));
assert.ok(usageTableWrap.innerHTML.includes('Non disponibile'));
assert.ok(!usageTableWrap.innerHTML.includes('$50'));
assert.equal((usageTableWrap.innerHTML.match(/\\$0.0000/g) || []).length, 1);
assert.equal((usageTableWrap.innerHTML.match(/<td class="mm-num"><\\/td>/g) || []).length, 2);
usageData = [{}, {total_cost_usd: null}, {total_cost_usd: 9, cost_estimated: true}];
renderUsage();
assert.ok(usageSummary.innerHTML.includes('Costi disponibili:;'));
assert.ok(!usageTableWrap.innerHTML.includes('$'));
'''
    subprocess.run(['node', '-e', harness + script + checks], check=True, capture_output=True, text=True)


def test_processor_logs_missing_cost_without_formatting_error(monkeypatch, caplog):
    from src.processors import processor_service as module
    analyzer = ClaudeCliAnalyzer(provider='codex', model='test')
    analyzer._accumulate_usage('{"usage":{"input_tokens":10,"output_tokens":5}}')
    monkeypatch.setattr(analyzer, 'analyze_items', lambda _: {})
    monkeypatch.setattr(module, 'load_llm_insights', lambda _: {})
    monkeypatch.setattr(module, 'filter_items_for_analysis', lambda *a, **k: [{'url': 'https://example.com'}])
    save = Mock()
    monkeypatch.setattr(module, 'append_llm_usage_log', save)
    service = module.ProcessorService(analyzer=analyzer, profile={}, llm_enabled=True)
    with caplog.at_level('INFO'):
        service._apply_llm_insights([], [])
    assert 'cost not available' in caplog.text
    assert save.call_args.args[0]['total_cost_usd'] is None
    assert save.call_args.args[0]['input_tokens'] == 10


def test_failed_batch_prevents_partial_total(monkeypatch):
    monkeypatch.setattr(analyzer_mod, '_detect_auth_mode', lambda *a: 'account')
    responses = [
        subprocess.CompletedProcess([], 0, '{"total_cost_usd":0.2,"structured_output":{}}', ''),
        subprocess.CompletedProcess([], 1, '', 'failed'),
        subprocess.CompletedProcess([], 0, '{"total_cost_usd":0.3,"structured_output":{}}', ''),
    ]
    monkeypatch.setattr(analyzer_mod.subprocess, 'run', Mock(side_effect=responses))
    analyzer = ClaudeCliAnalyzer(provider='claude', model='test', max_retries=0)
    analyzer.run_structured('a', {})
    analyzer.run_structured('b', {})
    analyzer.run_structured('c', {})
    assert analyzer.usage_summary()['total_cost_usd'] is None
