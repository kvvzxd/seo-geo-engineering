"""Behavioral/invariant tests for implemented tools; not a 38-scenario agent eval."""
import copy, json, sys, tempfile, unittest
from pathlib import Path

PKG=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PKG/'scripts'))
import policy, rep, snapshot, seo_geo

def fixture(name): return json.loads((PKG/'assets'/name).read_text(encoding='utf-8'))

class RobotsTests(unittest.TestCase):
    def decision(self,text,path='/x',token='Googlebot'):
        return rep.evaluate(text,'https://example.com'+path,token)['decision']
    def test_default(self): self.assertEqual(self.decision(''),'allow')
    def test_block(self): self.assertEqual(self.decision('User-agent: *\nDisallow: /'),'disallow')
    def test_specific_no_inheritance(self):
        self.assertEqual(self.decision('User-agent: *\nDisallow: /x\nUser-agent: Googlebot\nAllow: /'),'allow')
    def test_specific_group_merge(self):
        self.assertEqual(self.decision('User-agent: Googlebot\nDisallow: /a\nUser-agent: Googlebot\nDisallow: /x'),'disallow')
    def test_longer_allow(self): self.assertEqual(self.decision('User-agent: *\nDisallow: /\nAllow: /x'),'allow')
    def test_tie_allow(self): self.assertEqual(self.decision('User-agent: *\nDisallow: /x\nAllow: /x'),'allow')
    def test_documented_wildcard_precedence(self):
        self.assertEqual(self.decision('User-agent: *\nAllow: /page\nDisallow: /*.htm','/page.htm'),'disallow')
        self.assertEqual(self.decision('User-agent: *\nAllow: /page\nDisallow: /*.ph','/page.php5'),'allow')
    def test_no_arbitrary_agent_substring(self):
        self.assertEqual(self.decision('User-agent: bot\nDisallow: /'),'allow')
    def test_matching_complexity_bound(self):
        self.assertEqual(self.decision('User-agent: *\nDisallow: /'+'*x'*1000,'/'+'x'*2000),'unknown')
    def test_case_sensitive_path(self): self.assertEqual(self.decision('User-agent: *\nDisallow: /X'),'allow')
    def test_end_anchor(self):
        r='User-agent: *\nDisallow: /x$'
        self.assertEqual(self.decision(r),'disallow'); self.assertEqual(self.decision(r,'/x?q=1'),'allow')
    def test_wildcard(self): self.assertEqual(self.decision('User-agent: *\nDisallow: /*?q=','/x?q=1'),'disallow')
    def test_empty_disallow(self): self.assertEqual(self.decision('User-agent: *\nDisallow:'),'allow')
    def test_escaped_unreserved(self): self.assertEqual(self.decision('User-agent: *\nDisallow: /%78'),'disallow')
    def test_escaped_reserved(self): self.assertEqual(self.decision('User-agent: *\nDisallow: /a%2Fb','/a/b'),'allow')
    def test_unicode(self): self.assertEqual(self.decision('User-agent: *\nDisallow: /цена','/%D1%86%D0%B5%D0%BD%D0%B0'),'disallow')
    def test_unknown(self): self.assertEqual(self.decision('User-agent: *\nnoai: yes'),'unknown')
    def test_unsupported_profile(self): self.assertEqual(rep.evaluate('','https://example.com',profile='yandex')['decision'],'unknown')
    def test_size_limit(self): self.assertEqual(self.decision('#'+'a'*512001),'unknown')
    def test_sitemap_does_not_split_group(self):
        self.assertEqual(self.decision('User-agent: Other\nSitemap: https://example.com/s.xml\nUser-agent: Googlebot\nDisallow: /'),'disallow')

