import logging
import sys
import time

from . import helpers
from .base import Base
from .helpers import Client

logger = logging.getLogger(__name__)


class Service(Base):
    def __init__(self, client):
        self.client = Client()

    def run(self, items):
        self.save(items)
        self.client.fetch(items)
        helpers.clean(items)
        return items


def gen(items):
    for i in items:
        if i:
            yield i
    yield from items


def exits(x):
    if not x:
        sys.exit("bad")
    if x == 2:
        exit(2)
    print("ok")
    return x


def finals(x):
    try:
        return helpers.clean(x)
    finally:
        helpers.cleanup()


def late_raise(x):
    try:
        a = helpers.clean(x)
        b = helpers.parse(a)
        c = helpers.store(b)
    except KeyError:
        return None
    return c


def caught_raise(x):
    try:
        if x:
            raise ValueError("bad x")
        helpers.store(x)
    except ValueError:
        return None
    return x


def aug(x):
    total = 0
    total += x
    total -= helpers.clean(x)
    return total


def dupes(a, b):
    for i in a:
        helpers.store(i)
    for i in a:
        helpers.store(i)
    try:
        helpers.clean(a)
    except Exception:
        pass
    try:
        helpers.clean(b)
    except Exception:
        pass
    if a is None:
        return 1
    if a is None:
        return 2
    return 3


def logs(x):
    t0 = time.perf_counter()
    logger.info(f"Handling request for {x}")
    total = 0
    return total


def factory():
    def inner(x):
        if x:
            return 1
        return 2
    return inner(3)


def use(svc: Service):
    return svc.run([1])


def main():
    s = Service(None)
    s.run([1])
    exits(1)
