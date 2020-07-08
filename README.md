[![Pull Requests Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat)](http://makeapullrequest.com)
[![build status](https://github.com/pelican-plugins/linkbacks/workflows/build/badge.svg)](https://github.com/pelican-plugins/linkbacks/actions?query=workflow%3Abuild)
[![Pypi latest version](https://img.shields.io/pypi/v/pelican-plugin-linkbacks.svg)](https://pypi.python.org/pypi/pelican-plugin-linkbacks)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

[Pelican](https://getpelican.com) plugin implementing [Linkback](https://en.wikipedia.org/wiki/Linkback) protocols,
on the linking server side.

Protocols currently implemented:
- [x] XMLRPC Pingback: [protocol spec](http://www.hixie.ch/specs/pingback/pingback)
- [x] [Webmention](https://indieweb.org/Webmention): [protocol spec](https://github.com/converspace/webmention) - [W3C Recommendation](https://www.w3.org/TR/2017/REC-webmention-20170112/)

❌ Refback: won't be implemented because it requires to retrieve the HTTP `Referer` header,
which cannot be done by Pelican, a static blog engine

❌ TalkBack: won't be implemented because it did not gain enough popularity / traction since its birth in 2011

❌ Trackback ([protocol spec](http://archive.cweiske.de/trackback/trackback-1.2.html)):
won't be implemented because it does not seem widely used,
and requires to parse embedded RDF documents (enclosed in HTML comments as a fallback),
which seems a poor design in an era of HTML5 / [RDFa](https://fr.wikipedia.org/wiki/RDFa)

Do not hesitate to suggest other protocols, or report your experience with this plugin, by submitting an _issue_.

## What are linkbacks?

> A linkback is a method for Web authors to obtain notifications when other authors link to one of their documents.
> This enables authors to keep track of who is linking to, or referring to, their articles.
> The four methods (Refback, Trackback, Pingback and Webmention) differ in how they accomplish this task.

I invite you to read this Wikipedia page for more information & links: [Linkback](https://en.wikipedia.org/wiki/Linkback)


## What does this plugin do?
For every hyperlink in your articles, this plugin will notify their hosting websites
(just those supporting a Linkback protocol) of those references.

This plugin **does not** perform inclusion of Linkbacks **in your articles / as comments**,
for every website referencing your content following a Linkback protocol,
because this cannot be performed by a static website generator like Pelican.

When you enable this plugin the first time, it will process all the hyperlinks of your existing articles.
It will do it only once, and then create a cache file to avoid processing those links next time.
Still, because the `publish` step will be longer than usual the first time you enable this plugin,
I recommend to use `pelican -D` flag to get debug logs, and hence follow the plugin progress.


## Installation / setup instructions
To enable this plugin:
1. Install the package from Pypi: `pip install pelican-plugin-linkbacks`
2. Add the plugin to your `publishconf.py`:
```python
PLUGINS = [..., 'linkbacks']
```

### Cache
In order to avoid the repetitive CPU / bandwidth cost of repeatedly performing links parsing & linkback notifications,
this hook only proceed to do so once, the first time an article is published.

In order to do so, it uses a very simple and small cache that contains the list of all hyperlinks already parsed,
per article `slug`. <!-- Note: alternatively, we could cache only article slugs and consider them entirely processed -->

To remove a blog entry from cache, in order for the plugin to retry sending a linkback:

    jq "del(.['$slug'])" pelican-plugin-linkbacks.json | sponge pelican-plugin-linkbacks.json


### Configuration
Available options:

- `LINKBACKS_CACHEPATH` (optional, default: `$CACHE_PATH/pelican-plugin-linkbacks.json`,
where `$CACHE_PATH` is [a Pelican setting](https://docs.getpelican.com/en/latest/settings.html)) :
  the path to the JSON file containg this plugin cache (a list of URLs already processed).
- `LINKBACKS_USERAGENT` (optional, default: `pelican-plugin-linkbacks`) :
  the `User-Agent` HTTP header to use while sending notifications.
- `LINKBACKS_CERT_VERIFY` (optional, default: `False`) :
  enforce HTTPS certificates verification when sending linkbacks
- `LINKBACKS_REQUEST_TIMEOUT` (optional, in seconds, default: `3`) :
  time in seconds allowed for each HTTP linkback request before abandon


## Contributing

Contributions are welcome and much appreciated. Every little bit helps. You can contribute by improving the documentation,
adding missing features, and fixing bugs. You can also help out by reviewing and commenting on [existing issues](https://github.com/pelican-plugins/linkbacks/issues).

To start contributing to this plugin, review the [Contributing to Pelican](https://docs.getpelican.com/en/latest/contribute.html) documentation,
beginning with the **Contributing Code** section.


### Releasing a new version
With a valid `~/.pypirc`:

1. update `CHANGELOG.md`
2. bump version in `pyproject.toml`
3. `poetry build && poetry publish`
4. perform a release on GitGub, including the description added to `CHANGELOG.md`


## Linter & tests
To execute them:

    pylint *linkbacks.py
    pytest

### Integration tests

You'll find some advices & resources on [indieweb.org](https://indieweb.org):
[pingback page](https://indieweb.org/pingback), [webmention page](https://indieweb.org/Webmention).

For WebMentions specifically, the [webmention.io](https://webmention.io) service can be useful.

For Pingbacks, I used for my tests a Wordpress instance launched with Docker:

    docker run --rm -p 80:80 -e WORDPRESS_DB_HOST=host.docker.internal -e WORDPRESS_DB_USER=... -e WORDPRESS_DB_PASSWORD=... wordpress

From my experience, you'll also have to:
- configure a local MySQL database to accept connections from `$WORDPRESS_DB_USER:$WORDPRESS_DB_PASSWORD`
- configure the `xmlrpc_pingback_error` Wordpress filter to be _passthrough_, to get useful error messages
- configure the `http_request_host_is_external` Wordpress filter to always return `true`,
  so that it won't reject `host.docker.internal` links

Wordpress client source code related to XML-RPC pingbacks can be found there:
- [wp-includes/comment.php](https://github.com/WordPress/WordPress/blob/master/wp-includes/comment.php)
- [wp-includes/class-wp-xmlrpc-server.php](https://github.com/WordPress/WordPress/blob/master/wp-includes/class-wp-xmlrpc-server.php)
