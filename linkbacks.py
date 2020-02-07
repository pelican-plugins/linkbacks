from datetime import datetime
import json, logging, os, xmlrpc.client
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pelican import signals
from pelican.generators import ArticlesGenerator
import requests
from requests.utils import parse_header_links


BS4_HTML_PARSER = 'html.parser'  # Alt: 'html5lib', 'lxml', 'lxml-xml'
CACHE_FILENAME = 'pelican-plugin-linkbacks.json'
DEFAULT_USER_AGENT = 'pelican-plugin-linkbacks'
TIMEOUT = 3
WEBMENTION_POSS_REL = ('webmention', 'http://webmention.org', 'http://webmention.org/', 'https://webmention.org', 'https://webmention.org/')

LOGGER = logging.getLogger(__name__)


def process_all_articles_linkbacks(generators):
    'Just to ease testing, returns the number of notifications successfully sent'
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

    successful_notifs_count = 0
    for article in article_generator.articles:
        if article.status == 'published':
            successful_notifs_count += process_all_links_of_an_article(article, cache, settings)

    # Saving the cache:
    with open(cache_filepath, 'w+') as cache_file:
        json.dump(cache, cache_file)

    LOGGER.info("Execution took: %s", datetime.now() - start_time)
    return successful_notifs_count

def process_all_links_of_an_article(article, cache, settings):
    siteurl = settings.get('SITEURL', '')
    source_url = os.path.join(siteurl, article.url)
    user_agent = settings.get('LINKBACKS_USERAGENT', DEFAULT_USER_AGENT)
    successful_notifs_count = 0
    links_cache = set(cache.get(article.slug, []))
    doc_soup = BeautifulSoup(article.content, BS4_HTML_PARSER)
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
        for notifier in (send_pingback, send_webmention): #, send_trackback):
            if notifier(source_url, link_url, user_agent):
                successful_notifs_count += 1
        links_cache.add(link_url)
    cache[article.slug] = list(links_cache)
    return successful_notifs_count

def send_pingback(source_url, target_url, user_agent):
    try:
        # Pingback server autodiscovery:
        response = requests.get(target_url, headers={'User-Agent': user_agent}, timeout=TIMEOUT)
        response.raise_for_status()
        server_uri = response.headers.get('X-Pingback')
        if not server_uri:  # As a falback, we try parsing the HTML, looking for <link> elements
            doc_soup = BeautifulSoup(response.text, BS4_HTML_PARSER)
            link = doc_soup.find(rel='pingback', href=True)
            if link:
                server_uri = link['href']
        if not server_uri:
            return False
        # Performing pingback request:
        xml_rpc_client = xmlrpc.client.ServerProxy(server_uri, transport=XmlRpcClient(user_agent))
        try:
            response = xml_rpc_client.pingback.ping(source_url, target_url)
        except xmlrpc.client.Fault as fault:
            if fault.faultCode == 48:  # pingback already registered
                LOGGER.debug("Pingback already registered, XML-RPC response: code=%s - %s", fault.faultCode, fault.faultString)
            else:
                LOGGER.error("Pingback XML-RPC request failed: code=%s - %s", fault.faultCode, fault.faultString)
            return False
        LOGGER.info("Pingback notification sent for URL %s, endpoint response: %s", target_url, response)
        return True
    except Exception:
        LOGGER.exception("Failed to send Pingback for link url %s", target_url)
        return False

def send_webmention(source_url, target_url, user_agent):
    try:
        # WebMention server autodiscovery:
        server_uri = None
        response = requests.get(target_url, headers={'User-Agent': user_agent}, timeout=TIMEOUT)
        response.raise_for_status()
        link_header = response.headers.get('Link')
        if link_header:
            try:
                server_uri = next(lh.get('url') for lh in parse_header_links(link_header)
                                  if lh.get('url') and lh.get('rel') in WEBMENTION_POSS_REL)
            except StopIteration:
                pass
        if not server_uri:  # As a falback, we try parsing the HTML, looking for <link> elements
            for link in BeautifulSoup(response.text, BS4_HTML_PARSER).find_all(rel=WEBMENTION_POSS_REL, href=True):
                if link.get('href'):
                    server_uri = link.get('href')
        if not server_uri:
            return False
        server_uri = urljoin(target_url, server_uri)
        # Performing WebMention request:
        response = requests.post(server_uri, headers={'User-Agent': user_agent}, timeout=TIMEOUT,
                                 data={'source': source_url, 'target': target_url})
        response.raise_for_status()
        LOGGER.info("WebMention notification sent for URL %s, endpoint response: %s", target_url, response.text)
        return True
    except Exception:
        LOGGER.exception("Failed to send WebMention for link url %s", target_url)
        return False

class XmlRpcClient(xmlrpc.client.Transport):
    def __init__(self, user_agent):
        super().__init__()
        # Shadows parent class attribute:
        self.user_agent = user_agent

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = TIMEOUT
        return conn

# pylint: disable=unused-argument
def send_trackback(source_url, target_url, user_agent):
    pass  # Not implemented yet

def register():
    signals.all_generators_finalized.connect(process_all_articles_linkbacks)
