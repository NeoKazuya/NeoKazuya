import datetime as dt
import unittest
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlsplit
from unittest.mock import call, patch
import activity as a
class Tests(unittest.TestCase):
 def test_ranking_and_totals(self):
  rows=[{'name':n,'description':'a & <b>','prs':p,'commits':c} for n,p,c in [('z',2,9),('b',3,1),('a',3,1),('x',0,8)]]
  self.assertEqual([p['name'] for p in a.rank(rows)],['a','b','z','x'])
  root=ET.fromstring(a.render(rows,'Oct 2–8','2026-10-08'))
  text=' '.join(root.itertext());self.assertIn('8 PRs merged',text);self.assertIn('19 commits',text);self.assertIn('+ 1 other projects',text)
 def test_zero_and_long_names(self):
  ET.fromstring(a.render([],'Oct 2–8','now'))
  root=ET.fromstring(a.render([{'name':'x'*100,'description':'z'*150,'prs':999,'commits':1000}],'Oct 2–8','now','light'))
  self.assertNotIn('x'*40,' '.join(root.itertext()))
 def test_pr_bounds_author_and_closed_unmerged(self):
  start=a.instant('2026-10-02T07:00:00Z');end=a.instant('2026-10-09T07:00:00Z')
  def pr(date,author='me'):return {'merged_at':date,'user':{'login':author},'updated_at':'2026-10-09T08:00:00Z'}
  self.assertEqual(a.count_prs([[pr('2026-10-02T07:00:00Z'),pr('2026-10-09T07:00:00Z'),pr(None),pr('2026-10-03T00:00:00Z','other')]],'me',start,end),1)
 def test_pagination(self):
  with patch.object(a,'api',side_effect=[[{}]*100,[{}]*3]) as call:
   self.assertEqual(sum(len(p) for p in a.pages('/test?q=a')),103)
   self.assertEqual(call.call_args.args[0],'/test?q=a&per_page=100&page=2')

class CoverageTests(unittest.TestCase):
 def test_membership_and_external_discovery(self):
  start=a.instant('2026-10-02T07:00:00Z');end=a.instant('2026-10-09T07:00:00Z')
  owned={'full_name':'NeoKazuya/own'};collab={'full_name':'AdminAsistee/TCGNakama'}
  with patch.object(a,'pages',return_value=iter([[owned,collab]])) as listing, patch.object(a,'search_items',side_effect=[iter([{'repository_url':'https://api.github.com/repos/upstream/public'}]),iter([{'repository':{'full_name':'upstream/commit-only'}}])]), patch.object(a,'api',side_effect=lambda path:{'full_name':path.removeprefix('/repos/')}):
   repos=a.discover_repos('NeoKazuya',start,end)
   self.assertEqual({r['full_name'] for r in repos},{'NeoKazuya/own','AdminAsistee/TCGNakama','upstream/public','upstream/commit-only'})
   self.assertIn('collaborator,organization_member',listing.call_args.args[0])
 def test_search_refuses_partial_results(self):
  with patch.object(a,'api',return_value={'incomplete_results':True,'total_count':4,'items':[]}):
   with self.assertRaises(RuntimeError):list(a.search_items('issues','test'))


class ApiRecoveryTests(unittest.TestCase):
 def response(self, status, *, headers='', body='{}', stderr=''):
  return a.subprocess.CompletedProcess([], int(status >= 400), f'HTTP/2.0 {status}\n{headers}\n{body}', stderr)

 def test_retries_server_error_and_honors_retry_after(self):
  responses = [self.response(503), self.response(429, headers='Retry-After: 7\n'), self.response(200, body='[1]')]
  with patch.object(a.subprocess, 'run', side_effect=responses), patch.object(a.time, 'sleep') as sleep:
   self.assertEqual(a.api('/user/repos'), [1])
  self.assertEqual(sleep.call_args_list, [call(2), call(7)])

 def test_primary_rate_limit_respects_reset(self):
  responses = [self.response(403, headers='X-RateLimit-Remaining: 0\nX-RateLimit-Reset: 1075\n'), self.response(200)]
  with patch.object(a.subprocess, 'run', side_effect=responses), patch.object(a.time, 'time', return_value=1000), patch.object(a.time, 'sleep') as sleep:
   self.assertEqual(a.api('/user/repos'), {})
  sleep.assert_called_once_with(76)

 def test_permission_error_reports_status_without_private_details(self):
  response = self.response(403, headers='X-GitHub-Request-Id: 123A:456B\n', body='{"message":"secret body"}', stderr='secret stderr')
  with patch.object(a.subprocess, 'run', return_value=response) as run, patch.object(a.time, 'sleep') as sleep:
   with self.assertRaisesRegex(RuntimeError, r'/repos/<owner>/<repo>/pulls.*HTTP 403, request ID 123A:456B') as error:
    a.api('/repos/private-owner/private-repo/pulls?state=closed')
  self.assertNotRegex(str(error.exception), 'private-owner|private-repo|secret|state=closed')
  run.assert_called_once()
  sleep.assert_not_called()


