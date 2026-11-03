"""Local, optimistic-concurrency guarded robots/llms policy workflow."""
import hashlib, json, os, tempfile
from pathlib import Path
from urllib.parse import urlsplit
from rep import evaluate, parse
from snapshot import analyze

BEGIN='# BEGIN seo-geo-engineering managed rules'
END='# END seo-geo-engineering managed rules'
ALLOWED={'robots.txt','llms.txt'}

def canonical(obj): return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(content): return hashlib.sha256(content.encode('utf-8')).hexdigest() if content is not None else None
def read(path): return path.read_bytes().decode('utf-8') if path.exists() else None

def local_path(root, name):
    if name not in ALLOWED: raise ValueError('unmanaged artifact path: '+str(name))
    root=Path(root).resolve()
    path=root/name
    if path.is_symlink() or path.resolve().parent != root: raise ValueError('artifact cannot be a symlink or escape root')
    if path.exists() and not path.is_file(): raise ValueError('artifact must be a regular file')
    return path

def atomic(path, text):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.seo-geo-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='') as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def validate(p):
    try:
        return _validate(p)
    except (ValueError,TypeError,AttributeError) as e:
        return ['invalid policy value type or URL: '+str(e)]

def _validate(p):
    errors=[]
    if not isinstance(p,dict): return ['policy must be object']
    allowed={'schema_version','environment','origin','ownership','bot_groups','sitemaps','critical_urls','llms'}
    errors += ['unknown policy key: '+k for k in p if k not in allowed]
    if p.get('schema_version')!=1: errors.append('schema_version must be 1')
    if p.get('environment') not in ('production','staging'): errors.append('environment required')
    origin=p.get('origin','')
    u=urlsplit(origin)
    if u.scheme not in ('https','http') or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
        errors.append('origin must be an HTTP(S) origin without credentials/path/query')
    if p.get('ownership') not in ('managed_block','whole_file'): errors.append('explicit ownership required')
    groups=p.get('bot_groups')
    if not isinstance(groups,list) or not groups: errors.append('nonempty bot_groups required'); groups=[]
    for g in groups:
        if not isinstance(g,dict) or set(g)-{'user_agents','rules'}:
            errors.append('invalid group keys'); continue
        agents=g.get('user_agents')
        if not isinstance(agents,list) or not agents or any(not isinstance(a,str) or not a or any(c in a for c in '\r\n:# ') for a in agents): errors.append('invalid user_agents')
        rules=g.get('rules')
        if not isinstance(rules,list) or not rules: errors.append('each group needs explicit rules'); continue
        for r in rules:
            if not isinstance(r,dict) or set(r)!={'directive','path'}: errors.append('rule requires directive and path'); continue
            if r['directive'] not in ('Allow','Disallow'): errors.append('only Allow/Disallow generation supported')
            val=r['path']
            if not isinstance(val,str) or (val and not val.startswith('/')) or any(c in val for c in '\r\n#'): errors.append('invalid rule path')
    urls=p.get('critical_urls')
    if not isinstance(urls,list) or not urls: errors.append('critical_urls required'); urls=[]
    for row in urls:
        if not isinstance(row,dict) or set(row)-{'url','token','profile','expected_crawl','expected_indexing','capture'}:
            errors.append('invalid critical URL fields'); continue
        if row.get('profile')!='google': errors.append('only google evaluator profile is implemented')
        if row.get('expected_crawl') not in ('allow','disallow'): errors.append('expected_crawl required')
        if row.get('expected_indexing') not in (None,'blocked','no_blocking_directive'): errors.append('invalid expected_indexing')
        if not row.get('token'): errors.append('crawler token required')
        if not same_origin(origin,row.get('url','')): errors.append('critical URL outside origin')
        if row.get('expected_indexing') and not isinstance(row.get('capture'),dict): errors.append('indexing expectation requires actual capture')
        if isinstance(row.get('capture'),dict) and row['capture'].get('url')!=row.get('url'): errors.append('capture URL differs from critical URL')
    maps=p.get('sitemaps',[])
    if not isinstance(maps,list): errors.append('sitemaps must be list')
    else:
        for s in maps:
            if not isinstance(s,str) or not same_origin(origin,s) or any(c in s for c in '\r\n#'): errors.append('invalid sitemap URL/origin')
    llms=p.get('llms',{'enabled':False})
    if not isinstance(llms,dict) or set(llms)-{'enabled','description','links','use_case'}: errors.append('invalid llms fields')
    elif not isinstance(llms.get('enabled'),bool): errors.append('llms.enabled must be boolean')
    elif llms['enabled']:
        if not llms.get('use_case') or not isinstance(llms.get('description'),str): errors.append('llms requires use_case and description')
        links=llms.get('links')
        if not isinstance(links,list) or not links: errors.append('llms requires links')
        else:
            for row in links:
                if not isinstance(row,dict) or set(row)!={'title','url'} or not isinstance(row['title'],str) or not same_origin(origin,row['url']): errors.append('invalid llms link')
    return errors

