from setuptools import setup, find_packages

setup(
    name="xbbs",
    version="0.0.1",
    description="A dependency-resolving distributed build server for xbstrap",
    author="Arsen Arsenovic",
    author_email="arsen@aarsen.me",
    packages=find_packages(),
    license="AGPL-3.0-only",
    install_requires=[
        "attrs",
        "gunicorn",
        "gevent",
        "flask",
        "Jinja2",
        "xbstrap",
        "pyzmq",
        "toml",
        "logbook",
        "msgpack",
        "valideer",
        "requests",
        "humanize",
        # required to decompress repodata into tar
        "zstandard"
    ],
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "xbbs-coordinator = xbbs.coordinator:main",
            "xbbs-cli = xbbs.cli:main",
            "xbbs-worker = xbbs.worker:main"
        ]
    }
)
