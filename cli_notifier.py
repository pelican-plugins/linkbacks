#!/usr/bin/env python3
# USAGE: ./cli_notifier.py [pingback|webmention] $source_url $link_url
import logging, os, sys
from pelican.plugins.linkbacks import LinkbackConfig, PingbackNotifier, WebmentionNotifier

def main(kind, source_url, link_url):
    logging.basicConfig(format="%(levelname)s [%(name)s] %(message)s",
                        datefmt="%H:%M:%S", level=logging.DEBUG)
    config = LinkbackConfig(os.environ)
    notifier_class = PingbackNotifier if kind == "pingback" else WebmentionNotifier
    notifier = notifier_class(source_url, link_url, config)
    notifier.discover_server_uri()
    if notifier.server_uri:
        print(f"{notifier.kind} URI detected: {notifier.server_uri}")
        response = notifier.send()
        print(f"Notification successfully sent: {response}")

if __name__ == '__main__':
    main(*sys.argv[1:])