def same_origin(a,b):
    try:
        def key(v):
            u=urlsplit(v)
            return (u.scheme.lower(),(u.hostname or '').lower(),u.port or (443 if u.scheme=='https' else 80))
        return key(a)==key(b) and bool(urlsplit(b).hostname) and not urlsplit(b).username and not urlsplit(b).fragment
    except (ValueError,TypeError): return False

def robots_body(p,unmanaged=''):
    out=[]
    old_groups, diagnostics=parse(unmanaged)
    if diagnostics: raise ValueError('unmanaged rules require review: '+canonical(diagnostics))
    specific={a for g in old_groups for a in g['agents'] if a!='*'}
    fallback=[r for g in old_groups if '*' in g['agents'] for r in g['rules']]
    for g in p['bot_groups']:
        for agent in g['user_agents']:
            out.append('User-agent: '+agent)
            rules=[(r['directive'],r['path']) for r in g['rules']]
            if agent!='*' and agent.lower() not in specific and fallback:
                # Materialize prior fallback restrictions when adding a new token.
                out.append('# Retained fallback rules from unmanaged policy')
                rules += [(r['directive'].capitalize(),r['path']) for r in fallback]
            out += [k+': '+v for k,v in dict.fromkeys(rules)]
            out.append('')
    out += ['Sitemap: '+s for s in p.get('sitemaps',[])]
    return '\n'.join(out).strip()+'\n'

def candidate_robots(p,existing):
    if p['ownership']=='whole_file': return robots_body(p)
    old=existing or ''
    if old.count(BEGIN)!=old.count(END) or old.count(BEGIN)>1: raise ValueError('malformed managed block')
    if BEGIN in old:
        start=old.index(BEGIN); end=old.index(END)
        if end<start: raise ValueError('managed block order invalid')
        end+=len(END)
        if old[end:end+2]=='\r\n': end+=2
        elif old[end:end+1]=='\n': end+=1
        unmanaged=old[:start]+old[end:]
        block=BEGIN+'\n'+robots_body(p,unmanaged)+END+'\n'
        return old[:start]+block+old[end:]
    groups, errors=parse(old)
    if errors: raise ValueError('unmanaged rules need review: '+canonical(errors))
    if groups and not groups[-1]['rules']: raise ValueError('cannot append after an empty unmanaged group; review ownership')
    block=BEGIN+'\n'+robots_body(p,old)+END+'\n'
    return old+('' if not old or old.endswith('\n') else '\n')+block

def matrix(p,robots):
    result=[]
    for row in p['critical_urls']:
        check=evaluate(robots,row['url'],row['token'],row['profile'])
        item={'url':row['url'],'token':row['token'],'robots':check,'expected_crawl':row['expected_crawl'],
              'ok':check['decision']==row['expected_crawl']}
        if row.get('expected_indexing'):
            cap=analyze(dict(row['capture'],crawler_token=row['token']))
            item['indexing_directive']=cap['indexing_directive']
            item['ok'] &= cap['indexing_directive']==row['expected_indexing']
            if row['expected_indexing']=='blocked' and check['decision']!='allow':
                item['ok']=False; item['conflict']='noindex_cannot_be_observed_when_crawl_blocked'
        result.append(item)
    return result