class CaptureTests(unittest.TestCase):
    def setUp(self): self.c=fixture('capture.example.json'); self.f=fixture('facts.example.json')
    def test_extract(self):
        a=snapshot.analyze(self.c)
        self.assertEqual(a['title'],'Установка'); self.assertIn('материалы отдельно',a['text']); self.assertEqual(a['canonical'],[self.c['url']])
    def test_header_overrides_meta(self):
        self.c['headers']['X-Robots-Tag']='noindex'; self.c['html']=self.c['html'].replace('<title>','<meta name="robots" content="index"><title>')
        self.assertEqual(snapshot.analyze(self.c)['indexing_directive'],'blocked')
    def test_targeted_header(self):
        self.c['headers']['X-Robots-Tag']=['otherbot: noindex','max-snippet:50']
        self.assertEqual(snapshot.analyze(self.c)['indexing_directive'],'no_blocking_directive')
    def test_none(self):
        self.c['headers']['X-Robots-Tag']='none'; self.assertEqual(snapshot.analyze(self.c)['indexing_directive'],'blocked')
    def test_jsonld_not_visible_text(self):
        self.c['html']+='<script type="application/ld+json">{"price": 99}</script>'
        a=snapshot.analyze(self.c); self.assertEqual(a['jsonld'],[{'price':99}]); self.assertNotIn('price',a['text'])
    def test_fact_good_not_proven(self):
        r=snapshot.check_facts(self.f,self.c); self.assertEqual(r['flagged'],0); self.assertFalse(r['truth_proven']); self.assertTrue(r['semantic_review_required'])
    def test_from_lost(self):
        self.c['html']=self.c['html'].replace('от 25','25'); self.assertEqual(snapshot.check_facts(self.f,self.c)['flagged'],1)
    def test_exclusion_lost(self):
        self.c['html']=self.c['html'].replace(', материалы отдельно',''); self.assertEqual(snapshot.check_facts(self.f,self.c)['flagged'],1)
    def test_number_in_other_entity(self):
        self.c['html']=self.c['html'].replace('Установка: от 25 000 ₸ за одну установку, материалы отдельно.','Ремонт: от 25 000 ₸ за одну установку, материалы отдельно. Установка: по запросу.')
        self.assertEqual(snapshot.check_facts(self.f,self.c)['flagged'],1)
    def test_snippet_exclusion(self):
        self.c['html']=self.c['html'].replace('материалы отдельно','<span data-nosnippet>материалы отдельно</span>')
        self.assertEqual(snapshot.check_facts(self.f,self.c)['flagged'],1)
    def test_unverified(self):
        self.f['facts'][0]['status']='conflicting'; self.assertEqual(snapshot.check_facts(self.f,self.c)['flagged'],1)
    def test_no_scope(self):
        self.f['facts'][0]['checks'].pop('scope_regex'); self.assertEqual(snapshot.check_facts(self.f,self.c)['flagged'],1)
    def test_fact_array_validation(self):
        self.f['facts'][0]['checks']['required_text']='от'
        with self.assertRaises(ValueError): snapshot.check_facts(self.f,self.c)
    def test_compare(self):
        a=copy.deepcopy(self.c); a['headers']['X-Robots-Tag']='noindex'; a['status']=404
        self.assertEqual(snapshot.compare(self.c,a)['regressions'],['new_indexing_block','status_regression'])

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.p=fixture('policy.example.json')
    def tearDown(self): self.tmp.cleanup()
    def test_schema_validation(self): self.assertEqual(policy.validate(self.p),[])
    def test_unknown_field(self): self.p['magic']='rank_first'; self.assertTrue(policy.validate(self.p))
    def test_wrong_origin_type(self): self.p['origin']={}; self.assertTrue(policy.validate(self.p))
    def test_newline_injection(self): self.p['bot_groups'][0]['rules'][0]['path']='/\nDisallow: /'; self.assertTrue(policy.validate(self.p))
    def test_origin(self): self.p['critical_urls'][0]['url']='https://evil.example/'; self.assertTrue(policy.validate(self.p))
    def test_no_apply_from_plan(self):
        d=policy.plan(self.p,self.root); self.assertFalse((self.root/'robots.txt').exists()); self.assertEqual(len(d['matrix']),2)
    def test_apply_verify_rollback(self):
        d=policy.plan(self.p,self.root); self.assertTrue(policy.apply(d,'production')['ok']); self.assertTrue(policy.verify(d)['ok'])
        policy.apply(d,'production',True); self.assertFalse((self.root/'robots.txt').exists())
    def test_idempotent(self):
        d=policy.plan(self.p,self.root); policy.apply(d,'production'); self.assertEqual(policy.apply(d,'production')['changed'],[])
        d2=policy.plan(self.p,self.root); self.assertEqual(d2['files'][0]['before'],d2['files'][0]['after'])
    def test_optimistic_guard(self):
        d=policy.plan(self.p,self.root); (self.root/'robots.txt').write_text('# owner edit',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'drift'): policy.apply(d,'production')
        self.assertEqual((self.root/'robots.txt').read_text(),'# owner edit')
    def test_environment_guard(self):
        d=policy.plan(self.p,self.root)
        with self.assertRaisesRegex(ValueError,'environment'): policy.apply(d,'staging')
    def test_block_critical(self):
        self.p['bot_groups'][1]['rules']=[{'directive':'Disallow','path':'/'}]
        with self.assertRaisesRegex(ValueError,'critical'): policy.plan(self.p,self.root)
    def test_preserve_crlf(self):
        original=b'# owner\r\nUser-agent: Other\r\nDisallow: /custom\r\n'
        (self.root/'robots.txt').write_bytes(original)
        d=policy.plan(self.p,self.root); policy.apply(d,'production'); policy.apply(d,'production',True)
        self.assertEqual((self.root/'robots.txt').read_bytes(),original)
    def test_invalid_old_block(self):
        (self.root/'robots.txt').write_text(policy.BEGIN,encoding='utf-8')
        with self.assertRaises(ValueError): policy.plan(self.p,self.root)
    def test_preserve_unmanaged_fallback(self):
        (self.root/'robots.txt').write_text('User-agent: *\nDisallow: /account/\n',encoding='utf-8')
        d=policy.plan(self.p,self.root)
        self.assertEqual(rep.evaluate(d['files'][0]['after'],'https://example.com/account/','Googlebot')['decision'],'disallow')
        policy.apply(d,'production')
        d2=policy.plan(self.p,self.root)
        self.assertEqual(d2['files'][0]['after'],d2['files'][0]['before'])
    def test_noindex_inaccessible(self):
        row=self.p['critical_urls'][1]; row.update(expected_indexing='blocked',capture={'url':row['url'],'headers':{'X-Robots-Tag':'noindex'}})
        with self.assertRaisesRegex(ValueError,'critical'): policy.plan(self.p,self.root)
    def test_plan_tamper(self):
        d=policy.plan(self.p,self.root); d['files'][0]['after']='User-agent: *\nDisallow: /'
        with self.assertRaisesRegex(ValueError,'integrity'): policy.apply(d,'production')
    def test_rollback_preserves_owner_edit(self):
        d=policy.plan(self.p,self.root); policy.apply(d,'production'); (self.root/'robots.txt').write_text('# changed',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'drift'): policy.apply(d,'production',True)
    def test_llms_opt_in(self):
        self.p['llms']={'enabled':True,'use_case':'docs client','description':'Public instructions','links':[{'title':'Docs','url':'https://example.com/docs/'}]}
        d=policy.plan(self.p,self.root); self.assertEqual(len(d['files']),2); policy.apply(d,'production'); self.assertIn('/docs/',(self.root/'llms.txt').read_text())
    def test_drift(self):
        d=policy.plan(self.p,self.root); self.assertFalse(policy.verify(d)['ok']); policy.apply(d,'production'); self.assertTrue(policy.verify(d)['ok'])
    def test_lock(self):
        d=policy.plan(self.p,self.root); (self.root/'.seo-geo-policy.lock').touch()
        with self.assertRaisesRegex(ValueError,'lock'): policy.apply(d,'production')

