"""Stage the homepage hero and workspace entry without replacing local page sections."""
from pathlib import Path
import re
import sys


HERO = r'<div class="hero-copy">.*?(?=<div class="hero-visual">)'
TITLE = r'<title>[^<]+</title>'
DESCRIPTION = r'<meta name="description"[^>]+>'
SOCIAL_TITLE = r'<meta property="og:title"[^>]+>'
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
    for pattern in (HERO, TITLE, SOCIAL_TITLE):
        previous = replace(pattern, previous, release)
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
    for marker in ('id="account-screen"', 'id="account-form"', 'id="workspace"', 'id="public-status"'):
        if previous.count(marker) != 1:
            raise ValueError(f'Workspace entry requires one {marker}')
    return previous


if __name__ == '__main__':
    release, workspace, public, staged_public = map(Path, sys.argv[1:])
    try:
        updated_app = stage_workspace(workspace.read_text(), (release / 'app/static/index.html').read_text())
        updated_public = stage_public(public.read_text(), (release / 'public/index.html').read_text())
    except ValueError as exc:
        raise SystemExit(f'Could not stage entry pages: {exc}') from None
    workspace.write_text(updated_app)
    staged_public.write_text(updated_public)