def plan(p,root):
    errors=validate(p)
    if errors: raise ValueError(canonical(errors))
    root=Path(root).resolve()
    if not root.is_dir(): raise ValueError('root must be an existing local directory')
    existing=read(local_path(root,'robots.txt'))
    robots=candidate_robots(p,existing)
    checks=matrix(p,robots)
    if any(not x['ok'] for x in checks): raise ValueError('critical matrix failed: '+canonical(checks))
    files=[{'path':'robots.txt','before':existing,'after':robots}]
    llms=p.get('llms',{})
    if llms.get('enabled'):
        desc=llms['description']
        # This is an authored public description, not an automated extraction of facts.
        content='# '+urlsplit(p['origin']).hostname+'\n\n'+desc+'\n\n## Resources\n\n'
        content+='\n'.join('- ['+x['title'].replace('[','').replace(']','').replace('\n',' ')+']('+x['url']+')' for x in llms['links'])+'\n'
        files.append({'path':'llms.txt','before':read(local_path(root,'llms.txt')),'after':content})
    for f in files:
        f['before_hash']=digest(f['before']); f['after_hash']=digest(f['after'])
    access_changes=[{'url':r['url'],'token':r['token'],'before':evaluate(existing or '',r['url'],r['token'],r['profile'])['decision'],'after':c['robots']['decision']} for r,c in zip(p['critical_urls'],checks)]
    doc={'schema_version':1,'root':str(root),'environment':p['environment'],'policy':p,'files':files,'matrix':checks,'access_changes':access_changes,
         'limits':['local_files_only','no_CMS_CDN_header_apply','capture_is_not_live_verification','semantic_fact_review_required']}
    doc['plan_id']=hashlib.sha256(canonical(doc).encode()).hexdigest()
    return doc

def validate_plan(doc):
    if not isinstance(doc,dict) or not isinstance(doc.get('files'),list): raise ValueError('invalid plan')
    signed={k:v for k,v in doc.items() if k!='plan_id'}
    if hashlib.sha256(canonical(signed).encode()).hexdigest()!=doc.get('plan_id'): raise ValueError('plan integrity mismatch; rebuild plan')
    errors=validate(doc['policy'])
    if errors: raise ValueError('invalid embedded policy')
    if doc.get('environment')!=doc['policy']['environment']: raise ValueError('environment mismatch')
    paths=[f.get('path') for f in doc['files']]
    if len(paths)!=len(set(paths)) or 'robots.txt' not in paths: raise ValueError('invalid artifact set')
    for f in doc['files']:
        local_path(doc['root'],f['path'])
        if not isinstance(f['after'],str) or f['before'] is not None and not isinstance(f['before'],str): raise ValueError('invalid artifact content')
        if digest(f['before'])!=f['before_hash'] or digest(f['after'])!=f['after_hash']: raise ValueError('artifact hash mismatch')
    robots=next(f['after'] for f in doc['files'] if f['path']=='robots.txt')
    if any(not row['ok'] for row in matrix(doc['policy'],robots)): raise ValueError('embedded critical matrix failed')

def verify(doc):
    validate_plan(doc)
    rows=[]
    for f in doc['files']:
        got=digest(read(local_path(doc['root'],f['path'])))
        rows.append({'path':f['path'],'expected_hash':f['after_hash'],'actual_hash':got,'ok':got==f['after_hash']})
    return {'ok':all(r['ok'] for r in rows),'files':rows,'scope':'local_files_not_live_site'}

def apply(doc, environment, rollback=False):
    validate_plan(doc)
    if environment!=doc['environment']: raise ValueError('explicit environment must match plan')
    # Caller must serialize writers; optimistic checks catch preexisting drift.
    expected='after' if rollback else 'before'; target='before' if rollback else 'after'
    root=Path(doc['root'])
    lock=root/'.seo-geo-policy.lock'
    try:
        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError: raise ValueError('policy writer lock exists; investigate before retrying')
    os.close(fd)
    changed=[]
    try:
        current={f['path']:read(local_path(root,f['path'])) for f in doc['files']}
        if all(digest(current[f['path']])==f[target+'_hash'] for f in doc['files']):
            return {'ok':True,'changed':[],'status':'already_at_target','scope':'local_files_only'}
        for f in doc['files']:
            if digest(current[f['path']])!=f[expected+'_hash']: raise ValueError('concurrent drift: '+f['path'])
        for f in doc['files']:
            if f[expected]==f[target]: continue
            path=local_path(root,f['path'])
            if digest(read(path))!=f[expected+'_hash']: raise ValueError('concurrent drift before write: '+f['path'])
            if f[target] is None:
                path.unlink()
            else: atomic(path,f[target])
            changed.append(f['path'])
        return {'ok':True,'changed':changed,'status':'rolled_back' if rollback else 'applied','scope':'local_files_only'}
    except Exception:
        # Compensate only own completed writes; do not overwrite subsequent external changes.
        for name in reversed(changed):
            f=next(x for x in doc['files'] if x['path']==name); path=local_path(root,name)
            if digest(read(path))!=f[target+'_hash']: continue
            if current[name] is None: path.unlink(missing_ok=True)
            else: atomic(path,current[name])
        raise
    finally:
        lock.unlink(missing_ok=True)
