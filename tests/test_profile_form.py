"""Profile form accessibility and browser-side save/proposal behavior."""
import shutil
import subprocess

import pytest
from bs4 import BeautifulSoup

from src.outputs.dashboard_output import DashboardOutput


def test_profile_controls_have_persistent_accessible_help():
    soup = BeautifulSoup(DashboardOutput().render_html([]), 'html.parser')
    profile = soup.select_one('#profileView')
    for field_id in ('profAboutMe', 'profGoal', 'profInterests', 'profSkills', 'profKeywords'):
        field = profile.select_one('#' + field_id)
        assert field.name == 'textarea'
        assert profile.find('label', attrs={'for': field_id}).get_text(strip=True)
        assert profile.select_one('#' + field['aria-describedby']).get_text(strip=True)
    for field_id in ('profMsg', 'profExpandMsg', 'llmSettingsMsg'):
        assert profile.select_one('#' + field_id)['aria-live'] == 'polite'
    assert profile.select('details') == []
    assert str(profile).index('id="profSave"') < str(profile).index('id="llmSettingsSave"')


@pytest.mark.skipif(shutil.which('node') is None, reason='Node needed for dashboard JavaScript')
def test_profile_proposal_and_explicit_save_preserve_list_semantics():
    html = DashboardOutput().render_html([])
    script = 'let profilePollTimer' + html.split('let profilePollTimer', 1)[1].split('  function refreshProfileBanner', 1)[0]
    harness = r'''
const assert = require('node:assert/strict');
const ids = ['profAboutMe','profGoal','profInterests','profSkills','profKeywords',
             'profExpand','profExpandLabel','profExpandMsg','profMsg'];
const fields = Object.fromEntries(ids.map(id => [id, {value:'',textContent:'',style:{},disabled:false}]));
const document = {getElementById: id => fields[id]};
const clearInterval = () => {};
const setInterval = () => 1;
const refreshProfileBanner = () => {};
const requests = [];
let failure = false;
const proposal = {goal:'Obiettivo AI', interests:['SQL','ricerca'],skills_to_grow:['analisi'],keywords_boost:['API']};
async function fetch(url, options={}) {
  requests.push({url,options});
  if (failure) throw new Error('offline');
  if (url === '/api/profile/expand/status') return {json:async()=>({state:'done',proposal})};
  if (url === '/api/profile') return {ok:true,json:async()=>({ok:true,profile:JSON.parse(options.body).profile})};
  return {ok:true,status:202,json:async()=>({})};
}
'''
    checks = r'''
(async () => {
  assert.deepEqual(splitCsv(' SQL, ricerca\nanalisi\r\nAPI, , '), ['SQL','ricerca','analisi','API']);
  // No provider call for an empty description.
  await expandProfile();
  assert.equal(requests.length, 0);
  fields.profAboutMe.value = 'Descrizione manuale';
  await expandProfile();
  await pollExpand();
  assert.equal(fields.profAboutMe.value, 'Descrizione manuale');
  assert.equal(fields.profGoal.value, 'Obiettivo AI');
  assert.equal(requests.filter(r => r.url === '/api/profile').length, 0);
  fields.profGoal.value = '';
  fields.profInterests.value = 'SQL, ricerca\nanalisi\r\nAPI';
  fields.profSkills.value = '';
  fields.profKeywords.value = '';
  await saveProfile();
  const saved = JSON.parse(requests[requests.length-1].options.body).profile;
  assert.equal(saved.goal, '');
  assert.deepEqual(saved.interests, ['SQL','ricerca','analisi','API']);
  assert.deepEqual(saved.skills_to_grow, []);
  assert.deepEqual(saved.keywords_boost, []);
  assert.ok(fields.profMsg.textContent.includes('Profilo salvato'));
  failure = true;
  await saveProfile();
  assert.equal(fields.profMsg.textContent, 'Errore di connessione.');
})().catch(error => {console.error(error); process.exit(1);});
'''
    subprocess.run(['node', '-e', harness + script + checks], check=True, capture_output=True, text=True)
