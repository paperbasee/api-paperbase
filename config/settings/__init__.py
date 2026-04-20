"""
Django settings package.

Primary module (12-factor, Docker, production): ``config.settings.runtime``.

Legacy entrypoints (compatibility):

- ``config.settings.development`` — forces DEBUG=true, then loads runtime.
- ``config.settings.production`` — requires DEBUG=false, then loads runtime.
"""
