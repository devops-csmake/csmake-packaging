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
"""Binary-structure tests for RpmPackage's Python 3 port.

RpmPackage builds the RPM lead, the signature/metadata headers and the
cpio payload by hand.  Under Python 2 every one of those was a 'str'; the
port had to turn them into 'bytes' (struct.pack, hashlib, zlib and binary
file I/O all insist on it), fix octal literals, and stop calling .sort() on
a dict view.  A py_compile pass cannot see any of that, so these tests
build the real structures and then read them back with small independent
parsers (rpmparse.py) rather than re-using the code under test.  Binary
file I/O and the byte layouts are the thing under test here, so these use
in-memory buffers and real temp files; the engine, subprocess and signer
boundaries are exercised in the other Rpm test modules.

CsmakeModule subclasses need a whole csmake engine to construct, so the
RpmPackage methods are called on a bare instance (see support.py).
"""
import copy
import hashlib
import io
import os
import stat
import struct
import tarfile
import tempfile
import unittest
import zlib

from . import csmake_alias
from .rpmparse import by_tag, parse_header, read_cpio, read_ints, read_strings
from .rpmparse import RPM_HEADER_MAGIC
from .support import BINARY, bare_instance


def load_rpm_package():
    return csmake_alias.import_plugin('RpmPackage').RpmPackage


class RpmTestCase(unittest.TestCase):

    def setUp(self):
        self.RpmPackage = load_rpm_package()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, *parts):
        return os.path.join(self.tmp.name, *parts)

    def write_file(self, name, content):
        path = self.path(name)
        with open(path, 'wb') as out:
            out.write(content)
        return path

    def new_cpio(self, clearDigests=None, compressedDigests=None):
        self.cpio_out = io.BytesIO()
        return self.RpmPackage.CpioFile(
            bare_instance(self.RpmPackage),
            self.cpio_out,
            zlib.compressobj(9, zlib.DEFLATED, 31),  # gzip, as in _finishArchive
            [] if clearDigests is None else clearDigests,
            [] if compressedDigests is None else compressedDigests)

    def file_info(self, name, size, mode=0o755, rpmflags=0):
        info = self.RpmPackage.RpmFileInfo(name)
        info.mode = mode
        info.size = size
        info.uname = info.gname = 'root'
        info.rpmflags = rpmflags
        return info


class testRpmRecords(RpmTestCase):
    """The typed records that end up in an RPM header's store."""

    def test_string_record_is_nul_terminated_utf8_bytes(self):
        R = self.RpmPackage
        record = R.RpmRecordString(1000, 'café')
        self.assertEqual(b'caf\xc3\xa9\x00', record.data)
        self.assertEqual(6, record.datatype)
        self.assertEqual(1, record.entries)
        self.assertEqual(6, record.dataSize())
        # already-bytes input (e.g. a digest) is passed through untouched
        self.assertEqual(b'ab\x00', R.RpmRecordString(269, b'ab').data)

        out = io.BytesIO()
        record.writeHeader(out, 12, [])
        record.writeStore(out, [])
        self.assertEqual(
            struct.pack('!4I', 1000, 6, 12, 1) + b'caf\xc3\xa9\x00',
            out.getvalue())

    def test_string_array_record_encodes_each_element(self):
        R = self.RpmPackage
        record = R.RpmRecordStringArray(1117, ['a', b'bc', 'café', ''])
        self.assertEqual(b'a\x00bc\x00caf\xc3\xa9\x00\x00', record.data)
        self.assertEqual(8, record.datatype)
        self.assertEqual(4, record.entries)
        self.assertEqual(9, R.RpmRecordI18NStringArray(1, ['x']).datatype)
        with self.assertRaises(ValueError):
            R.RpmRecordStringArray(1117, 'not-a-list')

    def test_numeric_records_pack_big_endian_from_scalars_or_sequences(self):
        R = self.RpmPackage
        ints = R.RpmRecordInt32(1028, [1, 2, 70000])
        self.assertEqual(struct.pack('!3l', 1, 2, 70000), ints.data)
        self.assertEqual((4, 3, 4), (ints.datatype, ints.entries, ints.alignment))
        self.assertEqual(struct.pack('!l', 7), R.RpmRecordInt32(1, 7).data)
        self.assertEqual(2, R.RpmRecordInt32(1, (1, 2)).entries)
        # values past the signed limit switch the whole record to unsigned
        self.assertEqual(
            struct.pack('!L', 2 ** 31 + 5), R.RpmRecordInt32(1, 2 ** 31 + 5).data)
        modes = R.RpmRecordInt16(1030, [0o100644, 0o120777])
        self.assertEqual(struct.pack('!2H', 0o100644, 0o120777), modes.data)
        self.assertEqual(2, modes.alignment)


