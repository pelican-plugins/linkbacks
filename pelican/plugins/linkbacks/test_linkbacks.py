import json, logging, os

import httpretty
from pelican.generators import ArticlesGenerator
from pelican.tests.support import get_settings
import pytest

from linkbacks import (
    process_all_articles_linkbacks,
    Cache,
    LinkbackConfig,
    CACHE_FILENAME,
    MAX_RESPONSE_LENGTH,
)

CUR_DIR = os.path.dirname(__file__)
TEST_CONTENT_DIR = os.path.join(CUR_DIR, 'test_content')

@httpretty.activate
def test_ok(tmpdir):
    _setup_http_mocks()
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    assert process_all_articles_linkbacks([article_generator]) == 2

@httpretty.activate
def test_ok_zero_linkbacks(tmpdir):
    _setup_http_mocks(pingback=(), webmention=())
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    assert process_all_articles_linkbacks([article_generator]) == 0

@httpretty.activate
def test_cache(tmpdir, caplog):
    caplog.set_level(logging.DEBUG)
    _setup_http_mocks()
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    assert process_all_articles_linkbacks([article_generator]) == 2
    assert process_all_articles_linkbacks([article_generator]) == 0
    assert 'Link url http://localhost/sub/some-page.html skipped because it is present in cache with status: ALREADY SUBMITTED' in caplog.text

def test_ignore_internal_links(tmpdir, caplog):
    caplog.set_level(logging.DEBUG)
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir, site_url='http://localhost/sub/')
    assert process_all_articles_linkbacks([article_generator]) == 0
    assert 'Link url http://localhost/sub/some-page.html skipped because is starts with http://localhost/sub/' in caplog.text

def test_link_host_not_reachable(tmpdir, caplog):
    caplog.set_level(logging.DEBUG)
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    assert process_all_articles_linkbacks([article_generator]) == 0
    assert 'Failed to retrieve web page for link url http://localhost/sub/some-page.html' in caplog.text
    assert 'Failed to retrieve web page for link url http://localhost/sub/some-page.html' in caplog.text
    assert 'ConnectionError' in caplog.text

@httpretty.activate
def test_pingback_ok_without_http_header(tmpdir):
    _setup_http_mocks(pingback=('link',), webmention=())
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    assert process_all_articles_linkbacks([article_generator]) == 1

@httpretty.activate
def test_webmention_ok_without_http_header(tmpdir):
    _setup_http_mocks(pingback=(), webmention=('link',))
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    assert process_all_articles_linkbacks([article_generator]) == 1

@httpretty.activate
def test_pingback_http_error(tmpdir, caplog):
    _setup_http_mocks(pingback=('header', 'http_error'), webmention=())
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    assert process_all_articles_linkbacks([article_generator]) == 0
    assert 'Failed to send pingback for link url http://localhost/sub/some-page.html' in caplog.text
    assert '503' in caplog.text

@httpretty.activate
def test_pingback_xmlrpc_error(tmpdir, caplog):
    _setup_http_mocks(pingback=('header', 'xmlrpc_error'), webmention=())
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    assert process_all_articles_linkbacks([article_generator]) == 0
    assert 'Pingback XML-RPC request failed for URL http://localhost/sub/some-page.html: code=0 - Unexpected error.' in caplog.text

@httpretty.activate
def test_pingback_already_registered(tmpdir, caplog):
    _setup_http_mocks(pingback=('header', 'already_registered'), webmention=())
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    assert process_all_articles_linkbacks([article_generator]) == 0
    assert 'Pingback already registered for URL http://localhost/sub/some-page.html, XML-RPC response: code=48 - The pingback has already been registered.' in caplog.text

@httpretty.activate
def test_webmention_http_error(tmpdir, caplog):
    _setup_http_mocks(pingback=(), webmention=('header', 'http_error'))
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    assert process_all_articles_linkbacks([article_generator]) == 0
    assert 'Failed to send webmention for link url http://localhost/sub/some-page.html' in caplog.text
    assert '503' in caplog.text

@httpretty.activate
def test_response_too_big_and_link_in_header(tmpdir, caplog):
    caplog.clear()
    _setup_http_mocks(pingback=('header',), webmention=(), fat_html=True)
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    assert process_all_articles_linkbacks([article_generator]) == 1
    assert 'The response for URL http://localhost/sub/some-page.html was too large, and hence was truncated' in caplog.text

@httpretty.activate
def test_response_too_big_and_link_in_html(tmpdir, caplog):
    caplog.clear()
    _setup_http_mocks(pingback=('link',), webmention=(), fat_html=True)
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    assert process_all_articles_linkbacks([article_generator]) == 1

