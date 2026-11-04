"""Stage homepage metadata, the hero, and the workspace entry."""
from pathlib import Path
import re
import sys


HERO = r'<div class="hero-copy">.*?(?=<div class="hero-visual">)'
TITLE = r'<title>[^<]+</title>'
DESCRIPTION = r'<meta name="description"[^>]+>'
PUBLIC_HEAD_TAGS = (
    TITLE, DESCRIPTION, r'<link rel="canonical"[^>]+>', r'<link rel="icon"[^>]+>',
    *(rf'<meta property="og:{field}"[^>]+>' for field in ('type', 'site_name', 'url', 'title', 'description')),
    r'<script id="beepnado-website-schema"[^>]*>.*?</script>',
)
GUIDE_LINK = r'<a href="/guides/check-website-downtime/">[^<]+</a>'
AGENCY_LINK = r'<a href="/website-monitoring-for-agencies/">[^<]+</a>'
FOOTER_CREDIT = r'<span data-reboot-credit>[^<]*<a[^>]+>[^<]+</a>[^<]*</span>'
ENTRY = r'<p id="app-loading".*?(?=<section id="account-screen")'
OLD_APP_MARKETING = r'<header class="nav">.*?</main>\s*(?=<section id="account-screen")'
APP_BACK = r'<button\b[^>]*\bid="account-back"[^>]*>.*?</button>'


def matched(pattern, text):
    results = list(re.finditer(pattern, text, flags=re.S))
    if len(results) != 1:
        raise ValueError(f'Expected one match for {pattern}; found {len(results)}')
    return results[0].group()


def replace(pattern, previous, release):
    return previous.replace(matched(pattern, previous), matched(pattern, release), 1)


def stage_public(previous, release):
    previous = replace(HERO, previous, release)
    head = matched(r'<head>.*?</head>', previous)
    updated = head
    for pattern in PUBLIC_HEAD_TAGS:
        if re.search(pattern, updated, re.S):
            updated = replace(pattern, updated, release)
        else:
            updated = updated.replace('</head>', '\n' + matched(pattern, release) + '\n</head>', 1)
    previous = previous.replace(head, updated, 1)
    if not re.search(GUIDE_LINK, previous):
        pricing_link = matched(r'<a href="#pricing">Pricing</a>(?=</span></footer>)', previous)
        previous = previous.replace(pricing_link + '</span></footer>',
                                    pricing_link + ' · ' + matched(GUIDE_LINK, release) + '</span></footer>', 1)
    if not re.search(AGENCY_LINK, previous):
        guide_link = matched(GUIDE_LINK + r'(?=</span></footer>)', previous)
        previous = previous.replace(guide_link + '</span></footer>',
                                    guide_link + ' · ' + matched(AGENCY_LINK, release) + '</span></footer>', 1)
    if not re.search(FOOTER_CREDIT, previous):
        footer = matched(r'<footer id="site-footer">.*?</footer>', previous)
        old_credit = '<span>Your websites. Our radar.</span>'
        if old_credit in footer:
            updated_footer = footer.replace(old_credit, matched(FOOTER_CREDIT, release), 1)
            previous = previous.replace(footer, updated_footer, 1)
    return previous


def stage_workspace(previous, release):
    if re.search(OLD_APP_MARKETING, previous, re.S):
        previous = previous.replace(matched(OLD_APP_MARKETING, previous), matched(ENTRY, release), 1)
    elif 'id="public-home"' in previous or 'id="app-loading"' not in previous:
        raise ValueError('Unrecognized BeepNado workspace entry')
    for pattern in (TITLE, DESCRIPTION, r'<h2 id="account-title">[^<]+</h2>',
                    r'<p id="account-subtitle">[^<]+</p>', r'<button[^>]+id="account-submit"[^>]*>[^<]+</button>'):
        previous = replace(pattern, previous, release)
    if re.search(APP_BACK, previous, re.S):
        link = matched(r'<a class="go-back"[^>]*>[^<]+</a>', release)
        previous = previous.replace(matched(APP_BACK, previous), link, 1)
    previous = re.sub(r'<meta property="og:[^>]+>\n?', '', previous)
    previous = re.sub(r'<footer id="site-footer">.*?</footer>\n?', '', previous, flags=re.S)
    for pattern in (r'<link rel="stylesheet" href="/static/monitor-theme\.css(?:\?[^"]*)?">',
                    r'<script src="/static/app\.js(?:\?[^"]*)?" defer></script>',
                    r'<script src="/static/monitor-view\.js(?:\?[^"]*)?" defer></script>'):
        previous = replace(pattern, previous, release)
    client_script = matched(r'<script src="/static/client-pages\.js" defer></script>', release)
    if client_script not in previous:
        anchor = matched(r'<script src="/static/monitor-view\.js(?:\?[^"]*)?" defer></script>', previous)
        previous = previous.replace(anchor, anchor + client_script, 1)
    alarm_script = matched(r'<script src="/static/outage-alarm\.js" defer></script>', release)
    if alarm_script not in previous:
        previous = previous.replace(client_script, client_script + alarm_script, 1)
    alarm_section = r'<section id="outage-alarm"[^>]*>.*?</section>'
    if re.search(alarm_section, previous, re.S):
        previous = replace(alarm_section, previous, release)
    else:
        anchor = matched(r'<div id="work-content"></div>', previous)
        previous = previous.replace(anchor, matched(alarm_section, release) + '\n' + anchor, 1)
    client_nav = matched(r'<button class="side-link" data-view="clients">[^<]+</button>', release)
    if client_nav not in previous:
        anchor = matched(r'<button class="side-link" data-view="billing">', previous)
        previous = previous.replace(anchor, client_nav + anchor, 1)
    for marker in ('id="account-screen"', 'id="account-form"', 'id="workspace"', 'id="public-status"'):
        if previous.count(marker) != 1:
            raise ValueError(f'Workspace entry requires one {marker}')
    return previous


if __name__ == '__main__':
    public_only = sys.argv[1] == '--public-only'
    try:
        if public_only:
            release, public, staged_public = map(Path, sys.argv[2:])
        else:
            release, workspace, public, staged_public = map(Path, sys.argv[1:])
        updated_public = stage_public(public.read_text(), (release / 'public/index.html').read_text())
        if not public_only:
            updated_app = stage_workspace(workspace.read_text(), (release / 'app/static/index.html').read_text())
            workspace.write_text(updated_app)
        staged_public.write_text(updated_public)
    except ValueError as exc:
        raise SystemExit(f'Could not stage entry pages: {exc}') from None
