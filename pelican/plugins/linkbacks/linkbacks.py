from abc import ABC, abstractmethod
from collections import defaultdict
from contextlib import closing
try:
    from contextlib import nullcontext
except ImportError:  # => Python 3.6
    from contextlib import suppress as nullcontext
from datetime import datetime
import json
import logging
import os
import re
import sys
from os import makedirs
from os.path import basename, splitext
from ssl import CERT_NONE, SSLError
import xmlrpc.client
import warnings
from urllib.parse import urljoin
from urllib3.exceptions import InsecureRequestWarning, HTTPError

from bs4 import BeautifulSoup
import requests
from requests.exceptions import RequestException
from requests.utils import parse_header_links

from pelican import signals
from pelican.generators import ArticlesGenerator


BS4_HTML_PARSER = 'html.parser'  # Alt: 'html5lib', 'lxml', 'lxml-xml'
CACHE_FILENAME = 'pelican-plugin-linkbacks.json'
DEFAULT_USER_AGENT = 'pelican-plugin-linkbacks'
DEFAULT_CERT_VERIFY = True
DEFAULT_TIMEOUT = 3
DEFAULT_IGNORED_URLS_PATTERN = 'artstation.com|deviantart.com|github.com|github.io|itch.io|readthedocs.io|youtube.com|wikipedia.org'
IMAGE_EXTENSIONS = ('.gif', '.jpg', '.pdf', '.png', '.svg')
WEBMENTION_POSS_REL = ('webmention', 'http://webmention.org', 'http://webmention.org/', 'https://webmention.org', 'https://webmention.org/')

LOGGER = logging.getLogger(__name__)


def process_all_articles_linkbacks(generators):
    'Just to ease testing, returns the number of notifications successfully sent'
    root_logger_level = logging.root.level
    if root_logger_level > 0:  # inherit root logger level, if defined
        LOGGER.setLevel(root_logger_level)

    start_time = datetime.now()
    article_generator = next(g for g in generators if isinstance(g, ArticlesGenerator))

    config = LinkbackConfig(article_generator.settings)
    cache = Cache.load_from_json(config)

    original_cache_links_count = cache.links_count()
    successful_notifs_count = 0
    try:
        for article in article_generator.articles:
            if article.status == 'published':
                with nullcontext() if config.cert_verify else warnings.catch_warnings():
                    if not config.cert_verify:
                        warnings.simplefilter('ignore', InsecureRequestWarning)
                    successful_notifs_count += process_all_links_of_an_article(config, cache, article.url, article.slug, article.content)
        return successful_notifs_count
    finally:  # We save the cache & log our progress even in case of an interruption:
        cache.dump_to_json()
        LOGGER.info("Linkback plugin execution took: %s - Links processed & inserted in cache: %s - Successful notifications: %s",
                    datetime.now() - start_time, cache.links_count() - original_cache_links_count, successful_notifs_count)

