import logging, os

import httpretty
from pelican.generators import ArticlesGenerator
from pelican.tests.support import get_settings

from linkbacks import process_all_articles_linkbacks, CACHE_FILENAME, LOGGER


CUR_DIR = os.path.dirname(__file__)
TEST_CONTENT_DIR = os.path.join(CUR_DIR, 'test_content')


def setup():
    LOGGER.setLevel(logging.DEBUG)


@httpretty.activate
def test_ok(tmpdir):
    _setup_ok_http_mocks()
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    linkback_requests_made, linkback_requests_successful = process_all_articles_linkbacks([article_generator])
    assert linkback_requests_made == 1
    assert linkback_requests_successful == 1

@httpretty.activate
def test_cache(tmpdir, caplog):
    _setup_ok_http_mocks()
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    linkback_requests_made, linkback_requests_successful = process_all_articles_linkbacks([article_generator])
    assert linkback_requests_made == 1
    assert linkback_requests_successful == 1
    linkback_requests_made, linkback_requests_successful = process_all_articles_linkbacks([article_generator])
    assert linkback_requests_made == 0
    assert 'Link url http://localhost/sub/some-page.html skipped because it has already been processed (present in cache)' in caplog.text

def test_ignore_internal_links(tmpdir, caplog):
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir, site_url='http://localhost/sub/')
    linkback_requests_made, _ = process_all_articles_linkbacks([article_generator])
    assert linkback_requests_made == 0
    assert 'Link url http://localhost/sub/some-page.html skipped because is starts with http://localhost/sub/' in caplog.text

def test_link_host_not_reachable(tmpdir, caplog):
    article_generator = _build_article_generator(TEST_CONTENT_DIR, tmpdir)
    linkback_requests_made, linkback_requests_successful = process_all_articles_linkbacks([article_generator])
    assert linkback_requests_made == 1
    assert linkback_requests_successful == 0
    assert 'Failed to send webmention for link url http://localhost/sub/some-page.html: exception during GET request' in caplog.text
    # Better assertion, pending https://github.com/bear/ronkyuu/pull/25 :
    # assert 'Failed to send webmention for link url http://localhost/sub/some-page.html: exception during POST request: ConnectionError' in caplog.text


def _setup_ok_http_mocks():
    # Webmention:
    httpretty.register_uri(
        httpretty.GET, 'http://localhost/sub/some-page.html',
        adding_headers={'Link': '<http://localhost/sub/webmention-endpoint>; rel="webmention"'},
        body='Dummy linked content'
    )
    httpretty.register_uri(
        httpretty.POST, 'http://localhost/sub/webmention-endpoint',
        body='http://localhost/sub/alice.host/webmentions/222'
    )

def _build_article_generator(content_path, tmpdir, site_url='http://localhost/blog/'):
    settings = get_settings(filenames={})
    _setup_cache_dir(settings['CACHE_PATH'])
    settings['SITEURL'] = site_url
    context = settings.copy()
    context['generated_content'] = dict()
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
