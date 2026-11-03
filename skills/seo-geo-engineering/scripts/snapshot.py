"""Analyze supplied captures, never impersonate a real browser or search engine."""
import json, re
from html.parser import HTMLParser
from urllib.parse import urljoin

class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta=[]; self.links=[]; self.canonical=[]; self.text=[]; self.title=[]
        self.jsonld=[]; self.hidden_snippet=[]; self.stack=[]; self.capture=None
        self.capture_parts=[]; self.errors=[]

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hidden = 'data-nosnippet' in a or (self.stack and self.stack[-1][1])
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append((tag, bool(hidden)))
        if tag == 'meta':
            self.meta.append({str(k).lower():v or '' for k,v in attrs})
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
        if tag == 'link' and 'canonical' in a.get('rel','').lower().split():
            self.canonical.append(a.get('href',''))
        if tag == 'script' and a.get('type','').lower() == 'application/ld+json':
            self.capture = True; self.capture_parts=[]

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag,attrs)

    def handle_endtag(self, tag):
        if tag == 'script' and self.capture:
            try:
                self.jsonld.append(json.loads(''.join(self.capture_parts)))
            except ValueError as e:
                self.errors.append('invalid_jsonld: '+str(e))
            self.capture=None
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0] == tag:
                del self.stack[i:]; break

    def handle_data(self, data):
        if self.capture:
            self.capture_parts.append(data)
        tags = {x[0] for x in self.stack}
        if 'title' in tags: self.title.append(data)
        if not tags.intersection({'script','style','head','template'}):
            self.text.append(data)
            if self.stack and self.stack[-1][1]: self.hidden_snippet.append(data)

def analyze(capture):
    if not isinstance(capture,dict) or not isinstance(capture.get('url'),str):
        raise ValueError('capture requires url')
    html = capture.get('html','')
    if not isinstance(html,str): raise ValueError('html must be a string')
    p = Page(); p.feed(html)
    headers = capture.get('headers',{})
    if not isinstance(headers,dict): raise ValueError('headers must be an object; values string or list of strings')
    token = capture.get('crawler_token','Googlebot').lower()
    directives=[]
    for m in p.meta:
        if m.get('name','').lower() in ('robots',token): directives.append(m.get('content','').lower())
    for k, values in headers.items():
        if k.lower() != 'x-robots-tag': continue
        for value in values if isinstance(values,list) else [values]:
            if not isinstance(value,str): raise ValueError('header values must be strings')
            value = value.lower().strip()
            scoped = re.match(r'^([a-z][a-z0-9_-]*):\s*(.*)',value)
            known = {'max-snippet','max-image-preview','max-video-preview','unavailable_after'}
            if scoped and scoped[1] not in known:
                if scoped[1] != token: continue
                value=scoped[2]
            directives.append(value)
    terms = [t.strip() for v in directives for t in v.split(',')]
    blocked = bool({'noindex','none'}.intersection(terms))
    warnings = list(p.errors)
    if len(p.canonical)>1: warnings.append('multiple_canonical_elements')
    if not capture.get('captured_at'): warnings.append('capture_time_unknown')
    return {'url':capture['url'], 'captured_at':capture.get('captured_at'),
            'status':capture.get('status'), 'capture_method':capture.get('capture_method','unspecified'),
            'title':' '.join(''.join(p.title).split()), 'canonical':[urljoin(capture['url'],v) for v in p.canonical],
            'links':[urljoin(capture['url'],v) for v in p.links],
            'text':' '.join(' '.join(p.text).split()), 'data_nosnippet_text':' '.join(' '.join(p.hidden_snippet).split()),
            'jsonld':p.jsonld, 'robots_directives':terms, 'indexing_directive':'blocked' if blocked else 'no_blocking_directive',
            'html_uncompressed_bytes':len(html.encode('utf-8')), 'warnings':warnings,
            'limits':['static_capture_only','not_proof_of_rendering_or_indexing','CSS_visibility_not_evaluated']}