class testRpmRecordManager(RpmTestCase):
    """RpmRecordManager.write() lays out an RPM header structure."""

    def build_manager(self):
        R = self.RpmPackage
        manager = R.RpmRecordManager(63)
        # Added out of tag order on purpose: write() has to sort the index
        # (it used to call .sort() on dict.keys(), which py3 views lack).
        manager.addRecord(R.RpmRecordStringArray(1117, ['b', 'a']))
        manager.addRecord(R.RpmRecordInt16(1030, [33188]))
        manager.addRecord(R.RpmRecordInt32(1006, 7))
        manager.addRecord(R.RpmRecordBinary(1004, b'\x00\xff\x80'))
        manager.addRecord(R.RpmRecordString(1000, 'abcde'))
        manager.addRecord(R.RpmRecordStringArray(100, ['C']))
        return manager

    def test_header_has_sorted_index_aligned_store_and_region_trailer(self):
        out = io.BytesIO()
        self.build_manager().write(out)
        blob = out.getvalue()

        self.assertEqual(RPM_HEADER_MAGIC + b'\x00' * 4, blob[:8])
        entries, store, end = parse_header(blob)
        self.assertEqual(len(blob), end)
        # The region tag comes first (offset: where its trailer sits in the
        # store), then every record in ascending tag order.
        self.assertEqual(
            [(63, 7, 22, 16),
             (100, 8, 0, 1),
             (1000, 6, 2, 1),
             (1004, 7, 8, 3),
             (1006, 4, 12, 1),    # pushed from 11 to 12 by 4-byte alignment
             (1030, 3, 16, 1),
             (1117, 8, 18, 2)],
            entries)
        trailer = struct.pack('!IIiI', 63, 7, -(7 * 16), 16)
        self.assertEqual(
            b'C\x00' + b'abcde\x00' + b'\x00\xff\x80'
            + b'\x00'                                   # alignment padding
            + struct.pack('!l', 7) + struct.pack('!H', 33188)
            + b'b\x00a\x00' + trailer,
            store)

    def test_hash_functions_see_exactly_the_bytes_written(self):
        out = io.BytesIO()
        sha, md5 = hashlib.sha1(), hashlib.md5()
        self.build_manager().write(out, [sha, md5])
        self.assertEqual(hashlib.sha1(out.getvalue()).digest(), sha.digest())
        self.assertEqual(hashlib.md5(out.getvalue()).digest(), md5.digest())


class testRpmLead(RpmTestCase):

    def test_lead_is_96_bytes_with_magic_and_padded_name(self):
        package = bare_instance(self.RpmPackage, leadmagic=b'\xed\xab\xee\xdb')
        lead = package._makeLead(3, 0, 0, 0xff, 'demo-1.0-1', 1, 5)
        self.assertEqual(96, len(lead))
        self.assertEqual(b'\xed\xab\xee\xdb', lead[:4])
        self.assertEqual((3, 0, 0, 0xff), struct.unpack_from('!2b2h', lead, 4))
        self.assertEqual(b'demo-1.0-1' + b'\x00' * 56, lead[10:76])
        self.assertEqual((1, 5), struct.unpack_from('!2h', lead, 76))
        self.assertEqual(b'\x00' * 16, lead[80:])


class testRpmFileInfo(RpmTestCase):
    """RpmFileInfo replaces tarfile.TarInfo, which is __slots__-based."""

    def test_tarinfo_cannot_carry_rpmflags_but_rpmfileinfo_can(self):
        if not hasattr(tarfile.TarInfo, '__slots__'):
            self.skipTest("this Python's TarInfo accepts arbitrary attributes")
        with self.assertRaises(AttributeError):
            tarfile.TarInfo('x').rpmflags = 1
        info = self.RpmPackage.RpmFileInfo('x')
        info.rpmflags = 5
        self.assertEqual(5, copy.deepcopy(info).rpmflags)  # used for directories