def process_all_links_of_an_article(config, cache, url, slug, content):
    source_url = os.path.join(config.siteurl, url)
    successful_notifs_count = 0
    # Even if an entry exists in the cache, we always extract all links,
    # in order to support articles edits that could add new links.
    doc_soup = BeautifulSoup(content, BS4_HTML_PARSER)
    for anchor in doc_soup('a'):
        if 'href' not in anchor.attrs:
            continue
        link_url = anchor['href']
        if not link_url.startswith('http'):  # this effectively exclude relative links
            continue
        if config.siteurl and link_url.startswith(config.siteurl):
            LOGGER.debug("Link url %s skipped because is starts with %s", link_url, config.siteurl)
            continue
        if splitext(link_url)[1] in IMAGE_EXTENSIONS:
            LOGGER.debug("Link url %s skipped because it appears to be an image or PDF file", link_url)
            continue
        if config.ignored_urls_pattern.search(link_url):
            LOGGER.debug("Link url %s skipped because it matches the ignored URLs pattern", link_url)
            continue
        cache_status = cache.get_status(slug, link_url)
        if cache_status:
            LOGGER.debug("Link url %s skipped because it is present in cache with status: %s", link_url, cache_status)
            continue
        LOGGER.debug("Now attempting to send Linkbacks for link url %s", link_url)
        try:
            resp_content, resp_headers = requests_get_with_max_size(link_url, config)
        except Exception as error:
            LOGGER.debug("Failed to retrieve web page for link url %s: [%s] %s", link_url, error.__class__.__name__, error)
            cache.add_failure(slug, link_url, error)
            continue
        for notifier_class in (PingbackNotifier, WebmentionNotifier):
            try:
                notifier = notifier_class(source_url, link_url, config)
                notifier.discover_server_uri(resp_content, resp_headers)
                if notifier.server_uri:
                    LOGGER.debug("%s URI detected: %s", notifier.kind, notifier.server_uri)
                else:
                    cache.add_failure(slug, link_url, f"No {notifier.kind} URI found", notifier.kind)
                    continue
                response = notifier.send()
                LOGGER.info("%s notification sent for URL %s, endpoint response: %s", notifier.kind, link_url, response)
                cache.add_success(slug, link_url, notifier.kind, notifier.server_uri, response)
                successful_notifs_count += 1
            except (ConnectionError, HTTPError, NotifierError, RequestException, SSLError, xmlrpc.client.ProtocolError) as error:
                LOGGER.error("Failed to send %s for link url %s: [%s] %s", notifier.kind, link_url, error.__class__.__name__, error)
                cache.add_failure(slug, link_url, error, notifier.kind, notifier.server_uri)
            except Exception as error:  # unexpected exception => we display the stacktrace:
                LOGGER.exception("Failed to send %s for link url %s", notifier.kind, link_url)
                cache.add_failure(slug, link_url, error, notifier.kind, notifier.server_uri)
    return successful_notifs_count

class LinkbackConfig:
    def __init__(self, settings=None):
        if settings is None:
            settings = {}
        self.siteurl = settings.get('SITEURL', '')
        self.cache_filepath = settings.get('LINKBACKS_CACHEPATH')
        if not self.cache_filepath:
            cache_dir = settings.get('CACHE_PATH', '')
            self.cache_filepath = os.path.join(cache_dir, CACHE_FILENAME)
            if cache_dir:
                makedirs(cache_dir, exist_ok=True)
        self.cert_verify = settings.get('LINKBACKS_CERT_VERIFY', DEFAULT_CERT_VERIFY)
        self.timeout = settings.get('LINKBACKS_REQUEST_TIMEOUT', DEFAULT_TIMEOUT)
        self.user_agent = settings.get('LINKBACKS_USERAGENT', DEFAULT_USER_AGENT)
        self.ignored_urls_pattern = settings.get('LINKBACKS_IGNORED_URLS_PATTERN', DEFAULT_IGNORED_URLS_PATTERN)
        if self.ignored_urls_pattern and isinstance(self.ignored_urls_pattern, str):
            self.ignored_urls_pattern = re.compile(self.ignored_urls_pattern)

