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
"""Guards against Python 2 code creeping back in.

Every .py file in the repository must compile under the running Python 3
(print statements, backticks, 0777-style octal literals, 'except X, e' and
the mixed tab/space indentation that Python 2 tolerated are all errors),
and every module must import (a leftover 'import StringIO' or a top-level
use of a removed stdlib module compiles fine but fails here).  It proves
nothing about behaviour; the other test modules do that.
"""
import os
import unittest
import warnings

from . import csmake_alias

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# .git and __pycache__ are not source; target/ is gitignored build output.
SKIP_DIRS = ('.git', '__pycache__', 'target')


class testCompiles(unittest.TestCase):

    def test_every_python_file_compiles(self):
        checked = 0
        failures = []
        for root, dirs, files in os.walk(REPO):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in sorted(files):
                if not name.endswith('.py'):
                    continue
                path = os.path.join(root, name)
                with open(path, 'rb') as source:
                    text = source.read()
                try:
                    with warnings.catch_warnings():
                        # compile() warns about odd (but valid) constructs;
                        # only hard errors matter for this guard.
                        warnings.simplefilter('ignore')
                        compile(text, path, 'exec', dont_inherit=True)
                except (SyntaxError, ValueError) as exc:
                    failures.append("%s: %s" % (
                        os.path.relpath(path, REPO), exc))
                checked += 1
        self.assertGreater(checked, 0, "found no .py files under %s" % REPO)
        self.assertEqual([], failures)


class testImports(unittest.TestCase):

    # HermeticShell and WheelPackage need other csmake repos / core pieces
    # and are covered by their own owners' tests.
    EXCLUDED = ('HermeticShell', 'WheelPackage')

    def test_every_module_imports(self):
        modules = sorted(
            name[:-3] for name in os.listdir(os.path.join(REPO, 'CsmakeModules'))
            if name.endswith('.py') and name[:-3] not in self.EXCLUDED)
        self.assertGreater(len(modules), 0)
        for name in modules:
            with self.subTest(name):
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore')
                    csmake_alias.import_plugin(name)


if __name__ == '__main__':
    unittest.main()
