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
"""Independent readers for the RPM structures RpmPackage writes.

Deliberately written separately from the code under test (and from its
struct formats' helper methods) so a test reading an RPM back is not just
asking the writer to agree with itself.
"""
import struct

RPM_HEADER_MAGIC = b'\x8e\xad\xe8\x01'


def parse_header(blob, pos=0):
    """Reads one RPM header structure (signature or metadata).

    Returns (entries, store, end) where entries is a list of
    (tag, type, offset, count) in index order."""
    if blob[pos:pos + 4] != RPM_HEADER_MAGIC:
        raise AssertionError("no RPM header magic at %d: %r" % (
            pos, blob[pos:pos + 4]))
    reserved, nindex, hsize = struct.unpack_from('!III', blob, pos + 4)
    if reserved != 0:
        raise AssertionError("reserved header bytes are not zero")
    entries = [
        struct.unpack_from('!IIII', blob, pos + 16 + 16 * i)
        for i in range(nindex)]
    start = pos + 16 + 16 * nindex
    return entries, blob[start:start + hsize], start + hsize


def by_tag(entries):
    return {tag: (datatype, offset, count)
            for tag, datatype, offset, count in entries}


def read_strings(store, offset, count):
    strings = []
    for _ in range(count):
        end = store.index(b'\x00', offset)
        strings.append(store[offset:end])
        offset = end + 1
    return strings


def read_ints(store, entry, fmt):
    datatype, offset, count = entry
    return list(struct.unpack_from('!%d%s' % (count, fmt), store, offset))


def read_cpio(blob):
    """Reads an SVR4 'newc' cpio stream, checking its alignment rules.

    Returns (entries, end_offset); the TRAILER!!! entry is consumed but
    not returned."""
    entries = []
    pos = 0
    while True:
        if blob[pos:pos + 6] != b'070701':
            raise AssertionError("no newc magic at %d: %r" % (
                pos, blob[pos:pos + 6]))
        fields = [
            int(blob[pos + 6 + 8 * i:pos + 14 + 8 * i], 16)
            for i in range(13)]
        mode, filesize, namesize = fields[1], fields[6], fields[11]
        name_end = pos + 110 + namesize
        if blob[name_end - 1:name_end] != b'\x00':
            raise AssertionError("name at %d is not NUL terminated" % pos)
        name = blob[pos + 110:name_end - 1]
        data_start = (name_end + 3) & ~3
        data = blob[data_start:data_start + filesize]
        pos = (data_start + filesize + 3) & ~3
        if name == b'TRAILER!!!':
            return entries, pos
        entries.append({'name': name, 'mode': mode, 'data': data,
                        'namesize': namesize})


class ParsedRpm:
    """A whole .rpm file taken apart: lead, signature header, metadata
    header and the (still compressed) payload."""

    def __init__(self, blob):
        self.blob = blob
        self.lead = blob[:96]
        sig_entries, self.sig_store, sig_end = parse_header(blob, 96)
        self.sig = by_tag(sig_entries)
        header_start = (sig_end + 7) & ~7
        #What sits between the signature header and the 8 byte boundary
        self.padding = blob[sig_end:header_start]
        entries, self.store, header_end = parse_header(blob, header_start)
        self.tags = by_tag(entries)
        self.header = blob[header_start:header_end]
        self.payload = blob[header_end:]

    @classmethod
    def from_file(cls, path):
        with open(path, 'rb') as rpm:
            return cls(rpm.read())

    def strings(self, tag):
        _, offset, count = self.tags[tag]
        return read_strings(self.store, offset, count)

    def ints(self, tag, fmt='l'):
        return read_ints(self.store, self.tags[tag], fmt)

    def sig_strings(self, tag):
        _, offset, count = self.sig[tag]
        return read_strings(self.sig_store, offset, count)

    def sig_ints(self, tag, fmt='l'):
        return read_ints(self.sig_store, self.sig[tag], fmt)

    def sig_binary(self, tag):
        _, offset, count = self.sig[tag]
        return self.sig_store[offset:offset + count]