def compare(before, after):
    b,a=analyze(before),analyze(after)
    changes={k:{'before':b[k],'after':a[k]} for k in ('url','status','title','canonical','indexing_directive','robots_directives','jsonld') if b[k]!=a[k]}
    regressions=[]
    if b['indexing_directive']!='blocked' and a['indexing_directive']=='blocked': regressions.append('new_indexing_block')
    if b['status']==200 and a['status']!=200: regressions.append('status_regression')
    if b['text'] and not a['text']: regressions.append('lost_main_text')
    return {'changes':changes, 'regressions':regressions, 'removed_links':sorted(set(b['links'])-set(a['links'])),
            'added_links':sorted(set(a['links'])-set(b['links'])), 'requires_semantic_fact_review':True}

def check_facts(ledger, capture):
    page=analyze(capture); findings=[]; checked=0
    facts=ledger.get('facts')
    if not isinstance(facts,list): raise ValueError('ledger requires facts array')
    ids=set()
    for fact in facts:
        if not isinstance(fact,dict) or not all(k in fact for k in ('fact_id','entity_id','status','source','checks')):
            raise ValueError('fact requires fact_id, entity_id, status, source and checks')
        if not isinstance(fact['fact_id'],str) or not fact['fact_id'] or fact['fact_id'] in ids: raise ValueError('fact_id must be unique nonempty string')
        ids.add(fact['fact_id'])
        issues=[]
        if fact['status']!='verified': issues.append('unresolved_fact_status')
        if not isinstance(fact['source'],dict) or not fact['source'].get('artifact') or not fact['source'].get('locator'): issues.append('missing_source')
        rules=fact['checks']
        if not isinstance(rules,dict): raise ValueError('checks must be object')
        # Scope is an explicit reviewed entity marker/fragment, never a guess by number.
        scope=rules.get('scope_regex')
        if not scope:
            issues.append('entity_scope_not_provided'); fragment=''
        elif not isinstance(scope,str) or len(scope)>500:
            raise ValueError('scope_regex too long; use trusted, bounded patterns only')
        else:
            match=re.search(scope,page['text'],re.I)
            fragment=match[0] if match else ''
            if not match: issues.append('entity_scope_missing')
        terms=rules.get('required_text',[])
        if not isinstance(terms,list) or not isinstance(rules.get('forbidden_text',[]),list): raise ValueError('required_text/forbidden_text must be arrays')
        if not terms: issues.append('no_deterministic_assertions')
        for term in terms:
            if not isinstance(term,str) or not term: raise ValueError('required_text entries must be nonempty strings')
            if not contains_term(fragment,term): issues.append('missing_required_text: '+term)
        for term in rules.get('forbidden_text',[]):
            if contains_term(fragment,term): issues.append('forbidden_text: '+term)
        if rules.get('preserve_snippet_context'):
            for term in terms:
                if contains_term(page['data_nosnippet_text'],term): issues.append('required_context_excluded_from_snippet: '+term)
        findings.append({'fact_id':fact['fact_id'],'entity_id':fact['entity_id'],'issues':issues,
                         'deterministic_result':'flagged' if issues else 'checks_satisfied'})
        checked+=1
    return {'checked':checked,'findings':findings,'flagged':sum(bool(x['issues']) for x in findings),
            'semantic_review_required':True,'truth_proven':False,
            'limits':['substring_checks_do_not_prove_semantic_equivalence','regexes_are_trusted_local_input','jsonld_semantics_require_separate_review']}

def contains_term(text,term):
    if not isinstance(term,str) or not term: raise ValueError('assertion terms must be nonempty strings')
    prefix=r'(?<!\w)' if term[0].isalnum() or term[0]=='_' else ''
    suffix=r'(?!\w)' if term[-1].isalnum() or term[-1]=='_' else ''
    return bool(re.search(prefix+re.escape(term)+suffix,text,re.I))
