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
"""Helpers shared by the module tests.

CsmakeModule subclasses need a whole csmake engine to construct, so the
tests call their methods on a bare instance (object.__new__) carrying just
the attributes the method under test touches.
"""
import subprocess


class QuietLog:
    """Swallows every logger call (devdebug, warning, passed, ...)."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

    def out(self):
        return subprocess.DEVNULL

    err = out


def bare_instance(cls, **attributes):
    instance = object.__new__(cls)
    instance.log = QuietLog()
    instance.__dict__.update(attributes)
    return instance
