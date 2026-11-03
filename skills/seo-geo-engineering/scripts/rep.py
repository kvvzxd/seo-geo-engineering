"""Bounded offline robots evaluator. No networking or claim of full engine parity."""
import re
from urllib.parse import urlsplit, quote

UNRESERVED = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~')

def normalize(value):
    def pct(match):
        n = int(match[0][1:], 16)
        return chr(n) if chr(n) in UNRESERVED else '%%%02X' % n
    value = re.sub(r'%[0-9a-fA-F]{2}', pct, value)
    return quote(value, safe="/%?:@!$&'()*+,;=-._~[]")

def parse(text):
    groups, agents, rules, diagnostics = [], [], [], []
    body_started = False
    for no, raw in enumerate(text.lstrip('\ufeff').splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if ':' not in line:
            diagnostics.append({'line': no, 'issue': 'invalid_line'})
            continue
        key, val = (s.strip() for s in line.split(':', 1))
        key = key.lower()
        if key == 'user-agent':
            if body_started:
                groups.append({'agents': agents, 'rules': rules})
                agents, rules, body_started = [], [], False
            if not val:
                diagnostics.append({'line': no, 'issue': 'empty_user_agent'})
            else:
                token=re.match(r'^[A-Za-z_-]+',val)
                if val=='*': agents.append('*')
                elif token: agents.append(token[0].lower())
                else: diagnostics.append({'line':no,'issue':'unsupported_user_agent_token'})
        elif key in ('allow', 'disallow'):
            body_started = True
            if not agents:
                diagnostics.append({'line': no, 'issue': 'rule_without_group'})
            elif val:
                if not val.startswith('/'):
                    diagnostics.append({'line': no, 'issue': 'non_absolute_path'})
                else:
                    rules.append({'directive': key, 'path': val, 'line': no})
        elif key not in ('sitemap', 'crawl-delay', 'clean-param', 'host'):
            diagnostics.append({'line': no, 'issue': 'unknown_directive', 'key': key})
    if agents:
        groups.append({'agents': agents, 'rules': rules})
    if len(text.encode('utf-8')) > 500 * 1024:
        diagnostics.append({'issue': 'exceeds_google_500KiB_limit'})
    return groups, diagnostics

def evaluate(text, url, token='Googlebot', profile='google'):
    groups, diagnostics = parse(text)
    if profile != 'google':
        return {'decision':'unknown', 'reason':'unsupported_engine_profile', 'diagnostics':diagnostics}
    if diagnostics:
        return {'decision':'unknown', 'reason':'unresolved_parse_diagnostics', 'diagnostics':diagnostics}
    target = urlsplit(url)
    path = normalize((target.path or '/') + ('?' + target.query if target.query else ''))
    token = token.lower()
    specific = [(len(a), g) for g in groups for a in g['agents'] if a != '*' and a == token]
    if specific:
        best = max(n for n, g in specific)
        selected = [g for n, g in specific if n == best]
    else:
        selected = [g for g in groups if '*' in g['agents']]
    matches = []
    for group in selected:
        for rule in group['rules']:
            pattern = normalize(rule['path'])
            terminal = pattern.endswith('$')
            body = pattern[:-1] if terminal else pattern
            if len(body)*len(path)>2_000_000:
                return {'decision':'unknown','reason':'matching_complexity_limit','diagnostics':[]}
            if wildcard_prefix(body,path,terminal):
                # Google's documented wildcard precedence and upstream priority
                # use the escaped pattern length, including wildcard markers.
                specificity = len(pattern.encode('utf-8'))
                matches.append((specificity, rule['directive'] == 'allow', rule))
    if not matches:
        return {'decision':'allow', 'reason':'no_matching_rule', 'groups':[g['agents'] for g in selected], 'diagnostics':[]}
    _, allowed, rule = max(matches, key=lambda x:(x[0], x[1]))
    return {'decision':'allow' if allowed else 'disallow', 'rule':rule, 'groups':[g['agents'] for g in selected], 'diagnostics':[]}

def wildcard_prefix(pattern,path,terminal):
    # Dynamic state matching avoids regex backtracking on untrusted robots input.
    reachable={0}
    for char in pattern:
        if char=='*':
            reachable=set(range(min(reachable),len(path)+1)) if reachable else set()
        else:
            reachable={pos+1 for pos in reachable if pos<len(path) and path[pos]==char}
        if not reachable: return False
    return len(path) in reachable if terminal else bool(reachable)
