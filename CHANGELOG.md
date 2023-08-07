# Change Log
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [1.0.5] - not released yet
### Added
* ensured support for the latest Python 3.11 version
### Fixed
* `ImportError` with Python 3.6 due to `contextlib.nullcontext` not existing yet
* if the `cache/` directory does not exist, it is created - _cf._ [issue #11](https://github.com/pelican-plugins/linkbacks/issues/11)
### Changed
- The path for linkbacks is modified to allow for pelican-plugins to pick it up properly from pip

## [1.0.4] - 2020-07-15
### Changed
- web pages of size greater than 2<sup>20</sup> bytes are not ignored anymore

## [1.0.3] - 2020-07-09
### Changed
- silenced `InsecureRequestWarning`s

## [1.0.2] - 2020-07-09
### Fixed
- fixed this: `ValueError: empty or no certificate, match_hostname needs a SSL socket or SSL context with either CERT_OPTIONAL or CERT_REQUIRED`

## [1.0.1] - 2020-07-09
### Added
- `LINKBACKS_CERT_VERIFY` & `LINKBACKS_REQUEST_TIMEOUT` settings
### Fixed
- the `LINKBACKS_USER_AGENT` setting was previously ignored
- the user-agent & timeout were improperly configured for the pingback requests

## [1.0.0] - 2020-02-14
Initial version
