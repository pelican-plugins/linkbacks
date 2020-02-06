from datetime import datetime
import json, logging, os

from bs4 import BeautifulSoup
from pelican import signals
from pelican.generators import ArticlesGenerator
from ronkyuu.webmention import sendWebmention


CACHE_FILENAME = 'pelican-plugin-linkbacks.json'
DEFAULT_USER_AGENT = 'pelican-plugin-linkbacks'
TIMEOUT = 3

LOGGER = logging.getLogger(__name__)

def process_all_articles_linkbacks(generators):
    'Just to ease testing, returns (linkback_requests_made, linkback_requests_successful)'
    start_time = datetime.now()
    article_generator = next(g for g in generators if isinstance(g, ArticlesGenerator))

    settings = article_generator.settings
    cache_filepath = os.path.join(settings.get('CACHE_PATH'), CACHE_FILENAME)

    # Loading the cache:
    try:
        with open(cache_filepath) as cache_file:
            cache = json.load(cache_file)
    except FileNotFoundError:
        cache = {}

    linkback_requests_made, linkback_requests_successful = 0, 0
    for article in article_generator.articles:
        if article.status == 'published':
            made, successful = process_all_links_of_an_article(article, cache, settings)
            linkback_requests_made += made
            linkback_requests_successful += successful

    # Saving the cache:
    with open(cache_filepath, 'w+') as cache_file:
        json.dump(cache, cache_file)

    LOGGER.info("Execution took: %s", datetime.now() - start_time)
    return linkback_requests_made, linkback_requests_successful

def process_all_links_of_an_article(article, cache, settings):
    siteurl = settings.get('SITEURL', '')
    source_url = os.path.join(siteurl, article.url)
    user_agent = settings.get('LINKBACKS_USERAGENT', DEFAULT_USER_AGENT)
    linkback_requests_made, linkback_requests_successful = 0, 0
    links_cache = set(cache.get(article.slug, []))
    doc_soup = BeautifulSoup(article.content, 'html.parser')
    for anchor in doc_soup('a'):
        if 'href' not in anchor.attrs:
            continue
        link_url = anchor['href']
        if not link_url.startswith('http'):
            continue
        if siteurl and link_url.startswith(siteurl):
            LOGGER.debug("Link url %s skipped because is starts with %s", link_url, siteurl)
            continue
        if link_url in links_cache:
            LOGGER.debug("Link url %s skipped because it has already been processed (present in cache)", link_url)
            continue
        for notifier in (send_webmention,): # send_pingback, send_trackback
            linkback_requests_made += 1
            if notifier(source_url, link_url, user_agent):
                linkback_requests_successful += 1
        links_cache.add(link_url)
    cache[article.slug] = list(links_cache)
    return linkback_requests_made, linkback_requests_successful

def send_webmention(source_url, target_url, user_agent):
    response, debug_output = sendWebmention(source_url, target_url, test_urls=False, debug=True,
                                            headers={'User-Agent': user_agent}, timeout=TIMEOUT)
    if response:
        LOGGER.info("Webmention notification sent for URL %s, endpoint response: %s", target_url, response.text)
        return True
    LOGGER.error("Failed to send webmention for link url %s: %s", target_url, ' - '.join(debug_output))
    return False

# pylint: disable=unused-argument
def send_pingback(source_url, target_url, user_agent):
    pass  # Not implemented yet

def send_trackback(source_url, target_url, user_agent):
    pass  # Not implemented yet


def register():
    signals.all_generators_finalized.connect(process_all_articles_linkbacks)
