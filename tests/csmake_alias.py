# <copyright>
# (c) Copyright 2026 Autumn Patterson
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# </copyright>
"""Test-only import shim for the plugin modules.

Some plugin modules still say 'from Csmake.X import Y' but the installed
csmake only ships CsmakeCore.  That is a known, separate ecosystem issue
and production imports are deliberately left alone; the tests alias
'Csmake' -> 'CsmakeCore' (same module objects) instead.  CsmakeCore, and
the core 'CsmakeModules' (Packager, DebianPackage, ...) the plugins build
on, are taken from sys.path, else from the sibling checkout
<parent of this repo>/csmake.
"""
import importlib
import importlib.abc
import importlib.util
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_CHECKOUT = os.path.join(os.path.dirname(REPO), 'csmake')


class _CsmakeAliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolves 'Csmake.<sub>' to the same module object as 'CsmakeCore.<sub>'."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('Csmake.'):
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        real = importlib.import_module('CsmakeCore.' + spec.name.split('.', 1)[1])
        spec.loader_state = real.__spec__  # import overwrites __spec__; put it back
        return real

    def exec_module(self, module):
        module.__spec__ = module.__spec__.loader_state


def _on_path(name):
    try:
        return importlib.util.find_spec(name) is not None
    except ImportError:
        return False


def install():
    """Make 'import Csmake.X' work; SkipTest if csmake core cannot be found."""
    if _on_path('Csmake'):
        return
    if not _on_path('CsmakeCore'):
        if not os.path.isdir(os.path.join(CORE_CHECKOUT, 'CsmakeCore')):
            raise unittest.SkipTest("csmake core not found")
        sys.path.insert(0, CORE_CHECKOUT)
    sys.modules['Csmake'] = importlib.import_module('CsmakeCore')
    sys.meta_path.insert(0, _CsmakeAliasFinder())


def import_plugin(name):
    """Import CsmakeModules.<name> (this repo's, or a core one it builds on).

    Skips (rather than fails) when a base class from some other csmake
    checkout is unavailable; any other ImportError is a real failure."""
    install()
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    if os.path.isdir(CORE_CHECKOUT) and CORE_CHECKOUT not in sys.path \
            and not _on_path('CsmakeModules.Packager'):
        sys.path.append(CORE_CHECKOUT)  # core's CsmakeModules joins the namespace
    fullname = 'CsmakeModules.' + name
    try:
        return importlib.import_module(fullname)
    except ModuleNotFoundError as exc:
        if exc.name != fullname and (exc.name or '').split('.')[0] in (
                'CsmakeModules', 'Csmake', 'CsmakeCore'):
            raise unittest.SkipTest("%s needs %s, which is not available" % (
                fullname, exc.name))
        raise
