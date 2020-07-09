# Change Log
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

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
