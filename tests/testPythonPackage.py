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
"""Unit tests for PythonPackage.package() after the Python 3 port.

The port moved `import sys`/`import importlib` into the method and replaced
the reload() builtin with importlib.reload().  distutils itself is gone from
Python 3.12+, so a fake `distutils.core` is put in sys.modules and
importlib.reload is patched; the csmake engine is a FakeEngine.
"""
import sys
import types
import unittest
from unittest import mock

from . import csmake_alias
from .support import FakeEngine, FakeResult, new_packager


class testPythonPackage(unittest.TestCase):

    def setUp(self):
        self.PythonPackage = csmake_alias.import_plugin(
            'PythonPackage').PythonPackage
        self.setup_calls = []
        self.core = types.ModuleType('distutils.core')
        self.core.setup = lambda **kwargs: self.setup_calls.append(
            (kwargs, list(sys.argv)))
        package = types.ModuleType('distutils')
        package.__path__ = []
        package.core = self.core
        patch = mock.patch.dict(
            sys.modules, {'distutils': package, 'distutils.core': self.core})
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch('importlib.reload')
        self.reload = patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(sys, 'argv', ['prog', 'original'])
        patch.start()
        self.addCleanup(patch.stop)

    def make_package(self, step_result):
        return new_packager(
            self.PythonPackage,
            engine=FakeEngine(steps={'pkgdef': step_result}))

    def options(self):
        return {'command': 'bdist --dist-dir out', 'package': ' pkgdef '}

    def test_package_runs_setup_with_the_command_line_as_argv(self):
        package = self.make_package(FakeResult(True))
        package.package(self.options())

        self.assertEqual(
            [['setup.py', 'bdist', '--dist-dir', 'out']],
            [argv for _, argv in self.setup_calls])
        self.reload.assert_called_once_with(self.core)
        self.assertEqual([('pkgdef', None, {})], package.engine.launched)

    def test_package_restores_argv_afterwards(self):
        self.make_package(FakeResult(True)).package(self.options())
        self.assertEqual(['prog', 'original'], sys.argv)

    def test_package_restores_argv_when_setup_raises(self):
        self.core.setup = mock.Mock(side_effect=RuntimeError("setup blew up"))
        with self.assertRaises(RuntimeError):
            self.make_package(FakeResult(True)).package(self.options())
        self.assertEqual(['prog', 'original'], sys.argv)

    def test_package_fails_when_the_package_step_fails(self):
        package = self.make_package(FakeResult(False))
        self.assertIsNone(package.package(self.options()))
        self.assertTrue(package.log.called('failed'))
        self.assertEqual([], self.setup_calls)
        self.assertEqual(['prog', 'original'], sys.argv)

    def test_package_fails_when_the_package_step_is_unknown(self):
        package = self.make_package(None)
        self.assertIsNone(package.package(self.options()))
        self.assertEqual([], self.setup_calls)


if __name__ == '__main__':
    unittest.main()
