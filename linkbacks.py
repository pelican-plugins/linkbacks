from contextlib import closing
from datetime import datetime
import json, logging, os, xmlrpc.client, warnings
from os.path import splitext
from ssl import CERT_NONE, SSLError
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pelican import signals
from pelican.generators import ArticlesGenerator
import requests
from requests.exceptions import RequestException
from requests.utils import parse_header_links
from urllib3.exceptions import InsecureRequestWarning, HTTPError


BS4_HTML_PARSER = 'html.parser'  # Alt: 'html5lib', 'lxml', 'lxml-xml'
CACHE_FILENAME = 'pelican-plugin-linkbacks.json'
DEFAULT_USER_AGENT = 'pelican-plugin-linkbacks'
DEFAULT_CERT_VERIFY = False
DEFAULT_TIMEOUT = 3
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
    cache_filepath = settings.get('LINKBACKS_CACHEPATH') or os.path.join(settings.get('CACHE_PATH'), CACHE_FILENAME)
    config = LinkbackConfig(settings)
    if config.cert_verify:
        process_article_links = process_all_links_of_an_article
    else:  # silencing InsecureRequestWarnings:
        def process_article_links(article, cache, config):
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', InsecureRequestWarning)
                return process_all_links_of_an_article(article, cache, config)

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
                successful_notifs_count += process_article_links(article, cache, config)
        return successful_notifs_count
    finally:  # We save the cache & log our progress even in case of an interruption:
        with open(cache_filepath, 'w+') as cache_file:
            json.dump(cache, cache_file)
        new_cache_links_count = sum(len(urls) for slug, urls in cache.items())
        LOGGER.info("Linkback plugin execution took: %s - Links processed & inserted in cache: %s - Successful notifications: %s",
                    datetime.now() - start_time, new_cache_links_count - original_cache_links_count, successful_notifs_count)

class LinkbackConfig:
    def __init__(self, settings=None):
        if settings is None:
            settings = {}
        self.siteurl = settings.get('SITEURL', '')
        self.cert_verify = settings.get('LINKBACKS_CERT_VERIFY', DEFAULT_CERT_VERIFY)
        self.timeout = settings.get('LINKBACKS_REQUEST_TIMEOUT', DEFAULT_TIMEOUT)
        self.user_agent = settings.get('LINKBACKS_USERAGENT', DEFAULT_USER_AGENT)

def process_all_links_of_an_article(article, cache, config):
    source_url = os.path.join(config.siteurl, article.url)
    successful_notifs_count = 0
    links_cache = set(cache.get(article.slug, []))
    doc_soup = BeautifulSoup(article.content, BS4_HTML_PARSER)
    for anchor in doc_soup('a'):
        if 'href' not in anchor.attrs:
            continue
        link_url = anchor['href']
        if not link_url.startswith('http'):  # this effectively exclude relative links
            continue
        if config.siteurl and link_url.startswith(config.siteurl):
            LOGGER.debug("Link url %s skipped because is starts with %s", link_url, config.siteurl)
            continue
        if splitext(link_url)[1] in ('.gif', '.jpg', '.pdf', '.png', '.svg'):
            LOGGER.debug("Link url %s skipped because it appears to be an image or PDF file", link_url)
            continue
        if link_url in links_cache:
            LOGGER.debug("Link url %s skipped because it has already been processed (present in cache)", link_url)
            continue
        LOGGER.debug("Now attempting to send Linkbacks for link url %s", link_url)
        try:
            resp_content, resp_headers = requests_get_with_max_size(link_url, config)
        except Exception as error:
            LOGGER.debug("Failed to retrieve web page for link url %s: [%s] %s", link_url, error.__class__.__name__, error)
            continue
        for notifier in (send_pingback, send_webmention): #, send_trackback):
            if notifier(source_url, link_url, config, resp_content, resp_headers):
                successful_notifs_count += 1
        links_cache.add(link_url)
    cache[article.slug] = list(links_cache)
    return successful_notifs_count

def send_pingback(source_url, target_url, config=LinkbackConfig(), resp_content=None, resp_headers=None):
    try:
        if resp_content is None:
            resp_content, resp_headers = requests_get_with_max_size(target_url, config)
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
        transport = SafeXmlRpcTransport(config) if server_uri.startswith('https') else XmlRpcTransport(config)
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

def send_webmention(source_url, target_url, config=LinkbackConfig(), resp_content=None, resp_headers=None):
    try:
        if resp_content is None:
            resp_content, resp_headers = requests_get_with_max_size(target_url, config)
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
        response = requests.post(server_uri, headers={'User-Agent': config.user_agent}, timeout=config.timeout,
                                 data={'source': source_url, 'target': target_url}, verify=config.cert_verify)
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
def requests_get_with_max_size(url, config=LinkbackConfig()):
    '''
    We cap the allowed response size, in order to make things faster and avoid downloading useless huge blobs of data
    cf. https://benbernardblog.com/the-case-of-the-mysterious-python-crash/
    '''
    with closing(requests.get(url, stream=True, timeout=config.timeout, verify=config.cert_verify,
                              headers={'User-Agent': config.user_agent})) as response:
        response.raise_for_status()
        content = ''
        for chunk in response.iter_content(chunk_size=GET_CHUNK_SIZE, decode_unicode=True):
            content += chunk if response.encoding else chunk.decode()
            if len(content) >= MAX_RESPONSE_LENGTH:
                raise ResponseTooBig("The response for URL {} was too large (> {} bytes).".format(url, MAX_RESPONSE_LENGTH))
        return content, response.headers

class ResponseTooBig(Exception):
    pass

class XmlRpcTransport(xmlrpc.client.Transport):
    def __init__(self, config):
        super().__init__()
        self.config = config
        if config.user_agent is not None:
            # Shadows parent class attribute:
            self.user_agent = config.user_agent

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self.config.timeout
        return conn

class SafeXmlRpcTransport(xmlrpc.client.SafeTransport):
    def __init__(self, config):
        super().__init__()
        self.config = config
        if config.user_agent is not None:
            # Shadows parent class attribute:
            self.user_agent = config.user_agent

    def make_connection(self, host):
        conn = super().make_connection(host)
        if self.config.timeout is not None:
            conn.timeout = self.config.timeout
        if self.config.cert_verify is False:
            # pylint: disable=protected-access
            conn._check_hostname = False
            conn._context.check_hostname = False
            conn._context.verify_mode = CERT_NONE
        return conn

# pylint: disable=unused-argument
def send_trackback(source_url, target_url, config=LinkbackConfig()):
    pass  # Not implemented yet

def register():
    signals.all_generators_finalized.connect(process_all_articles_linkbacks)
