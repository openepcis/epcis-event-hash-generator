"""The offline context loader: every URL it claims must resolve without a network.

Why this exists: the loader's map is the only thing standing between a hash
computation and an outbound HTTP request. When a document carries a context URL
the map does not know, pyld falls through to the network -- slowly where that is
allowed, and not at all where it is blocked. That is not hypothetical: it is how
the missing unversioned entry was found, in an Odoo test suite that forbids
outbound requests, where every eventID computation failed outright.

A missing entry is invisible in any environment with a network, which is why it
is asserted here rather than left to be noticed.
"""

import hashlib
import json
import urllib.request

import pytest

from epcis_event_hash_generator import file_document_loader  # noqa: F401  (read as source)

# The two URLs that serve the same document. GS1 leaves the version off the
# current release, so "epcis-context.jsonld" and "2.0.1/epcis-context.jsonld"
# are one and the same file today.
CURRENT = "https://ref.gs1.org/standards/epcis/epcis-context.jsonld"
VERSIONED_CURRENT = "https://ref.gs1.org/standards/epcis/2.0.1/epcis-context.jsonld"
DEPRECATED_PREFIX = "https://ref.gs1.org/standards/epcis/2.0.0/epcis-context.jsonld"


def _from_source():
    import inspect
    import re

    source = inspect.getsource(file_document_loader)
    return dict(re.findall(r'"(https://[^"]+)":\s*\n?\s*"([0-9a-f]+\.jsonld)"', source))


def _raw(name):
    import importlib.resources

    return importlib.resources.files("epcis_event_hash_generator").joinpath(name).read_bytes()


def _bundled(name):
    return json.loads(_raw(name))


def _prefix(document):
    context = document["@context"]
    merged = {}
    for part in context if isinstance(context, list) else [context]:
        if isinstance(part, dict):
            merged.update(part)
    return merged.get("gs1")


def test_every_mapped_url_resolves_from_the_package():
    """No entry may point at a file the package does not ship."""
    mapping = _from_source()
    assert mapping, "the loader's URL map could not be read"
    for url, name in mapping.items():
        assert _bundled(name), f"{url} maps to {name}, which is empty or missing"


def test_the_current_context_is_carried_offline():
    """The URL we actually write must not need the network."""
    mapping = _from_source()
    assert CURRENT in mapping


def test_the_versioned_current_context_is_carried_too():
    """2.0.1 is the current release, so nothing emits it -- until a 2.0.2 lands.

    On that day the unversioned URL moves on and anything pinned to 2.0.1 would
    start reaching for the network. Carrying it now costs one map entry and no
    extra file, because it is the same document.
    """
    mapping = _from_source()
    assert VERSIONED_CURRENT in mapping
    assert mapping[VERSIONED_CURRENT] == mapping[CURRENT], (
        "2.0.1 and the unversioned URL serve the same document and should share a file"
    )


def test_every_bundled_context_binds_gs1_to_the_current_namespace():
    """The project deliberately serves the corrected `gs1` prefix everywhere.

    GS1 published 2.0.0 with `gs1` bound to the deprecated https://gs1.org/voc/
    and corrected it in 2.0.1 to https://ref.gs1.org/voc/. This package does not
    reproduce that split: 3e27c21 ("changing examples and expected hashes")
    replaced the bundled 2.0.0 document with the corrected one and moved the
    expected hashes with it, so a document declaring 2.0.0 expands here the way
    2.0.1 would.

    That is a choice, not an accident, and it is pinned here so it cannot be
    undone by a careless re-download. What it costs is worth knowing: a peer
    that loads 2.0.0 from the network expands `gs1:` CURIEs to the deprecated
    IRI and can compute a different hash for the same event.
    """
    for url, name in _from_source().items():
        if not url.startswith("https://ref.gs1.org/"):
            continue
        assert _prefix(_bundled(name)) == "https://ref.gs1.org/voc/", url


@pytest.mark.skipif(
    __import__("os").environ.get("OFFLINE") == "1",
    reason="needs the network; set OFFLINE=1 to skip",
)
def test_the_bundled_copies_still_match_what_gs1_serves():
    """A bundled context that has drifted from the published one is worse than none.

    Only what GS1 actually serves is compared. Some entries are deliberately
    ahead of publication -- 2.1.0 answers 404 today -- and a copy of something
    unpublished has nothing to be checked against. Those are reported, not
    failed, so the check stays honest about what it did and did not verify.

    Skipped where there is no network, which is of course exactly the situation
    the rest of this file is about.
    """
    geprueft, unveroeffentlicht, ersetzt = [], [], []
    for url, name in _from_source().items():
        if not url.startswith("https://ref.gs1.org/"):
            continue  # the github.io and eecc.de copies are not GS1-published
        # The filename is the sha256 of the published document. Where it no
        # longer matches, the copy was replaced on purpose (see the test above),
        # and comparing it against GS1 would just re-report that decision.
        if hashlib.sha256(_raw(name)).hexdigest() != name[: -len(".jsonld")]:
            ersetzt.append(url)
            continue
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                served = json.loads(response.read())
        except urllib.error.HTTPError as answer:
            if answer.code == 404:
                unveroeffentlicht.append(url)
                continue
            raise
        assert served == _bundled(name), f"the bundled copy of {url} has drifted"
        geprueft.append(url)

    assert geprueft, "nothing was actually compared"
    print(
        f"compared {len(geprueft)}; not published yet: {unveroeffentlicht}; "
        f"replaced on purpose: {ersetzt}"
    )