class testRpmCpioPayload(RpmTestCase):
    """CpioFile writes the gzip'd newc archive that is the RPM's payload."""

    def payload_tuples(self):
        blob = self.write_file('blob.bin', BINARY)
        hello = self.write_file('hello.txt', b'hello\n')
        # Out of name order: writePayload has to sort them (RPM binary
        # searches the file list).
        return [
            (None, hello, '/usr/share/hello.txt',
             self.file_info('/usr/share/hello.txt', 6, 0o644)),
            (None, blob, '/usr/bin/blob.bin',
             self.file_info('/usr/bin/blob.bin', len(BINARY), 0o755,
                            rpmflags=1))]

    def test_payload_round_trips_binary_files_with_digests(self):
        clear, compressed = hashlib.sha1(), hashlib.md5()
        cpio = self.new_cpio([clear], [compressed])
        written = cpio.writePayload(self.payload_tuples())

        gzipped = self.cpio_out.getvalue()
        self.assertEqual(len(gzipped), written)
        raw = zlib.decompress(gzipped, 31)
        entries, end = read_cpio(raw)   # also asserts the 4 byte alignment
        self.assertEqual(len(raw), end)
        self.assertEqual(len(raw), cpio.getTotalUncompressedSize())
        self.assertEqual(len(BINARY) + 6, cpio.getTotalUncompressedFileSize())
        self.assertEqual(
            [b'./usr/bin/blob.bin', b'./usr/share/hello.txt'],
            [e['name'] for e in entries])
        self.assertEqual([BINARY, b'hello\n'], [e['data'] for e in entries])
        self.assertEqual(
            [stat.S_IFREG | 0o755, stat.S_IFREG | 0o644],
            [e['mode'] for e in entries])
        self.assertEqual(
            [hashlib.md5(BINARY).hexdigest(), hashlib.md5(b'hello\n').hexdigest()],
            cpio.fileMD5s)
        # The digests handed in were fed the very bytes that were written.
        self.assertEqual(hashlib.sha1(raw).digest(), clear.digest())
        self.assertEqual(hashlib.md5(gzipped).digest(), compressed.digest())

    def test_payload_tags_describe_the_files(self):
        cpio = self.new_cpio()
        cpio.writePayload(self.payload_tuples())
        manager = self.RpmPackage.RpmRecordManager(63)
        cpio.addFileTagsToManager(manager)
        out = io.BytesIO()
        manager.write(out)

        entries, store, _ = parse_header(out.getvalue())
        tags = by_tag(entries)
        self.assertEqual(
            [b'blob.bin', b'hello.txt'],
            read_strings(store, tags[1117][1], tags[1117][2]))      # BASENAMES
        self.assertEqual(
            [b'/usr/bin/', b'/usr/share/'],
            read_strings(store, tags[1118][1], tags[1118][2]))      # DIRNAMES
        self.assertEqual([0, 1], read_ints(store, tags[1116], 'l'))  # DIRINDEXES
        self.assertEqual([len(BINARY), 6], read_ints(store, tags[1028], 'l'))
        # Regular-file modes exceed 32767, so they must be written unsigned.
        self.assertEqual(3, tags[1030][0])
        self.assertEqual([0o100755, 0o100644], read_ints(store, tags[1030], 'H'))
        self.assertEqual([1, 0], read_ints(store, tags[1037], 'l'))  # FILEFLAGS
        self.assertEqual(
            [hashlib.md5(BINARY).hexdigest().encode(),
             hashlib.md5(b'hello\n').hexdigest().encode()],
            read_strings(store, tags[1035][1], tags[1035][2]))      # FILEMD5S
        self.assertEqual(
            [b'root', b'root'],
            read_strings(store, tags[1039][1], tags[1039][2]))      # FILEUSERNAME
        self.assertEqual(
            [b'', b''],
            read_strings(store, tags[1036][1], tags[1036][2]))      # FILELINKTOS

    def test_non_ascii_file_name_is_sized_in_bytes(self):
        name = '/usr/share/café.txt'
        source = self.write_file('menu.txt', b'x')
        cpio = self.new_cpio()
        cpio.writePayload([(None, source, name, self.file_info(name, 1, 0o644))])

        entries, _ = read_cpio(zlib.decompress(self.cpio_out.getvalue(), 31))
        encoded = ('.' + name).encode('utf-8')
        self.assertEqual([encoded], [e['name'] for e in entries])
        self.assertEqual(len(encoded) + 1, entries[0]['namesize'])
        self.assertEqual([b'x'], [e['data'] for e in entries])

    def test_symlink_header_from_a_plain_tarinfo(self):
        # Aspects (e.g. IdentifyShlibsDebian) hand the packager real
        # tarfile.TarInfo objects, which have no rpmflags attribute.
        info = tarfile.TarInfo('./usr/lib/libfoo.so')
        info.type = tarfile.SYMTYPE
        info.linkname = 'libfoo.so.1'
        info.mode = 0o777
        info.size = len(info.linkname)
        cpio = self.new_cpio()
        # NOTE: _addLinkToCpio is not used here; it calls the misspelled
        # self.createCpioHeader (a pre-existing bug unrelated to Python 3).
        header = cpio._createCpioHeader(info)

        self.assertEqual(b'070701', header[:6])
        self.assertEqual(
            stat.S_IFLNK | 0o777, int(header[6 + 8:6 + 16], 16))  # c_mode
        self.assertEqual(b'./usr/lib/libfoo.so', header[110:])
        self.assertEqual([stat.S_IFLNK | 0o777], cpio.fileModes)
        self.assertEqual(['libfoo.so.1'], cpio.fileLinkTos)
        self.assertEqual([0], cpio.fileFlags)


if __name__ == '__main__':
    unittest.main()
