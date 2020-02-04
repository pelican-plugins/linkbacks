from concurrent.futures import as_completed
import logging

from pelican import signals
from pelican.generators import ArticlesGenerator

import requests
from requests_futures.sessions import FuturesSession

from ronkyuu.webmention import sendWebmention


DEFAULT_USER_AGENT = 'pelican-plugin-linkbacks'
LOGGER = logging.getLogger(__name__)


def process_all_articles_linkbacks(generators):
    article_generator = next(g for g in generators if isinstance(g, ArticlesGenerator))
    settings = article_generator.settings
    user_agent = settings.get('LINKBACKS_USERAGENT', DEFAULT_USER_AGENT)
    with FuturesSession() as session:
        all_linkbacks_requests = []
        for article in article_generator.articles:
            if article.status == 'published':
                continue # TODO: parse with beautiful soup, cf. ronkyuu.webmentions.findMentions / https://github.com/silentlamb/pelican-deadlinks/blob/master/deadlinks.py
                article_links = []
                for link in article_links:
                    for sender in (send_pingback, send_trackback, send_webmention):
                        linkback_request = sender(session, link, user_agent)
                        if linkback_request:
                            all_linkbacks_requests.append(linkback_request)
        for future in as_completed(all_linkbacks_requests):
            print(future)


def send_pingback(session, source_url, target_url, user_agent):
    pass

def send_trackback(session, source_url, target_url, user_agent):
    pass

def send_webmention(session, source_url, target_url, user_agent):
    # TODO: inject session through mock.patch
    sendWebmention(source_url, target_url, test_urls=True, headers={'User-Agent': user_agent}, timeout=5)


def register():
    signals.all_generators_finalized.connect(process_all_articles_linkbacks)
