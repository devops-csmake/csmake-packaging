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
"""Integration tests for HermeticShell.

CsmakeModule instances need a fully wired Environment/engine to construct
(see csmake core's testCsmakeModulePackager.py), and HermeticShell itself
needs both the csmake launcher's own module loader (its
'from CsmakeModules.Shell import Shell' resolves through that, not a
plain Python import) and csmake-docker-runtime's DockerRuntimeLibrary on
the module path. So this drives the real csmake launcher via subprocess
against a small fixture buildspec, with --modules-path covering all three
sibling checkouts (csmake, csmake-packaging, csmake-docker-runtime) --
assumed to be sibling directories under the same parent, matching this
project's own layout convention. Skips gracefully if either the sibling
checkouts or a working Docker aren't found, rather than failing.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

THIS_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK_DIR = os.path.dirname(THIS_REPO)
CORE_REPO = os.path.join(WORK_DIR, 'csmake')
DOCKER_RUNTIME_REPO = os.path.join(WORK_DIR, 'csmake-docker-runtime')


def _docker_available():
    if shutil.which('docker') is None:
        return False
    try:
        completed = subprocess.run(
            ['docker', 'info'], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=15)
        return completed.returncode == 0
    except Exception:
        return False


def _siblings_available():
    return (
        os.path.isfile(os.path.join(CORE_REPO, 'csmake'))
        and os.path.isdir(os.path.join(
            DOCKER_RUNTIME_REPO, 'DockerRuntimeLibrary')))


FIXTURE_CSMAKEFILE = """
[~~phases~~]
build=run the hermetic step
**default=build

[ShellEnv@fixture-env]
FIXTURE_MARKER=hello-from-hermetic-shell

[HermeticShell@fixture]
image=alpine:latest
exec=/bin/sh
env=fixture-env
command=echo "marker=$FIXTURE_MARKER" > out/result.txt
        cat /etc/os-release >> out/result.txt

[command@run]
description=run the fixture step
00=fixture
"""


@unittest.skipUnless(_siblings_available(), "sibling checkouts not found")
@unittest.skipUnless(_docker_available(), "docker not available")
class TestHermeticShellIntegration(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp()
        with open(os.path.join(self.workdir, 'csmakefile'), 'w') as f:
            f.write(FIXTURE_CSMAKEFILE)
        os.makedirs(os.path.join(self.workdir, 'out'))

    def tearDown(self):
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _run(self):
        env = dict(os.environ)
        # DockerRuntimeLibrary is a plain Python library, not a
        # CsmakeModules-loader-managed section -- --modules-path makes
        # HermeticShell.py itself discoverable, but the library import
        # inside it ('from DockerRuntimeLibrary.DockerRuntime import
        # DockerRuntime') is a normal Python import needing the
        # checkout's own root on sys.path.  A registry-installed
        # dependency gets this automatically (ModuleRegistry adds its
        # root to sys.path); a dev checkout needs it set explicitly.
        env['PYTHONPATH'] = DOCKER_RUNTIME_REPO
        # CliDriver reads $PWD, not os.getcwd() -- see
        # csmake core's testCsmakeModulePackager.py for why this matters.
        env['PWD'] = self.workdir
        modules_path = ':%s:%s' % (THIS_REPO, DOCKER_RUNTIME_REPO)
        return subprocess.run(
            [sys.executable, os.path.join(CORE_REPO, 'csmake'),
             '--modules-path=' + modules_path,
             '--command=run', 'build'],
            cwd=self.workdir, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)

    def test_command_runs_inside_the_pinned_container(self):
        completed = self._run()
        self.assertEqual(
            completed.returncode, 0,
            "csmake failed:\n%s" % completed.stderr.decode('utf-8', 'replace'))
        with open(os.path.join(self.workdir, 'out', 'result.txt')) as f:
            result = f.read()
        self.assertIn('marker=hello-from-hermetic-shell', result)
        # Proves the command actually ran inside the pinned Alpine image,
        # not on the host (whatever the host OS is).
        self.assertIn('Alpine Linux', result)

    def test_host_environment_is_not_leaked(self):
        with open(os.path.join(self.workdir, 'csmakefile'), 'w') as f:
            f.write(textwrap.dedent("""\
                [~~phases~~]
                build=check env isolation
                **default=build

                [HermeticShell@fixture]
                image=alpine:latest
                exec=/bin/sh
                command=env | grep -c 'SOME_HOST_ONLY_MARKER' > out/marker_count.txt || true

                [command@run]
                description=check
                00=fixture
                """))
        env = dict(os.environ)
        # DockerRuntimeLibrary is a plain Python library, not a
        # CsmakeModules-loader-managed section -- --modules-path makes
        # HermeticShell.py itself discoverable, but the library import
        # inside it ('from DockerRuntimeLibrary.DockerRuntime import
        # DockerRuntime') is a normal Python import needing the
        # checkout's own root on sys.path.  A registry-installed
        # dependency gets this automatically (ModuleRegistry adds its
        # root to sys.path); a dev checkout needs it set explicitly.
        env['PYTHONPATH'] = DOCKER_RUNTIME_REPO
        env['PWD'] = self.workdir
        env['SOME_HOST_ONLY_MARKER'] = 'must-not-leak-into-container'
        modules_path = ':%s:%s' % (THIS_REPO, DOCKER_RUNTIME_REPO)
        completed = subprocess.run(
            [sys.executable, os.path.join(CORE_REPO, 'csmake'),
             '--modules-path=' + modules_path,
             '--command=run', 'build'],
            cwd=self.workdir, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        self.assertEqual(
            completed.returncode, 0,
            "csmake failed:\n%s" % completed.stderr.decode('utf-8', 'replace'))
        # A host-only env var must not have crossed into the container.
        with open(os.path.join(self.workdir, 'out', 'marker_count.txt')) as f:
            self.assertEqual(f.read().strip(), '0')


if __name__ == "__main__":
    unittest.main()
