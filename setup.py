from setuptools import setup
import os.path as path

reqs = path.join(path.dirname(__file__), "requirements.txt")
requirements = []
with open(reqs) as f:
    for line in f:
        req = line.split('#')[0].strip()
        if len(req) > 0:
            requirements.append(req)

setup(
    name="xbci",
    version="0.0.1",
    description="A dependency-resolving distributed CI system for xbstrap",
    author="Arsen Arsenovic",
    author_email="arsen@aarsen.me",
    packages=["xbci"],
    license="AGPL-3.0-only",
    # TODO(arsen): remove requirements file
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "xbci-coordinator = xbci.coordinator.coordinator:main",
            "xbci-worker = xbci.worker.worker:main"
        ]
    }
)