class Cache:
    def __init__(self, config, data):
        self.cache_filepath = config.cache_filepath
        # Cache structure:
        # {
        #   $article_slug: {
        #     $link_url: {
        #       "pingback": {
        #         "error": // string or null if successful
        #         "response": // string or null if failed
        #         "server_uri": "http...", // optional string
        #       },
        #       "webmention": {
        #         "error": // string or null if successful
        #         "response": // string or null if failed
        #         "server_uri": "http...", // optional string
        #       }
        #     },
        #     ...
        #   },
        #   ...
        # }
        self.data = defaultdict(dict)
        self.data.update(data)
    def add_success(self, article_slug, link_url, kind, server_uri, response):
        article_links = self.data[article_slug]
        link_status = article_links.get(link_url)
        if link_status is None:
            link_status = {}
            article_links[link_url] = link_status
        link_status[kind] = {
            "response": response,
            "server_uri": server_uri
        }
    def add_failure(self, article_slug, link_url, error, notifier_kind=None, server_uri=None):
        article_links = self.data[article_slug]
        link_status = article_links.get(link_url)
        if link_status is None:
            link_status = {}
            article_links[link_url] = link_status
        kinds = [notifier_kind] if notifier_kind else ["pingback", "webmention"]
        for kind in kinds:
            status = {
                "error": error if isinstance(error, str) else f"[{error.__class__.__name__}] {error}"
            }
            if server_uri:
                status["server_uri"] = server_uri
            link_status[kind] = status
    def get_status(self, article_slug, link_url):
        "Return None if a notification should be sent; otherwise return the reason why it should be skipped"
        article_links = self.data[article_slug]
        link_status = article_links.get(link_url)
        if link_status is None:
            return None  # link not processed yet
        pingback_status = link_status.get("pingback")
        webmention_status = link_status.get("webmention")
        if pingback_status is None or webmention_status is None:
            return None  # defensive, should never happen
        # For now we never retry sending pingbacks & webmentions if there is already an entry in the cache.
        # Later on, we could for example consider retrying on HTTP 5XX errors.
        if pingback_status.get("response") or webmention_status.get("response"):
            return "ALREADY SUBMITTED"
        return pingback_status.get("error") or webmention_status.get("error")
    def links_count(self):
        return sum(len(url_statuses) for url_statuses in self.data.values())
    @classmethod
    def load_from_json(cls, config):
        try:
            with open(config.cache_filepath, encoding='utf8') as cache_file:
                data = json.load(cache_file)
        except FileNotFoundError:
            data = {}
        is_old_cache = data and isinstance(list(data.values())[0], list)
        if is_old_cache:
            raise EnvironmentError(
                f"Old cache format detected in {config.cache_filepath}: please remove this file before publishing your website."
                " All linkbacks will be processed again on next pelican execution.",
            )
        return cls(config, data)
    def dump_to_json(self):
        with open(self.cache_filepath, 'w+', encoding='utf8') as cache_file:
            json.dump(self.data, cache_file)

class Notifier(ABC):
    """
    Public properties:
    * kind: 'pingback' or 'webmention'
    * server_uri: URL of the notification endpoint
    """
    @abstractmethod
    def discover_server_uri(self):
        """
        Sets .server_uri if a notification endpoint is found for target_url.
        Must be called before calling send().
        """
    @abstractmethod
    def send(self):
        "Sends the actual notification."

class NotifierError(RuntimeError):
    pass

class PingbackNotifier(Notifier):
    def __init__(self, source_url, target_url, config=LinkbackConfig()):
        self.kind = "pingback"
        self.source_url = source_url
        self.target_url = target_url
        self.config = config
        self.server_uri = None
    def discover_server_uri(self, resp_content=None, resp_headers=None):
        if resp_content is None:
            resp_content, resp_headers = requests_get_with_max_size(self.target_url, self.config)
        # Pingback server autodiscovery:
        self.server_uri = resp_headers.get('X-Pingback')
        if not self.server_uri and resp_headers.get('Content-Type', '').startswith('text/html'):
            # As a falback, we try parsing the HTML, looking for <link> elements
            doc_soup = BeautifulSoup(resp_content, BS4_HTML_PARSER)
            link = doc_soup.find(rel='pingback', href=True)
            if link:
                self.server_uri = link['href']
    def send(self):
        # Performing pingback request:
        transport = SafeXmlRpcTransport(self.config) if self.server_uri.startswith('https') else XmlRpcTransport(self.config)
        xml_rpc_client = xmlrpc.client.ServerProxy(self.server_uri, transport)
        try:
            return xml_rpc_client.pingback.ping(self.source_url, self.target_url)
        except xmlrpc.client.Fault as fault:
            if fault.faultCode == 48:  # pingback already registered
                raise NotifierError(f"Pingback already registered for URL {self.target_url}, XML-RPC response: code={fault.faultCode} - {fault.faultString}") from fault
            raise NotifierError(f"Pingback XML-RPC request failed for URL {self.target_url}: code={fault.faultCode} - {fault.faultString}") from fault

