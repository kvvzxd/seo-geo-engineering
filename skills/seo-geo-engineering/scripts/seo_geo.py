#!/usr/bin/env python3
"""SEO/GEO offline CLI. JSON in/out; local mutations are explicit commands."""
import argparse, csv, json, math, re, sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
import policy
from snapshot import analyze, compare, check_facts

def load(path):
    if Path(path).stat().st_size>32*1024*1024: raise ValueError('input exceeds 32MiB; split captures/exports')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def analytics(path,context):
    required=('engine','property','search_type','country','device','timezone')
    if not isinstance(context,dict) or any(not context.get(k) for k in required):
        raise ValueError('context requires engine, property, search_type, country, device, timezone; use explicit all for mixed filters')
    groups=defaultdict(lambda:{'clicks':0.0,'impressions':0.0,'position_weight':0.0,'position_impressions':0.0})
    dates=[]; count=0
    with open(path,encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if not {'date','page','query','clicks','impressions'}.issubset(reader.fieldnames or []):
            raise ValueError('CSV requires date,page,query,clicks,impressions; normalize export explicitly')
        for row in reader:
            dates.append(date.fromisoformat(row['date'])); count+=1
            for k in ('property','engine','country','device','search_type'):
                if k in row and context[k]!='all' and row[k]!=context[k]: raise ValueError('mixed context in CSV: '+k)
            if not row['page']: raise ValueError('empty page')
            c=float(row['clicks']); n=float(row['impressions'])
            if not all(math.isfinite(x) and x>=0 for x in (c,n)) or c>n: raise ValueError('invalid clicks/impressions')
            g=groups[(row['page'],row['query'])]; g['clicks']+=c; g['impressions']+=n
            if row.get('position'):
                pos=float(row['position'])
                if not math.isfinite(pos) or pos<1: raise ValueError('invalid position')
                g['position_weight']+=pos*n; g['position_impressions']+=n
    rows=[]
    for (page,query),g in sorted(groups.items()):
        rows.append({'page':page,'query':query,'clicks':g['clicks'],'impressions':g['impressions'],
                     'ctr':g['clicks']/g['impressions'] if g['impressions'] else None,
                     'impression_weighted_position':g['position_weight']/g['position_impressions'] if g['position_impressions'] else None})
    return {'context':context,'source_rows':count,'period':{'start':str(min(dates)),'end':str(max(dates))} if dates else None,
            'rows':rows,'limits':['export_coverage_only','no_causal_claim','position_is_impression_weighted_not_fixed_rank','query_anonymization_and_export_limits_not_recoverable']}

def geo(data,domain,brand):
    runs=data.get('runs') if isinstance(data,dict) else None
    if not isinstance(runs,list): raise ValueError('requires runs array')
    if not isinstance(brand,str) or not brand.strip(): raise ValueError('brand must be nonempty')
    target=urlsplit('https://'+domain)
    if target.hostname!=domain.lower() or target.path: raise ValueError('domain must be bare hostname')
    seen=set(); groups=defaultdict(list); invalid=[]
    for row in runs:
        if not isinstance(row,dict): raise ValueError('run must be object')
        rid=row.get('run_id')
        if not rid or rid in seen: raise ValueError('run_id must be unique and nonempty')
        seen.add(rid)
        fields=('query_id','query','engine','surface','language','country','timestamp','collection_method')
        if any(not isinstance(row.get(k),str) or not row[k] for k in fields) or not isinstance(row.get('answer'),str):
            invalid.append({'run_id':rid,'reason':'missing_metadata_or_answer'}); continue
        cited=row.get('cited_urls',[])
        if not isinstance(cited,list) or any(not isinstance(u,str) for u in cited): raise ValueError('cited_urls must be string array')
        sources=[]
        for url in cited:
            u=urlsplit(url)
            if u.scheme in ('http','https') and u.hostname and not u.username: sources.append(u.hostname.lower())
        host_match=lambda host: host==domain.lower() or host.endswith('.'+domain.lower())
        mentioned=bool(re.search(r'(?<!\w)'+re.escape(brand)+r'(?!\w)',row['answer'],re.I))
        entry=dict(row,has_sources=bool(sources),target_cited=any(host_match(s) for s in sources),brand_mentioned=mentioned)
        conditions={k:row.get(k,'unknown') for k in ('model','configuration_version','search_or_browsing_enabled','personalization_state','device_or_client')}
        groups[(row['engine'],row['surface'],row['language'],row['country'],row['collection_method'],policy.canonical(conditions))].append(entry)
    output=[]
    for key,rows in sorted(groups.items()):
        n=len(rows); cited=sum(r['target_cited'] for r in rows); source_n=sum(r['has_sources'] for r in rows)
        mention=sum(r['brand_mentioned'] for r in rows)
        checks=[c for r in rows for c in r.get('fact_checks',[])]
        if any(not isinstance(c,dict) or c.get('result') not in ('correct','incorrect','unknown') for c in checks): raise ValueError('fact_checks results must be correct/incorrect/unknown')
        reviewed=sum(c['result']!='unknown' for c in checks); errors=sum(c['result']=='incorrect' for c in checks)
        output.append({'engine':key[0],'surface':key[1],'language':key[2],'country':key[3],'collection_method':key[4],
          'conditions':json.loads(key[5]),'valid_runs':n,'unique_queries':len({r['query_id'] for r in rows}),
          'branded_query_runs':sum(bool(re.search(r'(?<!\w)'+re.escape(brand)+r'(?!\w)',r['query'],re.I)) for r in rows),
          'citation_presence':{'numerator':cited,'denominator':n,'rate':cited/n},
          'conditional_citation':{'numerator':cited,'denominator':source_n,'rate':cited/source_n if source_n else None},
          'brand_mentions':{'numerator':mention,'denominator':n,'rate':mention/n},
          'fact_errors':{'numerator':errors,'denominator':reviewed,'rate':errors/reviewed if reviewed else None,'unknown':len(checks)-reviewed},
          'query_repetitions':{q:sum(r['query_id']==q for r in rows) for q in sorted({r['query_id'] for r in rows})}})
    return {'groups':output,'invalid_runs':invalid,'total_input_runs':len(runs),
      'limits':['observed_panel_not_population','repetitions_are_not_independent_queries','citations_do_not_prove_recommendation_or_revenue','fact_checks_are_supplied_annotations','no_automated_confidence_interval_or_causal_claim']}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',help='JSON output path; otherwise stdout')
    sub=parser.add_subparsers(dest='command',required=True)
    for cmd in ('inspect','validate'):
        x=sub.add_parser(cmd); x.add_argument('input')
    x=sub.add_parser('facts'); x.add_argument('ledger'); x.add_argument('capture')
    x=sub.add_parser('compare'); x.add_argument('before'); x.add_argument('after')
    x=sub.add_parser('plan'); x.add_argument('input'); x.add_argument('--root',required=True)
    for cmd in ('apply','verify','drift','rollback'):
        x=sub.add_parser(cmd); x.add_argument('input')
        if cmd in ('apply','rollback'): x.add_argument('--environment',required=True,choices=['production','staging'])
    x=sub.add_parser('analytics'); x.add_argument('input'); x.add_argument('--context',required=True)
    x=sub.add_parser('geo'); x.add_argument('input'); x.add_argument('--domain',required=True); x.add_argument('--brand',required=True)
    args=parser.parse_args(argv)
    try:
        cmd=args.command
        if cmd=='inspect': result=analyze(load(args.input))
        elif cmd=='facts': result=check_facts(load(args.ledger),load(args.capture))
        elif cmd=='compare': result=compare(load(args.before),load(args.after))
        elif cmd=='validate':
            errors=policy.validate(load(args.input)); result={'ok':not errors,'errors':errors}
        elif cmd=='plan': result=policy.plan(load(args.input),args.root)
        elif cmd in ('verify','drift'): result=policy.verify(load(args.input))
        elif cmd in ('apply','rollback'): result=policy.apply(load(args.input),args.environment,cmd=='rollback')
        elif cmd=='analytics': result=analytics(args.input,load(args.context))
        elif cmd=='geo': result=geo(load(args.input),args.domain,args.brand)
        payload=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
        if args.out: policy.atomic(Path(args.out),payload)
        else: print(payload,end='')
        if result.get('ok') is False or result.get('flagged',0) or result.get('regressions'): return 2
        return 0
    except (ValueError,TypeError,KeyError,OSError) as e:
        print(json.dumps({'ok':False,'error':str(e)},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8')
    sys.exit(main())
