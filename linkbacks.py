from contextlib import closing
from datetime import datetime
import json, logging, os, xmlrpc.client
from os.path import splitext
from ssl import SSLError
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pelican import signals
from pelican.generators import ArticlesGenerator
import requests
from requests.exceptions import RequestException
from requests.utils import parse_header_links
from urllib3.exceptions import HTTPError


BS4_HTML_PARSER = 'html.parser'  # Alt: 'html5lib', 'lxml', 'lxml-xml'
CACHE_FILENAME = 'pelican-plugin-linkbacks.json'
DEFAULT_USER_AGENT = 'pelican-plugin-linkbacks'
TIMEOUT = 3
WEBMENTION_POSS_REL = ('webmention', 'http://webmention.org', 'http://webmention.org/', 'https://webmention.org', 'https://webmention.org/')

LOGGER = logging.getLogger(__name__)


def process_all_articles_linkbacks(generators):
    'Just to ease testing, returns the number of notifications successfully sent'
    root_logger_level = logging.root.level
    if root_logger_level > 0:  # inherit root logger level, if defined
        LOGGER.setLevel(root_logger_level)

    start_time = datetime.now()
    article_generator = next(g for g in generators if isinstance(g, ArticlesGenerator))

    settings = article_generator.settings
    cache_filepath = settings.get('LINKBACKS_CACHEPATH', os.path.join(settings.get('CACHE_PATH'), CACHE_FILENAME))

    try:
        with open(cache_filepath) as cache_file:
            cache = json.load(cache_file)
    except FileNotFoundError:
        cache = {}

    original_cache_links_count = sum(len(urls) for slug, urls in cache.items())
    successful_notifs_count = 0
    try:
        for article in article_generator.articles:
            if article.status == 'published':
                successful_notifs_count += process_all_links_of_an_article(article, cache, settings)
        return successful_notifs_count
    finally:  # We save the cache & log our progress even in case of an interruption:
        with open(cache_filepath, 'w+') as cache_file:
            json.dump(cache, cache_file)
        new_cache_links_count = sum(len(urls) for slug, urls in cache.items())
        LOGGER.info("Linkback plugin execution took: %s - Links processed & inserted in cache: %s - Successful notifications: %s",
                    datetime.now() - start_time, new_cache_links_count - original_cache_links_count, successful_notifs_count)

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
        if not link_url.startswith('http'):  # this effectively exclude relative links
            continue
        if siteurl and link_url.startswith(siteurl):
            LOGGER.debug("Link url %s skipped because is starts with %s", link_url, siteurl)
            continue
        if splitext(link_url)[1] in ('.gif', '.jpg', '.pdf', '.png', '.svg'):
            LOGGER.debug("Link url %s skipped because it appears to be an image or PDF file", link_url)
            continue
        if link_url in links_cache:
            LOGGER.debug("Link url %s skipped because it has already been processed (present in cache)", link_url)
            continue
        LOGGER.debug("Now attempting to send Linkbacks for link url %s", link_url)
        try:
            resp_content, resp_headers = requests_get_with_max_size(link_url, user_agent)
        except Exception as error:
            LOGGER.debug("Failed to retrieve web page for link url %s: [%s] %s", link_url, error.__class__.__name__, error)
            continue
        for notifier in (send_pingback, send_webmention): #, send_trackback):
            if notifier(source_url, link_url, resp_content, resp_headers):
                successful_notifs_count += 1
        links_cache.add(link_url)
    cache[article.slug] = list(links_cache)
    return successful_notifs_count

def send_pingback(source_url, target_url, resp_content=None, resp_headers=None, user_agent=None):
    try:
        if resp_content is None:
            resp_content, resp_headers = requests_get_with_max_size(target_url, user_agent)
        # Pingback server autodiscovery:
        server_uri = resp_headers.get('X-Pingback')
        if not server_uri and resp_headers.get('Content-Type', '').startswith('text/html'):
            # As a falback, we try parsing the HTML, looking for <link> elements
            doc_soup = BeautifulSoup(resp_content, BS4_HTML_PARSER)
            link = doc_soup.find(rel='pingback', href=True)
            if link:
                server_uri = link['href']
        if not server_uri:
            return False
        LOGGER.debug("Pingback URI detected: %s", server_uri)
        # Performing pingback request:
        transport = SafeXmlRpcTransport(user_agent, TIMEOUT) if server_uri.startswith('https') else XmlRpcTransport(user_agent, TIMEOUT)
        xml_rpc_client = xmlrpc.client.ServerProxy(server_uri, transport)
        try:
            response = xml_rpc_client.pingback.ping(source_url, target_url)
        except xmlrpc.client.Fault as fault:
            if fault.faultCode == 48:  # pingback already registered
                LOGGER.debug("Pingback already registered for URL %s, XML-RPC response: code=%s - %s", target_url, fault.faultCode, fault.faultString)
            else:
                LOGGER.error("Pingback XML-RPC request failed for URL %s: code=%s - %s", target_url, fault.faultCode, fault.faultString)
            return False
        LOGGER.info("Pingback notification sent for URL %s, endpoint response: %s", target_url, response)
        return True
    except (ConnectionError, HTTPError, RequestException, ResponseTooBig, SSLError) as error:
        LOGGER.error("Failed to send Pingback for link url %s: [%s] %s", target_url, error.__class__.__name__, error)
        return False
    except Exception:  # unexpected exception => we display the stacktrace:
        LOGGER.exception("Failed to send Pingback for link url %s", target_url)
        return False