class WebmentionNotifier(Notifier):
    def __init__(self, source_url, target_url, config=LinkbackConfig()):
        self.kind = "webmention"
        self.source_url = source_url
        self.target_url = target_url
        self.config = config
        self.server_uri = None
    def discover_server_uri(self, resp_content=None, resp_headers=None):
        if resp_content is None:
            resp_content, resp_headers = requests_get_with_max_size(self.target_url, self.config)
        # WebMention server autodiscovery:
        link_header = resp_headers.get('Link')
        if link_header:
            try:
                self.server_uri = next(lh.get('url') for lh in parse_header_links(link_header)
                                       if lh.get('url') and lh.get('rel') in WEBMENTION_POSS_REL)
            except StopIteration:
                pass
        if not self.server_uri and resp_headers.get('Content-Type', '').startswith('text/html'):
            # As a falback, we try parsing the HTML, looking for <link> elements
            doc_soup = BeautifulSoup(resp_content, BS4_HTML_PARSER)  # HTML parsing could be factored out of both methods
            for link in doc_soup.find_all(rel=WEBMENTION_POSS_REL, href=True):
                if link.get('href'):
                    self.server_uri = link.get('href')
    def send(self):
        # Performing WebMention request:
        url = urljoin(self.target_url, self.server_uri)
        response = requests.post(url, headers={'User-Agent': self.config.user_agent}, timeout=self.config.timeout,
                                 data={'source': self.source_url, 'target': self.target_url}, verify=self.config.cert_verify)
        response.raise_for_status()
        return response.text


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
                # Even truncated, the output is maybe still parsable as HTML to extract <link> tags.
                # And if not, the linkback endpoint is maybe present as a HTTP header, so we do not abort and still return the content.
                LOGGER.warning("The response for URL %s was too large, and hence was truncated to %s bytes.", url, MAX_RESPONSE_LENGTH)
                break
        return content, response.headers

class XmlRpcTransport(xmlrpc.client.Transport):
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

def register():
    signals.all_generators_finalized.connect(process_all_articles_linkbacks)


def cli(html_filepath):
    logging.basicConfig(format="%(levelname)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S", level=logging.DEBUG)
    config = LinkbackConfig(os.environ)
    cache = Cache.load_from_json(config)
    with nullcontext() if config.cert_verify else warnings.catch_warnings():
        if not config.cert_verify:
            warnings.simplefilter('ignore', InsecureRequestWarning)
        url = basename(html_filepath)
        slug = url.replace(".html", "")
        with open(html_filepath, "r+", encoding="utf-8") as html_file:
            content = html_file.read()
        LOGGER.debug("Now extracting content from tag <article>...")
        content = str(BeautifulSoup(content, BS4_HTML_PARSER).find("article"))
        LOGGER.debug("Now processing HTML file with url=%s slug=%s...", url, slug)
        successful_notifs_count = process_all_links_of_an_article(config, cache, url, slug, content)
        LOGGER.info("Done - Notifications sent: %s", successful_notifs_count)
        cache.dump_to_json()

if __name__ == '__main__':
    try:  # Optional logs coloring:
        from colorama import Back, Fore, Style
        # Recipe from: https://chezsoi.org/lucas/blog/colored-logs-in-python.html
        class ColorLogsWrapper:
            COLOR_MAP = {
                'debug': Fore.CYAN,
                'info': Fore.GREEN,
                'warning': Fore.YELLOW,
                'error': Fore.RED,
                'critical': Back.RED,
            }
            def __init__(self, logger):
                self.logger = logger
            def __getattr__(self, attr_name):
                if attr_name == 'warn':
                    attr_name = 'warning'
                if attr_name not in 'debug info warning error critical':
                    return getattr(self.logger, attr_name)
                log_level = getattr(logging, attr_name.upper())
                # mimicking logging/__init__.py behaviour
                if not self.logger.isEnabledFor(log_level):
                    return None
                def wrapped_attr(msg, *args, **kwargs):
                    style_prefix = self.COLOR_MAP[attr_name]
                    msg = style_prefix + msg + Style.RESET_ALL
                    # We call _.log directly to not increase the callstack
                    # so that Logger.findCaller extract the corrects filename/lineno
                    # pylint: disable=protected-access
                    return self.logger._log(log_level, msg, args, **kwargs)
                return wrapped_attr
        LOGGER = ColorLogsWrapper(LOGGER)
    except ImportError:
        print("colorama not available - Logs coloring disabled")
    cli(sys.argv[1])
