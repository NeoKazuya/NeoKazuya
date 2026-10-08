#!/usr/bin/env python3
"""Publish aggregate GitHub activity; never persist commit messages or PR details."""
import argparse, datetime as dt, html, json, os, subprocess
from pathlib import Path
from zoneinfo import ZoneInfo
UTC = dt.timezone.utc

def api(path):
    result = subprocess.run(['gh', 'api', path], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('GitHub request failed; existing card preserved. Check access and rate limits.')
    return json.loads(result.stdout)

def pages(path):
    page = 1
    while True:
        batch = api(path + ('&' if '?' in path else '?') + f'per_page=100&page={page}')
        if not isinstance(batch, list): raise RuntimeError('Unexpected API response')
        yield batch
        if len(batch) < 100: break
        page += 1

def instant(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00'))

def count_prs(batches, login, start, end):
    count = 0
    for batch in batches:
        for p in batch:
            if p.get('merged_at') and p['user']['login'].lower() == login.lower() and start <= instant(p['merged_at']) < end:
                count += 1
        # Endpoint sorted by updated_at descending; no older PR can have merged in range.
        if batch and instant(batch[-1]['updated_at']) < start: break
    return count

def search_items(kind, query):
    """Discover external contributions without silently truncating GitHub search."""
    from urllib.parse import urlencode
    page = 1
    while True:
        result = api('/search/' + kind + '?' + urlencode({'q':query,'per_page':100,'page':page}))
        if result.get('incomplete_results') or result['total_count'] > 1000:
            raise RuntimeError('Contribution discovery incomplete; previous card preserved.')
        yield from result['items']
        if page * 100 >= result['total_count']: break
        page += 1

def discover_repos(login, start, end):
    # Membership covers direct commits, including private collaborator/org work.
    repos = {r['full_name'].lower():r for batch in pages(
        '/user/repos?affiliation=owner,collaborator,organization_member&sort=full_name') for r in batch}
    # Search also finds public upstream contributions without collaborator access.
    # Broad UTC dates discover candidates; exact timezone boundaries are applied later.
    dates = start.astimezone(UTC).date().isoformat() + '..' + end.astimezone(UTC).date().isoformat()
    names = set()
    for pr in search_items('issues', f'is:pr author:{login} is:merged merged:{dates}'):
        names.add(pr['repository_url'].split('/repos/',1)[1])
    for commit in search_items('commits', f'author:{login} committer-date:{dates}'):
        names.add(commit['repository']['full_name'])
    for name in sorted(names):
        if name.lower() not in repos: repos[name.lower()] = api('/repos/' + name)
    return list(repos.values())

def collect(login, start, end):
    from urllib.parse import urlencode
    rows = []
    for repo in discover_repos(login, start, end):
        if repo['full_name'].lower() == f'{login}/{login}'.lower(): continue
        if repo.get('size', 0) == 0: continue
        base = '/repos/' + repo['full_name']
        prs = count_prs(pages(base + '/pulls?state=closed&sort=updated&direction=desc'), login, start, end)
        query = urlencode({'author':login, 'since':start.isoformat(), 'until':end.isoformat()})
        commits = set()
        for batch in pages(base+'/commits?'+query):
            for c in batch:
                date = c['commit']['committer']['date']
                if start <= instant(date) < end: commits.add(c['sha'])
        if prs or commits:
            rows.append({'name':repo['name'], 'repository':repo['full_name'], 'description':repo.get('description') or '', 'prs':prs, 'commits':len(commits)})
    # Distinguish identically named projects owned by different people.
    from collections import Counter
    names = Counter(p['name'].casefold() for p in rows)
    for p in rows:
        if names[p['name'].casefold()] > 1: p['name'] = p['repository']
    return rows

def rank(rows):
    return sorted(rows, key=lambda p: (-p['prs'], -p['commits'], p['name'].casefold()))

def render(rows, label, generated, theme='dark'):
    bg, fg, sub, border, green, blue = ('#101419','#d6dde5','#99a5b3','#29313b','#8cd5ac','#9bbef5') if theme=='dark' else ('#f6f8fa','#24292f','#57606a','#d0d7de','#176f40','#1756a9')
    shown = rank(rows)[:3]; rest=rank(rows)[3:]
    height = 165 + max(1,len(shown))*65 + (37 if rest else 0)
    s=[f'<svg xmlns="http://www.w3.org/2000/svg" width="768" height="{height}" viewBox="0 0 768 {height}" role="img" aria-labelledby="title desc">', '<title id="title">Weekly GitHub build log</title>',f'<desc id="desc">{html.escape(label)}. {sum(p["prs"] for p in rows)} authored PRs merged; {sum(p["commits"] for p in rows)} authored commits on default branches. Top three active projects.</desc>', f'<rect x=".5" y=".5" width="767" height="{height-1}" rx="8" fill="{bg}" stroke="{border}"/>']
    def text(x,y,value,size=14,color=None,weight=400):
        s.append(f'<text x="{x}" y="{y}" fill="{color or fg}" font-family="Menlo,Consolas,monospace" font-size="{size}" font-weight="{weight}">{html.escape(str(value))}</text>')
    def line(y):s.append(f'<path d="M28 {y} H740" stroke="{border}"/>')
    def trim(v,n):
        v=' '.join(v.split())
        return v if len(v)<=n else v[:n-1]+'…'
    text(28,35,'last 7 days',14,sub);text(577,35,label,12,sub)
    text(28,70,f'{sum(p["prs"] for p in rows):,} PRs merged',18,green,600)
    text(290,70,f'{sum(p["commits"] for p in rows):,} commits',18,blue,600)
    text(575,70,f'{len(rows)} projects',16)
    line(91)
    for i,p in enumerate(shown):
        y=125+i*65
        text(28,y,trim(p['name'],39),15)
        text(474,y,f'{p["prs"]:>3,} PRs',14,green)
        text(599,y,f'{p["commits"]:,} commits',14,blue)
        if p['description']:text(28,y+24,trim(p['description'],80),12,sub)
    if not shown:text(28,131,'No recorded activity in this window.',14,sub)
    bottom=108+max(1,len(shown))*65
    line(bottom)
    if rest:
        text(28,bottom+27,f'+ {len(rest)} other projects',13,sub)
        text(474,bottom+27,f'{sum(p["prs"] for p in rest):>3,} PRs',14,green)
        text(599,bottom+27,f'{sum(p["commits"] for p in rest):,} commits',14,blue)
    text(28,height-18,f'updated {generated} · America/Los_Angeles',10,sub)
    s.append('</svg>')
    return ''.join(s)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--login',default='NeoKazuya');parser.add_argument('--date');parser.add_argument('--data');args=parser.parse_args()
    zone=ZoneInfo('America/Los_Angeles');now=dt.datetime.now(zone)
    today=dt.date.fromisoformat(args.date) if args.date else now.date()
    start=dt.datetime.combine(today-dt.timedelta(days=6),dt.time(),zone)
    end=dt.datetime.combine(today+dt.timedelta(days=1),dt.time(),zone)
    rows=json.loads(Path(args.data).read_text()) if args.data else collect(args.login,start,end)
    label=f'{start.strftime("%b")} {start.day}–{today.strftime("%b")} {today.day}'
    generated=now.strftime('%Y-%m-%d %H:%M')
    root=Path(__file__).resolve().parents[1];out=root/'assets';out.mkdir(exist_ok=True)
    rendered={theme:render(rows,label,generated,theme) for theme in ['dark','light']}
    for theme,svg in rendered.items():
        tmp=out/f'activity-{theme}.tmp';tmp.write_text(svg);tmp.replace(out/f'activity-{theme}.svg')
    # This report is aggregate-only; raw API responses are never written.
    (root/'activity.json').write_text(json.dumps({'period':label,'projects':rank(rows)},indent=2)+'\n')
    print(f'Updated {len(rows)} active projects, {sum(p["prs"] for p in rows)} merged PRs, {sum(p["commits"] for p in rows)} commits.')
if __name__=='__main__':main()
