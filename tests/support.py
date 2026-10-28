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
"""Test doubles shared by the module tests.

CsmakeModule subclasses need a whole csmake engine to construct, so the
tests call their methods on a bare instance (object.__new__) carrying just
the attributes the method under test touches, with the engine, metadata,
logger, subprocess and signer boundaries replaced by the doubles below.

The doubles honour the real APIs' type contracts (a signer's update() takes
bytes only, check_call takes a list of str and returns an int or raises
CalledProcessError, ...) so that a str/bytes conversion bug still fails
instead of being papered over by a forgiving fake.
"""
import subprocess

# Payload content that is deliberately not valid UTF-8 (\xc3\x28, \xa0,
# \xff), not a multiple of 4 bytes long, and contains every byte value.
BINARY = bytes(range(256)) + b'\xc3\x28\xa0\xa1 not utf-8 \xff\xfe'


class RecordingLog:
    """Logger double: every call (debug, warning, error, passed, ...) is
    recorded as (method, args); see messages()."""

    def __init__(self):
        self.calls = []
        self.out_stream = object()
        self.err_stream = object()

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.calls.append((name, args))
        return record

    def out(self):
        return self.out_stream

    def err(self):
        return self.err_stream

    def messages(self, method):
        """The formatted messages logged through log.<method>()."""
        found = []
        for name, args in self.calls:
            if name != method or not args:
                continue
            try:
                found.append(args[0] % args[1:] if len(args) > 1 else args[0])
            except (TypeError, ValueError):
                found.append(' '.join(str(a) for a in args))
        return found

    def called(self, method):
        return any(name == method for name, _ in self.calls)


def bare_instance(cls, **attributes):
    instance = object.__new__(cls)
    instance.log = RecordingLog()
    instance.__dict__.update(attributes)
    return instance


def new_packager(cls, options=None, metadata=None, engine=None, **attributes):
    """A bare Packager subclass instance with what Packager.__init__ sets."""
    return bare_instance(
        cls, options={} if options is None else options,
        metadata=metadata,
        engine=FakeEngine() if engine is None else engine,
        classifiers=None, controls={}, aspects=[], **attributes)


DEFAULT_METADATA = {
    'name': 'demo',
    'description': 'A demo',
    'about': 'Line one\nLine two',
    'packager': 'Pat Packager <pat@example.com>',
    'manufacturer': 'Demo Corp',
    'url': 'https://example.com/demo',
    'classifiers': 'License :: OSI Approved :: MIT License\n'
                   'Programming Language :: Python :: 3 :: Only',
}


def make_metadata(metadata_class, fields=None, version=None):
    """A real `metadata` module instance, built bare: the csmakefile
    [metadata] section as the packagers see it (its version formatting is
    the real thing)."""
    metadata = bare_instance(metadata_class)
    metadata.version = dict(version or {'primary': '1.2.3'})
    metadata.versionkeys = [
        key for key in ('epoch', 'primary') if key in metadata.version]
    metadata.original = dict(DEFAULT_METADATA)
    metadata.original.update(fields or {})
    metadata.original['version'] = metadata.version
    metadata.languages = {'en_US': metadata.original}
    return metadata


class FakeResult:
    """A launched step's result (and the step itself): what the engine hands
    back from launchStep()."""

    def __init__(self, passed=True, value=None):
        self.passed = passed
        self.value = value

    def _didPass(self):
        return self.passed

    def _getReturnValue(self, key):
        return self.value

    def _getResult(self):
        return self

    def getReturnValue(self, key):
        return self.value


class FakeEngine:
    """The slice of the csmake engine that Packager modules call.

    steps maps a section name to the FakeResult launchStep() returns for it
    (anything else yields None, as an unknown step would)."""

    def __init__(self, steps=None, sections=()):
        self.steps = {} if steps is None else steps
        self.sections = set(sections)
        self.launched = []
        self.aspects_launched = []

    def getPhase(self):
        return 'package'

    def lookupSection(self, name):
        return {} if name in self.sections else None

    def launchStep(self, name, phase=None, **kwargs):
        self.launched.append((name, phase, kwargs))
        return self.steps.get(name)

    def launchAspects(self, aspects, joinpoint, phase, step, options,
                      extraOptions=None):
        self.aspects_launched.append((aspects, joinpoint, extraOptions))


class FakeFileMapping:
    def __init__(self, specs):
        self.specs = specs

    def iterspecs(self):
        return iter(self.specs)


class FakeFileManager:
    """FileManager.parseFileMap() returning scripted (froms, tos) specs."""

    def __init__(self, specs):
        self.specs = specs
        self.maps = []

    def parseFileMap(self, text):
        self.maps.append(text)
        return FakeFileMapping(self.specs)


class FakeSubprocess:
    """Replaces the `subprocess` module as RpmPackage sees it (only
    check_call is used, to run dd).

    Type contract: the command is a list of str, the result is the int 0 or
    CalledProcessError is raised.  Every call is recorded as (command,
    kwargs).  The effect of dd is emulated on the real files named by the
    if=/of= operands, faithfully enough that the command line matters:
    with oflag=append the data lands after what is already in of=,
    otherwise it overwrites from offset 0 (and truncates unless
    conv=notrunc).  dd_flavour='bsd' rejects oflag= the way macOS's dd does
    (it only knows GNU's); fail_call=N makes the Nth call exit non-zero."""

    CalledProcessError = subprocess.CalledProcessError

    def __init__(self, dd_flavour='gnu', fail_call=None):
        self.dd_flavour = dd_flavour
        self.fail_call = fail_call
        self.calls = []

    def check_call(self, command, **kwargs):
        if not isinstance(command, (list, tuple)) or \
                not all(isinstance(part, str) for part in command):
            raise TypeError("command must be a list of str: %r" % (command,))
        self.calls.append((list(command), kwargs))
        if self.fail_call == len(self.calls):
            raise subprocess.CalledProcessError(1, command)
        operands = dict(part.split('=', 1) for part in command[1:])
        if self.dd_flavour == 'bsd' and 'oflag' in operands:
            raise subprocess.CalledProcessError(1, command)
        with open(operands['if'], 'rb') as source:
            data = source.read()
        if operands.get('oflag') == 'append':
            mode = 'ab'
        elif operands.get('conv') == 'notrunc':
            mode = 'r+b'
        else:
            mode = 'wb'
        with open(operands['of'], mode) as target:
            target.write(data)
        return 0


class FakeSigner:
    """Stands in for csmake-swak's Signature.Signer, the object that drives
    `gpg -b` (a thread feeding gpg's stdin through os.write).

    update() takes bytes only, as os.write does, and not after digest() or
    close().  exit_status is what gpg exited with: non-zero means didFail()
    and an empty signature, as for a bad --local-user key."""

    def __init__(self, signature=b'\x89\x01-gpg-signature-', exit_status=0,
                 signtype='GPG'):
        self.signature = signature
        self.exit_status = exit_status
        self._signtype = signtype
        self.fed = []
        self.finished = False

    def signtype(self):
        return self._signtype

    def update(self, buf):
        if self.finished:
            raise RuntimeError("update may not be called after digest or close")
        if not isinstance(buf, (bytes, bytearray)):
            raise TypeError("a bytes-like object is required, not %r" %
                            type(buf).__name__)
        self.fed.append(bytes(buf))

    def digest(self):
        self.finished = True
        return b'' if self.exit_status else self.signature

    def close(self):
        self.finished = True

    def didFail(self):
        return self.exit_status != 0

    def fed_bytes(self):
        return b''.join(self.fed)
