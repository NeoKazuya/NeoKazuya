import datetime as dt
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
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

if __name__=='__main__':unittest.main()
