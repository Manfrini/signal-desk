"""The "Analizza questo articolo" button must render as a button, not as text."""
import shutil
import subprocess

import pytest

from src.outputs.dashboard_output import DashboardOutput


@pytest.mark.skipif(shutil.which('node') is None, reason='Node needed to execute dashboard JavaScript')
def test_render_card_keeps_insight_button_as_html():
    html = DashboardOutput().render_html([])
    render_card = html[html.index('function renderCard(item)'):html.index('function renderSection(')]
    harness = r'''
const assert = require('node:assert/strict');
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const state = {savedUrls: new Set(), votes: {}};
const ICON = new Proxy({}, {get: () => ''});
const categoryLabel = x => x, personalScore = () => 0, formatDate = () => '';
const signalScore = () => 1, sourceInitial = () => 'O', safeHref = u => u;
'''
    checks = r'''
const missing = renderCard({url: 'https://x.com/a', title: 'T'});
assert.equal((missing.match(/<button type="button" class="dd-retry insight-btn"/g) || []).length, 2);
assert.ok(!missing.includes('&lt;button'));
const analysed = renderCard({url: 'https://x.com/b', title: 'T', why_it_matters: '<b>x</b>', possible_action: 'y', insight_source: 'claude_cli'});
assert.ok(analysed.includes('&lt;b&gt;x'));
assert.ok(!analysed.includes('insight-btn'));
'''
    subprocess.run(['node', '-e', harness + render_card + checks], check=True, capture_output=True, text=True)