class SearchRecoveryTests(unittest.TestCase):
 def test_recovers_incomplete_range_and_over_limit_subrange(self):
  def response(path):
   query = parse_qs(urlsplit(path).query)['q'][0]
   dates = query.split('committer-date:')[1]
   if dates == '2026-11-22..2026-11-25':
    return {'incomplete_results': True, 'total_count': 1, 'items': ['discard me']}
   if dates == '2026-11-22..2026-11-23':
    return {'incomplete_results': False, 'total_count': 1001, 'items': []}
   return {'incomplete_results': False, 'total_count': 1, 'items': [dates]}

  with patch.object(a, 'api', side_effect=response):
   results = a.search_date_range('commits', 'author:me', 'committer-date', dt.date(2026, 11, 22), dt.date(2026, 11, 25))
  self.assertEqual(results, ['2026-11-22', '2026-11-23', '2026-11-24..2026-11-25'])

 def test_single_day_retry_discards_incomplete_pagination_attempt(self):
  def result(items, incomplete=False):
   return {'incomplete_results': incomplete, 'total_count': 101, 'items': items}

  responses = [result(['stale'] * 100), result([], True), result(['current'] * 100), result(['last'])]
  with patch.object(a, 'api', side_effect=responses) as api, patch.object(a.time, 'sleep') as sleep:
   date = dt.date(2026, 11, 25)
   results = a.search_date_range('commits', 'author:me', 'committer-date', date, date)
  self.assertEqual(results, ['current'] * 100 + ['last'])
  self.assertEqual([parse_qs(urlsplit(args[0]).query)['page'][0] for args, _ in api.call_args_list], ['1', '2', '1', '2'])
  sleep.assert_called_once_with(2)

 def test_single_day_limit_still_preserves_the_existing_card(self):
  with patch.object(a, 'api', return_value={'incomplete_results': False, 'total_count': 1001, 'items': []}) as api:
   with self.assertRaisesRegex(a.IncompleteSearch, '1001 matches.*2026-11-25 UTC.*Existing card preserved'):
    date = dt.date(2026, 11, 25)
    a.search_date_range('commits', 'author:me', 'committer-date', date, date)
  api.assert_called_once()


class DescriptionTests(unittest.TestCase):
 def test_full_description_survives_wrapping(self):
  description = 'A long GitHub description with words that should wrap. ' * 8
  row={'name':'project','description':description,'prs':1,'commits':2}
  root=ET.fromstring(a.render([row],'Oct 2–8','now'))
  values=[t.text or '' for t in root.findall('{http://www.w3.org/2000/svg}text')]
  lines=[t for t in values if t.startswith('A long') or 'description' in t or 'should wrap' in t]
  self.assertIn(' '.join(description.split()),' '.join(values))
  self.assertNotIn('…',' '.join(values))
  self.assertGreater(int(root.attrib['height']),230)

class AltTextTests(unittest.TestCase):
 def test_alt_escapes_names_and_preserves_layout(self):
  rows=[{'name':'a "quoted" & <name>', 'prs':2, 'commits':7}]
  original='<picture><img alt="old" src="assets/activity-dark.svg" width="710" /></picture>'
  updated=a.update_readme_alt(original,rows,'Oct 2–Oct 8')
  root=ET.fromstring(updated);image=root.find('img')
  self.assertEqual(image.attrib['width'],'710')
  self.assertIn('2 merged PRs, 7 default-branch commits',image.attrib['alt'])
  self.assertIn(rows[0]['name'],image.attrib['alt'])
  self.assertEqual(updated,a.update_readme_alt(updated,rows,'Oct 2–Oct 8'))
 def test_alt_uses_all_totals_but_only_top_three_names(self):
  rows=[{'name':str(i),'prs':i,'commits':i} for i in range(1,5)]
  self.assertIn('10 merged PRs',a.activity_alt(rows,'week'))
  self.assertIn('Top projects by merged PRs: 4, 3, 2.',a.activity_alt(rows,'week'))
 def test_missing_card_fails_instead_of_silently_staling(self):
  with self.assertRaises(ValueError):a.update_readme_alt('other content',[],'week')

if __name__=='__main__':unittest.main()