def send_webmention(source_url, target_url, resp_content=None, resp_headers=None, user_agent=None):
    try:
        if resp_content is None:
            resp_content, resp_headers = requests_get_with_max_size(target_url, user_agent)
        # WebMention server autodiscovery:
        server_uri = None
        link_header = resp_headers.get('Link')
        if link_header:
            try:
                server_uri = next(lh.get('url') for lh in parse_header_links(link_header)
                                  if lh.get('url') and lh.get('rel') in WEBMENTION_POSS_REL)
            except StopIteration:
                pass
        if not server_uri and resp_headers.get('Content-Type', '').startswith('text/html'):
            # As a falback, we try parsing the HTML, looking for <link> elements
            for link in BeautifulSoup(resp_content, BS4_HTML_PARSER).find_all(rel=WEBMENTION_POSS_REL, href=True):
                if link.get('href'):
                    server_uri = link.get('href')
        if not server_uri:
            return False
        LOGGER.debug("WebMention URI detected: %s", server_uri)
        server_uri = urljoin(target_url, server_uri)
        # Performing WebMention request:
        response = requests.post(server_uri, headers={'User-Agent': user_agent}, timeout=TIMEOUT,
                                 data={'source': source_url, 'target': target_url})
        response.raise_for_status()
        LOGGER.info("WebMention notification sent for URL %s, endpoint response: %s", target_url, response.text)
        return True
    except (ConnectionError, HTTPError, RequestException, ResponseTooBig, SSLError) as error:
        LOGGER.error("Failed to send WebMention for link url %s: [%s] %s", target_url, error.__class__.__name__, error)
        return False
    except Exception:  # unexpected exception => we display the stacktrace:
        LOGGER.exception("Failed to send WebMention for link url %s", target_url)
        return False


GET_CHUNK_SIZE = 2**10
MAX_RESPONSE_LENGTH = 2**20
def requests_get_with_max_size(url, user_agent=None):
    '''
    We cap the allowed response size, in order to make things faster and avoid downloading useless huge blobs of data
    cf. https://benbernardblog.com/the-case-of-the-mysterious-python-crash/
    '''
    with closing(requests.get(url, stream=True, timeout=TIMEOUT,
                              headers={'User-Agent': user_agent} if user_agent else {})) as response:
        response.raise_for_status()
        content = ''
        for chunk in response.iter_content(chunk_size=GET_CHUNK_SIZE, decode_unicode=True):
            content += chunk if response.encoding else chunk.decode()
            if len(content) >= MAX_RESPONSE_LENGTH:
                raise ResponseTooBig("The response for URL {} was too large (> {} bytes).".format(url, MAX_RESPONSE_LENGTH))
        return content, response.headers

class ResponseTooBig(Exception):
    pass

class CustomUserAgentAndTimeoutMixin:
    def __init__(self, user_agent, timeout):
        super().__init__()
        # Shadows parent class attribute:
        self.user_agent = user_agent
        self.timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self.timeout
        return conn

class XmlRpcTransport(xmlrpc.client.Transport, CustomUserAgentAndTimeoutMixin):
    pass

class SafeXmlRpcTransport(xmlrpc.client.SafeTransport, CustomUserAgentAndTimeoutMixin):
    pass

# pylint: disable=unused-argument
def send_trackback(source_url, target_url, resp_content=None, resp_headers=None, user_agent=None):
    pass  # Not implemented yet

def register():
    signals.all_generators_finalized.connect(process_all_articles_linkbacks)


if __name__ == '__main__':
    # Some integrations tests:
    logging.basicConfig(level=logging.DEBUG)
    LOGGER.setLevel(logging.DEBUG)
    send_webmention('https://chezsoi.org/lucas/blog/',
                    'https://chezsoi.org/lucas/blog/pages/jeux-de-role.html', user_agent=DEFAULT_USER_AGENT)
    send_pingback('https://chezsoi.org/lucas/blog/',
                  'https://chezsoi.org/lucas/blog/pages/jeux-de-role.html', user_agent=DEFAULT_USER_AGENT)
    # Handling 301 redirects to HTTPS
    # Now getting "Invalid discovery target" errors, probably due to akismet: https://github.com/wp-plugins/akismet/blob/master/class.akismet.php#L1099
    send_pingback('https://chezsoi.org/lucas/blog/lassassin-de-la-reine.html',
                  'https://www.evilhat.com/home/for-the-queen', user_agent=DEFAULT_USER_AGENT)
    # Many Wordpress websites answer a faultCode 0 with no message, due to the default value of xmlrpc_pingback_error :(
    send_pingback('https://chezsoi.org/lucas/blog/face-au-titan.html',
                  'https://www.500nuancesdegeek.fr/sword-without-master', user_agent=DEFAULT_USER_AGENT)
    # ArtStation is protected by CloudFare and keep responding 403s...
    send_pingback('https://chezsoi.org/lucas/blog/porte-monstre-trophee-dore.html',
                  'https://www.artstation.com/artwork/VXe5N', user_agent=DEFAULT_USER_AGENT)
    # Local testing with Wordpress Docker image:
    send_pingback('http://host.docker.internal:5500/OriMushi/',
                  'http://localhost/2020/02/07/test-article/', user_agent=DEFAULT_USER_AGENT)
