from setuptools import setup, find_packages

setup(
    name="xbci",
    version="0.0.1",
    description="A dependency-resolving distributed CI system for xbstrap",
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
        "requests"
    ],
    entry_points={
        "console_scripts": [
            "xbci-coordinator = xbci.coordinator:main",
            "xbci-worker = xbci.worker:main"
        ]
    }
)
