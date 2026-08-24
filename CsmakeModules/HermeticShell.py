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
from CsmakeModules.Shell import Shell
import os


class HermeticShell(Shell):
    """Purpose: Execute a shell command inside a pinned container image
       Type: Module   Library: csmake-packaging
       Requires:
           exec: docker
       Options:
           (all Shell options -- command(<phase>), env, exec, line-pad --
            apply unchanged; see: csmake --list-type Shell)

           caps    - Capability name (the same vocabulary as a module
                     docstring's Requires: caps: field -- see
                     docs/MODULE_ECOSYSTEM_DESIGN.md) naming which pinned
                     image to run the command in.  Looked up in this
                     class's HERMETIC_CAP_IMAGES table.
           image   - (OPTIONAL) Explicit image, overriding the caps lookup.
                     Use this for an image HERMETIC_CAP_IMAGES doesn't
                     know about yet.
           workdir - (OPTIONAL) In-container working directory that the
                     current host directory is mounted at.
                     Default: /workspace
       Notes:
           This is a minimal, opt-in escape hatch, not automatic
           orchestration: a build chooses HermeticShell instead of Shell
           for a step whose capability isn't satisfiable on the host (a
           section declaring the same capability under a module
           docstring's Requires: caps: field would otherwise only be
           reported as unavailable by CliDriver's preflight check, never
           acted on -- csmake knows and tells, never installs).  There is
           no automatic host-vs-container fallback; if you want that,
           write it as a build-time decision in your own csmakefile.

           Hermetic means hermetic: unlike Shell, the host's environment
           is NOT passed into the container -- only variables explicitly
           supplied via env= ShellEnv references cross the boundary.  The
           current host working directory is bind-mounted at 'workdir'
           inside the container; command(<phase>) runs there via the
           container's own shell ('exec', default /bin/bash).
       Examples:
           [HermeticShell@build-rpm]
           caps = rpmbuild-env
           command = rpmbuild -bb my-package.spec

           [HermeticShell@custom-image]
           image = my-registry.internal/build-env:latest
           command = make all
       See Also:
           csmake --list-type Shell
    """

    # exec name -> pinned image.  Data only, like SystemPackageHints in
    # core -- a package or a walled garden can extend/override this at
    # the point they consume it (subclass, or a future config layer).
    HERMETIC_CAP_IMAGES = {
        'rpmbuild-env': 'fedora:latest',
        'debian-build-env': 'debian:bookworm',
    }

    def _getStartingEnvironment(self, options):
        # Hermetic: don't leak the host's environment into the container.
        # Only env= ShellEnv references (merged in afterward by
        # Shell._getEnvironment) and Shell's own _MAPPING/_froms/_tos
        # additions cross the boundary.
        return {}

    def _resolveImage(self, options):
        if 'image' in options:
            return self.env.doSubstitutions(options['image'].strip())
        capname = options.get('caps', '').strip()
        if not capname:
            raise ValueError(
                "HermeticShell requires either 'caps' or 'image'")
        image = self.HERMETIC_CAP_IMAGES.get(capname)
        if not image:
            raise ValueError(
                "No pinned image for capability '%s' in "
                "HermeticShell.HERMETIC_CAP_IMAGES" % capname)
        return image

    def _executeShell(self, command, env, execer='/bin/bash'):
        from DockerRuntimeLibrary.DockerRuntime import DockerRuntime
        runner = DockerRuntime()
        try:
            runner.execute(
                image=self._image,
                env=env,
                host_cwd=os.getcwd(),
                workdir=self._workdir,
                entrypoint=execer,
                args=['-c', command])
            return 0
        except RuntimeError as e:
            self.log.error("HermeticShell: %s", e)
            return 1

    def default(self, options):
        self._image = self._resolveImage(options)
        self._workdir = options.get('workdir', '/workspace').strip()
        return Shell.default(self, options)