def _setup_http_mocks(pingback=('header', 'link'), webmention=('header', 'link'), fat_html=False):
    headers = {'Content-Type': 'text/html'}
    if 'header' in pingback:
        headers['X-Pingback'] = 'http://localhost/sub/pingback-endpoint'
    if 'header' in webmention:
        headers['Link'] = '<http://localhost/sub/webmention-endpoint>; rel="webmention"'
    httpretty.register_uri(
        httpretty.GET, 'http://localhost/sub/some-page.html',
        adding_headers=headers,
        body=_build_html_content(pingback, webmention, fat_html)
    )
    # Pingback endpoint:
    xmlrpc_body = _build_xmlrpc_success('Pingback registered. Keep the web talking! :-)')
    if 'already_registered' in pingback:
        xmlrpc_body = _build_xmlrpc_error(fault_code=48, fault_string='The pingback has already been registered.')
    if 'xmlrpc_error' in pingback:
        xmlrpc_body = _build_xmlrpc_error(fault_code=0, fault_string='Unexpected error.')
    httpretty.register_uri(
        httpretty.POST, 'http://localhost/sub/pingback-endpoint',
        body=xmlrpc_body,
        status=503 if 'http_error' in pingback else 200,
    )
    # Webmention endpoint:
    httpretty.register_uri(
        httpretty.POST, 'http://localhost/sub/webmention-endpoint',
        body='http://localhost/sub/webmentions/222',
        status=503 if 'http_error' in webmention else 200,
    )

def _build_html_content(pingback, webmention, fat_html=False):
    pingback_link = '<link rel="pingback" href="http://localhost/sub/pingback-endpoint">' if 'link' in pingback else ''
    webmention_link = '<link rel="webmention" href="http://localhost/sub/webmention-endpoint">' if 'link' in webmention else ''
    extra_body = 'X'*MAX_RESPONSE_LENGTH if fat_html else ''
    return f'''<!DOCTYPE html>
    <html lang="en-US">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width">
        <title>Dummy linkback test page</title>
        {pingback_link}
        {webmention_link}
    </head>
    <body>
    Dummy linked content {extra_body}
    </body>'''

def _build_xmlrpc_success(message):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
    <methodResponse><params>
        <param><value><string>{message}</string></value></param>
    </params></methodResponse>'''

def _build_xmlrpc_error(fault_code, fault_string):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
    <methodResponse><fault>
        <value><struct>
            <member><name>faultCode</name><value><int>{fault_code}</int></value></member>
            <member><name>faultString</name><value><string>{fault_string}</string></value></member>
        </struct></value>
    </fault></methodResponse>'''

def _build_article_generator(content_path, tmpdir, site_url='http://localhost/blog/'):
    settings = get_settings(filenames={})
    _setup_cache_dir(settings['CACHE_PATH'])
    settings['SITEURL'] = site_url
    context = settings.copy()
    context['generated_content'] = {}
    context['static_links'] = set()
    article_generator = ArticlesGenerator(
        context=context, settings=settings,
        path=content_path, theme=settings['THEME'], output_path=str(tmpdir))
    article_generator.generate_context()
    return article_generator

def _setup_cache_dir(cache_dir_path):
    if not os.path.isdir(cache_dir_path):
        os.mkdir(cache_dir_path)
    try:
        os.remove(os.path.join(cache_dir_path, CACHE_FILENAME))
    except FileNotFoundError:
        pass

def test_cache_load_old_format(tmpdir):
    with (tmpdir / CACHE_FILENAME).open("w") as cache_file:
        json.dump({
            "festival-meujeuteries-merveilles": [
                "https://laubergedesreveurs.fr/festival-meujeuterie-et-merveilles/"
            ]
        }, cache_file)
    config = LinkbackConfig({'CACHE_PATH': str(tmpdir)})
    with pytest.raises(EnvironmentError) as error:
        Cache.load_from_json(config)
    assert "Old cache format detected" in str(error)

def test_cache_load_new_format(tmpdir):
    with (tmpdir / CACHE_FILENAME).open("w") as cache_file:
        json.dump({
            "more-amazing-creative-commons-artists": {
                "https://creativecommons.org/share-your-work/cclicenses/": {
                    "pingback": {
                        "error": "No pingback URI found"
                    },
                    "webmention": {
                        "error": "No webmention URI found"
                    }
                }
            }
        }, cache_file)
    config = LinkbackConfig({'CACHE_PATH': str(tmpdir)})
    cache = Cache.load_from_json(config)
    assert cache.get_status("more-amazing-creative-commons-artists", "https://creativecommons.org/share-your-work/cclicenses/") == "No pingback URI found"