class MetricsTests(unittest.TestCase):
    def runrow(self,rid='1',**kw):
        return dict({'run_id':rid,'query_id':'q1','query':'service','engine':'engine','surface':'UI','language':'ru','country':'KZ','timestamp':'2026-11-03T00:00:00Z','collection_method':'manual_capture','answer':'Example Brand','cited_urls':['https://example.com/a']},**kw)
    def test_geo_counts(self):
        rows=[self.runrow(),self.runrow('2',answer='Other',cited_urls=[])]
        g=seo_geo.geo({'runs':rows},'example.com','Example Brand')['groups'][0]
        self.assertEqual(g['citation_presence']['rate'],.5); self.assertEqual(g['unique_queries'],1); self.assertEqual(g['query_repetitions']['q1'],2)
    def test_host_boundary(self):
        g=seo_geo.geo({'runs':[self.runrow(cited_urls=['https://example.com.evil/a'])]},'example.com','Example Brand')['groups'][0]
        self.assertEqual(g['citation_presence']['numerator'],0)
    def test_unknown_facts(self):
        row=self.runrow(fact_checks=[{'result':'unknown'},{'result':'incorrect'}]); g=seo_geo.geo({'runs':[row]},'example.com','Example Brand')['groups'][0]
        self.assertEqual(g['fact_errors'],{'numerator':1,'denominator':1,'rate':1,'unknown':1})
    def test_missing_answer(self):
        r=self.runrow(); r.pop('answer'); g=seo_geo.geo({'runs':[r]},'example.com','Example Brand'); self.assertEqual(len(g['invalid_runs']),1)
    def test_models_not_conflated(self):
        r=seo_geo.geo({'runs':[self.runrow(model='A'),self.runrow('2',model='B')]},'example.com','Example Brand')
        self.assertEqual(len(r['groups']),2)
    def test_duplicate_runs(self):
        with self.assertRaises(ValueError): seo_geo.geo({'runs':[self.runrow(),self.runrow()]},'example.com','Example Brand')
    def test_analytics_weighting(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'data.csv'; p.write_text('date,page,query,clicks,impressions,position\n2026-11-01,/a,q,1,10,2\n2026-11-02,/a,q,2,30,6\n',encoding='utf-8')
            r=seo_geo.analytics(p,fixture('analytics-context.example.json'))['rows'][0]
            self.assertEqual(r['ctr'],3/40); self.assertEqual(r['impression_weighted_position'],5)
    def test_analytics_nan(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'data.csv'; p.write_text('date,page,query,clicks,impressions\n2026-11-01,/a,q,NaN,10\n',encoding='utf-8')
            with self.assertRaises(ValueError): seo_geo.analytics(p,fixture('analytics-context.example.json'))

if __name__=='__main__': unittest.main(verbosity=2)
