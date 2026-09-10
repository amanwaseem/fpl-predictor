"""Test package.

Exists so the suite has more than one working invocation. Without it,
`python -m unittest discover -s tests -t .` fails with "Start directory is not
importable" and a bare `python -m unittest` finds nothing — leaving exactly one
undocumented-by-default spelling that works, which is how a suite silently
stops running in CI.

Shared fixtures are imported as `from tests import fixtures` rather than
`import fixtures`, because once this file exists the tests directory is a
package and is no longer placed on sys.path in its own right. That form also
resolves without this file, through a namespace package, so a test module
written against either layout works under both.
"""
