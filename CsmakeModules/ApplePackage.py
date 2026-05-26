# <copyright>
# (c) Copyright 2025 Autumn Patterson
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
from CsmakeModules.Packager import Packager
import datetime
import gzip
import hashlib
import io
import os
import os.path
import shutil
import stat
import struct
import sys
import time
import zlib


# ======================================================================
# cpio odc (POSIX.1 octal) format helpers — what macOS pkgutil expects
# ======================================================================

def _cpio_entry_odc(arcpath, content, mode, uid, gid, mtime, ino, dev=0, rdev=0):
    """Return bytes for one cpio odc entry (magic 070707, no padding).

    arcpath  - path string as it appears in the archive (e.g. './bin/mytool')
    content  - bytes or None (None → empty, used for directories)
    """
    if content is None:
        content = b''
    name_b   = arcpath.encode('utf-8') + b'\x00'
    namesize = len(name_b)
    filesize = len(content)
    nlink    = 2 if stat.S_ISDIR(mode) else 1

    # 76-byte octal ASCII header
    header = (
        '070707'
        + '%06o' % (dev     & 0o777777)
        + '%06o' % (ino     & 0o777777)
        + '%06o' % mode
        + '%06o' % uid
        + '%06o' % gid
        + '%06o' % nlink
        + '%06o' % (rdev    & 0o777777)
        + '%011o' % mtime
        + '%06o'  % namesize
        + '%011o' % filesize
    ).encode('ascii')

    # odc has no padding — name and data follow immediately
    return header + name_b + content


def _build_payload(staging_dir, file_records):
    """Build a gzip-compressed cpio odc archive from staging_dir.

    file_records maps staging_path → {type, mode, uid, gid}
    Returns gzip-compressed bytes stored as-is in the XAR Payload entry.
    """
    buf = bytearray()
    ino = 1
    now = int(time.time())

    # Root '.' entry
    buf += _cpio_entry_odc('.', None,
                           mode=stat.S_IFDIR | 0o755,
                           uid=0, gid=0, mtime=now, ino=ino)
    ino += 1

    # Collect all paths in sorted order, dirs before their contents
    all_paths = []
    for root, dirs, files in os.walk(staging_dir):
        dirs.sort()
        files.sort()
        for d in dirs:
            all_paths.append(('dir', os.path.join(root, d)))
        for f in files:
            all_paths.append(('file', os.path.join(root, f)))

    for ftype, fpath in all_paths:
        rel = os.path.relpath(fpath, staging_dir)
        arcpath = './' + rel
        rec = file_records.get(fpath, {})
        uid   = rec.get('uid', 0)
        gid   = rec.get('gid', 0)
        mtime = int(os.path.getmtime(fpath))

        if os.path.islink(fpath):
            target = os.readlink(fpath)
            mode   = rec.get('mode', stat.S_IFLNK | 0o777)
            buf += _cpio_entry_odc(arcpath, target.encode('utf-8'),
                                   mode=mode, uid=uid, gid=gid, mtime=mtime, ino=ino)
        elif ftype == 'dir':
            mode = rec.get('mode', stat.S_IFDIR | 0o755)
            buf += _cpio_entry_odc(arcpath, None,
                                   mode=mode, uid=uid, gid=gid, mtime=mtime, ino=ino)
        else:
            mode = rec.get('mode', stat.S_IFREG | 0o644)
            with open(fpath, 'rb') as fh:
                content = fh.read()
            buf += _cpio_entry_odc(arcpath, content,
                                   mode=mode, uid=uid, gid=gid, mtime=mtime, ino=ino)
        ino += 1

    # TRAILER (namesize=11 for 'TRAILER!!!\x00', nlink=1 required)
    buf += _cpio_entry_odc('TRAILER!!!', None, mode=0, uid=0, gid=0, mtime=0, ino=0)

    # Gzip-compress so pkgutil can decompress internally
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode='wb', mtime=0) as gz:
        gz.write(bytes(buf))
    return out.getvalue()


# ======================================================================
# Apple BOM (Bill of Materials) format
# ======================================================================

class _BOMWriter:
    """Low-level BOM block/var manager."""

    # Apple's mkbom pre-allocates this many block table slots.
    _NUM_BLOCK_SLOTS = 2730

    def __init__(self):
        self._blocks = [b'']   # index 0 is always the null block
        self._vars   = []      # ordered list of (block_idx, name)

    def add_block(self, data):
        idx = len(self._blocks)
        self._blocks.append(bytes(data))
        return idx

    def add_var(self, name, block_idx):
        """Register a named var pointing to an already-created block."""
        self._vars.append((block_idx, name))

    def serialize(self):
        HEADER_SIZE = 32

        # Assign absolute file offsets to each non-null block
        offsets = [0]  # block 0: null
        cursor  = HEADER_SIZE
        for blk in self._blocks[1:]:
            offsets.append(cursor)
            cursor += len(blk)

        # numBlocks = number of non-null active blocks (matches Apple convention)
        non_null = len(self._blocks) - 1

        # Vars section: count(4) then for each var: block_idx(4) + name_len(1) + name
        vars_buf = bytearray(struct.pack('>I', len(self._vars)))
        for bidx, name in self._vars:
            name_b = name.encode('ascii')
            vars_buf += struct.pack('>I', bidx)
            vars_buf += struct.pack('>B', len(name_b))
            vars_buf += name_b
        vars_size = len(vars_buf)

        # Apple layout: [header][blocks][vars][block_table]
        # vars section comes immediately after the last block, before the index.
        vars_offset  = cursor
        index_offset = vars_offset + vars_size

        # Block table pre-allocated to _NUM_BLOCK_SLOTS entries
        num_slots  = max(self._NUM_BLOCK_SLOTS, len(self._blocks))
        # lsbom reads 52 bytes past the last block table entry; Apple's mkbom
        # always pads to make index_size = 4 + slots*8 + 52.
        _BLOCK_TABLE_TRAIL = 52
        index_size = 4 + num_slots * 8 + _BLOCK_TABLE_TRAIL

        # Header: magic(8) + version(4) + numBlocks(4) +
        #         indexOffset(4) + indexSize(4) + varsOffset(4) + varsSize(4)
        header = (
            b'BOMStore'
            + struct.pack('>I', 1)
            + struct.pack('>I', non_null)
            + struct.pack('>I', index_offset)
            + struct.pack('>I', index_size)
            + struct.pack('>I', vars_offset)
            + struct.pack('>I', vars_size)
        )  # 32 bytes

        # Block table with pre-allocated empty slots
        block_table = bytearray(struct.pack('>I', num_slots))
        for i in range(num_slots):
            if 0 < i < len(self._blocks):
                block_table += struct.pack('>II', offsets[i], len(self._blocks[i]))
            else:
                block_table += struct.pack('>II', 0, 0)
        block_table += b'\x00' * _BLOCK_TABLE_TRAIL

        result = bytearray(header)
        for blk in self._blocks[1:]:
            result += blk
        result += vars_buf
        result += block_table
        return bytes(result)


def _build_bom(entries):
    """Build a BOM binary from a list of file-info dicts.

    Each dict must have: path (e.g. '.', './bin', './bin/foo'),
    type ('file'|'dir'|'symlink'), mode (full st_mode), uid, gid,
    size, mtime.  Optional: crc32 (for files/symlinks).
    Entries MUST be sorted by path so parents come before children.

    Apple BOM leaf entry format (from reverse-engineering mkbom):
      leaf entry = (infoPtr_blk, file_blk)
      infoPtr_blk  : {serial(4), pathInfo2_blk(4)}   — 8 bytes
      file_blk     : {parent_serial(4), basename_nul} — variable
      pathInfo2_blk: BOMPathInfo2 — 31 bytes (dirs) or 35 bytes (files)
    serial = 1-indexed position of this entry in the leaf.
    parent_serial = serial of the parent entry (0 for the root '.').
    """
    bom = _BOMWriter()
    path_to_serial = {}   # path → 1-indexed serial
    leaf_entries   = []   # list of (infoPtr_blk, file_blk)

    for serial, entry in enumerate(entries, start=1):
        path = entry['path']
        path_to_serial[path] = serial

        # Parent serial (0 for root)
        if path == '.':
            parent_serial = 0
            basename      = '.'
        else:
            parent_path   = os.path.dirname(path)
            parent_serial = path_to_serial.get(parent_path, 0)
            basename      = os.path.basename(path)

        # BOMFile block: {parent_serial(4), basename_nul}
        file_data = struct.pack('>I', parent_serial) + basename.encode('utf-8') + b'\x00'
        file_blk  = bom.add_block(file_data)

        # BOMPathInfo2: base (23 bytes, has_extra=1) + type-specific extra
        ptype = {'file': 1, 'dir': 2, 'symlink': 4}.get(entry['type'], 1)
        mode  = entry.get('mode',  stat.S_IFREG | 0o644)
        uid   = entry.get('uid',   0)
        gid   = entry.get('gid',   0)
        mtime = entry.get('mtime', 0)
        size  = entry.get('size',  0)
        # arch=0x000f matches Apple's mkbom output; has_extra=1 always
        base = struct.pack('>BBHHIIIIB',
                           ptype, 1, 0x000f, mode & 0xFFFF,
                           uid, gid, mtime, size, 1)
        if entry['type'] == 'dir':
            extra = b'\x00' * 8           # 31 bytes total
        else:
            crc   = entry.get('crc32', 0)
            extra = struct.pack('>I', crc) + b'\x00' * 8   # 35 bytes total
        info_blk = bom.add_block(base + extra)

        # InfoPtr block: {serial(4), pathInfo2_blk(4)}
        info_ptr_blk = bom.add_block(struct.pack('>II', serial, info_blk))

        leaf_entries.append((info_ptr_blk, file_blk))

    # ---- Paths leaf (BOMPaths), padded to blockSize=4096 ----
    leaf_raw  = struct.pack('>HHII', 1, len(leaf_entries), 0, 0)
    leaf_raw += b''.join(struct.pack('>II', ip, fb) for ip, fb in leaf_entries)
    paths_leaf_idx = bom.add_block(leaf_raw.ljust(4096, b'\x00'))

    # ---- Paths BOMTree ----
    paths_tree_idx = bom.add_block(
        b'tree' + struct.pack('>IIIIB', 1, paths_leaf_idx, 4096, len(entries), 0))

    # ---- HLIndex: empty BOMTree, blockSize=4096 ----
    hl_leaf_idx = bom.add_block(struct.pack('>HHII', 1, 0, 0, 0).ljust(4096, b'\x00'))
    hl_tree_idx = bom.add_block(
        b'tree' + struct.pack('>IIIIB', 1, hl_leaf_idx, 4096, 0, 0))

    # ---- VIndex: empty BOMTree (blockSize=128) behind a 13-byte header ----
    vi_leaf_idx   = bom.add_block(struct.pack('>HHII', 1, 0, 0, 0).ljust(128, b'\x00'))
    vi_tree_idx   = bom.add_block(
        b'tree' + struct.pack('>IIIIB', 1, vi_leaf_idx, 128, 0, 0))
    vi_header_idx = bom.add_block(struct.pack('>IIIB', 1, vi_tree_idx, 0, 0))

    # ---- Size64: empty BOMTree, blockSize=4096 ----
    s64_leaf_idx = bom.add_block(struct.pack('>HHII', 1, 0, 0, 0).ljust(4096, b'\x00'))
    s64_tree_idx = bom.add_block(
        b'tree' + struct.pack('>IIIIB', 1, s64_leaf_idx, 4096, 0, 0))

    # ---- BomInfo (28 bytes): version, numPaths, 1, 0, 0, 0, 0 ----
    # numPaths = len(entries) + 1, matching Apple's mkbom convention
    bom_info_idx = bom.add_block(struct.pack('>IIIIIII', 1, len(entries) + 1, 1, 0, 0, 0, 0))

    # ---- Register vars in Apple's canonical order ----
    bom.add_var('BomInfo', bom_info_idx)
    bom.add_var('Paths',   paths_tree_idx)
    bom.add_var('HLIndex', hl_tree_idx)
    bom.add_var('VIndex',  vi_header_idx)
    bom.add_var('Size64',  s64_tree_idx)

    return bom.serialize()


def _collect_bom_entries(staging_dir, file_records):
    """Return a sorted list of BOM entry dicts for every item in staging_dir."""
    now = int(time.time())
    entries = []

    # Root '.' entry
    entries.append({
        'path': '.', 'type': 'dir',
        'mode': stat.S_IFDIR | 0o755,
        'uid': 0, 'gid': 0, 'size': 0, 'mtime': now,
    })

    for root, dirs, files in os.walk(staging_dir, followlinks=False):
        dirs.sort()
        files.sort()
        for name in dirs + files:
            full = os.path.join(root, name)
            rel  = os.path.relpath(full, staging_dir)
            arc  = './' + rel
            rec  = file_records.get(full, {})
            s    = os.stat(full, follow_symlinks=False)

            if os.path.islink(full):
                entries.append({
                    'path': arc, 'type': 'symlink',
                    'mode': stat.S_IFLNK | 0o777,
                    'uid':  rec.get('uid', s.st_uid),
                    'gid':  rec.get('gid', s.st_gid),
                    'size': s.st_size,
                    'mtime': int(s.st_mtime),
                    'link': os.readlink(full),
                })
            elif os.path.isdir(full):
                mode = rec.get('mode', stat.S_IFDIR | 0o755)
                entries.append({
                    'path': arc, 'type': 'dir',
                    'mode': mode,
                    'uid':  rec.get('uid', s.st_uid),
                    'gid':  rec.get('gid', s.st_gid),
                    'size': 0,
                    'mtime': int(s.st_mtime),
                })
            else:
                mode = rec.get('mode', stat.S_IFREG | 0o644)
                entries.append({
                    'path': arc, 'type': 'file',
                    'mode': mode,
                    'uid':  rec.get('uid', s.st_uid),
                    'gid':  rec.get('gid', s.st_gid),
                    'size': s.st_size,
                    'mtime': int(s.st_mtime),
                })

    # BFS order: sort by (depth, path) so parent_serial is non-decreasing
    # in the leaf, which lsbom requires for correct traversal.
    entries.sort(key=lambda e: (e['path'].count('/'), e['path']))
    return entries


# ======================================================================
# XAR container format
# ======================================================================

# checksum algorithm ID for SHA-1
_XAR_CKSUM_SHA1 = 1
_XAR_SHA1_SIZE  = 20


# Files stored with zlib deflate (XAR calls this "application/x-gzip").
# Payload is already gzip-compressed cpio and is stored verbatim.
_XAR_ZLIB_NAMES = frozenset(['PackageInfo', 'Bom'])


def _build_xar(files):
    """Build an XAR archive matching the Apple flat-package convention.

    files  - list of (name_str, content_bytes) tuples

    Encoding per file:
      PackageInfo, Bom → application/x-gzip (XAR zlib-deflates them)
      Payload          → application/octet-stream (gzip-cpio stored verbatim)

    Despite the MIME type name, XAR's "application/x-gzip" encoding is
    zlib.compress() / zlib.decompress(), NOT actual gzip.  Only the Payload
    uses real gzip (produced by _build_payload) and is stored raw.
    Returns bytes.
    """
    heap_items = []
    file_meta  = []
    offset     = _XAR_SHA1_SIZE   # files start after checksum

    for i, (name, content) in enumerate(files):
        if name in _XAR_ZLIB_NAMES:
            stored   = zlib.compress(content)
            encoding = 'application/x-gzip'
            sha1_ext = hashlib.sha1(content).hexdigest()
            sha1_arc = hashlib.sha1(stored).hexdigest()
        else:
            stored   = content
            encoding = 'application/octet-stream'
            sha1_ext = hashlib.sha1(content).hexdigest()
            sha1_arc = sha1_ext

        file_meta.append({
            'id':       i + 1,
            'name':     name,
            'offset':   offset,
            'clen':     len(stored),
            'ulen':     len(content),
            'sha1_ext': sha1_ext,
            'sha1_arc': sha1_arc,
            'encoding': encoding,
        })
        heap_items.append(stored)
        offset += len(stored)

    # TOC XML
    ct = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    toc_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<xar>',
        '  <toc>',
        '    <checksum style="sha1">',
        '      <size>%d</size>' % _XAR_SHA1_SIZE,
        '      <offset>0</offset>',
        '    </checksum>',
        '    <creation-time>%s</creation-time>' % ct,
    ]
    for fm in file_meta:
        toc_lines += [
            '    <file id="%d">'  % fm['id'],
            '      <name>%s</name>' % fm['name'],
            '      <type>file</type>',
            '      <data>',
            '        <length>%d</length>'  % fm['clen'],
            '        <offset>%d</offset>'  % fm['offset'],
            '        <size>%d</size>'      % fm['ulen'],
            '        <extracted-checksum style="sha1">%s</extracted-checksum>' % fm['sha1_ext'],
            '        <archived-checksum style="sha1">%s</archived-checksum>'   % fm['sha1_arc'],
            '        <encoding style="%s"/>' % fm['encoding'],
            '      </data>',
            '    </file>',
        ]
    toc_lines += ['  </toc>', '</xar>', '']
    toc_xml        = '\n'.join(toc_lines).encode('utf-8')
    toc_compressed = zlib.compress(toc_xml)
    toc_checksum   = hashlib.sha1(toc_compressed).digest()

    header = struct.pack('>IHHQQI',
        0x78617221, 28, 1,
        len(toc_compressed), len(toc_xml),
        _XAR_CKSUM_SHA1)

    heap = toc_checksum
    for item in heap_items:
        heap += item

    return header + toc_compressed + heap


# ======================================================================
# PackageInfo XML
# ======================================================================

def _build_package_info(bundle_id, version, install_location,
                         n_files, install_kb, auth='root'):
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<pkg-info format-version="2"',
        '    identifier="%s"' % bundle_id,
        '    version="%s"'    % version,
        '    install-location="%s"' % install_location,
        '    auth="%s">' % auth,
        '  <payload numberOfFiles="%d" installKBytes="%d"/>' % (n_files, install_kb),
        '  <scripts/>',
        '</pkg-info>',
        '',
    ]
    return '\n'.join(lines)


# ======================================================================
# ApplePackage module
# ======================================================================

class ApplePackage(Packager):
    """Purpose: Create a macOS .pkg installer package (pure Python, no
                external tools required).
       Implements: Packager
       Type: Module   Library: csmake-packaging
       Package Name Format: apple
       Phases:
           package - Stage files and build the .pkg
           clean, package_clean - Delete the result directory
       Options:
           package-version  - version suffix for the package file name
           maps             - installmap section(s) defining the payload
           result           - directory for output
           bundle-id        - (OPTIONAL) Reverse-DNS bundle identifier.
                              Default: com.<package-name>
           install-location - (OPTIONAL) Absolute install prefix on the
                              target system.  Files in the installmap are
                              placed relative to this prefix.
                              Default: /usr/local
           auth             - (OPTIONAL) 'root' (default) or 'none'.
                              'root' requires administrator authentication
                              during installation.
       Format notes:
           A .pkg is an XAR (XML ARchive) containing three members:
           - PackageInfo: XML metadata (bundle-id, version, install path)
           - Bom: binary Bill of Materials (file tree with uid/gid/mode/size)
           - Payload: gzip-compressed cpio newc archive of the files
           All three are written entirely in Python without external tools.
       Install Map Definitions:  See Packager module
       See Also:
           csmake --list-type Packager
    """

    REQUIRED_OPTIONS = ['maps', 'result', 'package-version']
    PACKAGER_NAME_FORMAT = 'apple'

    METAMAP_METHODS = {
        'Package'      : Packager.PackageNameMapper,
        '**python-lib' : Packager.AppendingClassifierMapper,
        'License'      : Packager.ClassifierMapper,
    }

    METAMAP = {
        'Package': 'name',
    }

    CLASSIFIER_MAPS = {
        '**python-lib' : Packager.CLASSIFIER_MAPS['**python-lib'],
        'License'      : Packager.CLASSIFIER_MAPS['License'],
    }

    # ------------------------------------------------------------------ #
    # Path mapping                                                         #
    # ------------------------------------------------------------------ #

    def _map_path_python_lib(self, value, pathmaps, pathkeymaps):
        # Files are laid out relative to install-location (default /usr/local).
        # Python packages therefore live under lib/python3/dist-packages
        # within that prefix.
        # Override via default_python-lib option if needed.
        if 'default_python-lib' in self.options:
            path = self.options['default_python-lib'].replace(
                '{', '%(').replace('}', ')s')
            pathmaps[value] = [path]
        else:
            pathmaps[value] = ['%(root)s/lib/python3/dist-packages']
        pathkeymaps['python-lib'] = pathmaps[value]

    def _map_path_python_script(self, value, pathmaps, pathkeymaps):
        # Scripts go in bin/ relative to install-location.
        if 'default_python-script' in self.options:
            path = self.options['default_python-script'].replace(
                '{', '%(').replace('}', ')s')
            pathmaps[value] = [path]
        else:
            pathmaps[value] = ['%(root)s/bin']
        pathkeymaps['python-script'] = pathmaps[value]

    # ------------------------------------------------------------------ #
    # Packager overrides                                                   #
    # ------------------------------------------------------------------ #

    def _calculateFileNameAndVersioning(self):
        Packager._calculateFileNameAndVersioning(self)
        self.archiveFileName = '%s.pkg' % self.fullPackageName
        self.fullPathToArchive = os.path.join(self.resultdir, self.archiveFileName)
        self.stagingDir = os.path.join(
            self.resultdir, '%s-staging' % self.fullPackageName)

    def _map_path_root(self, value, pathmaps, pathkeymaps):
        pathmaps[value] = [self.stagingDir]
        self.archiveRoot = self.stagingDir
        pathkeymaps['root'] = [self.stagingDir]

    def _setupArchive(self):
        self._ensureDirectoryExists(self.fullPathToArchive)
        if os.path.exists(self.stagingDir):
            shutil.rmtree(self.stagingDir)
        os.makedirs(self.stagingDir)
        # Tracks intended uid/gid/mode per staged path (installmap values)
        self._file_records = {}
        self.archive = None  # XAR written in _finishPackage

    def _placeFileInArchive(self, mapping, sourcePath, archivePath, aspects):
        if aspects is not None and not self._doArchiveFileAspects(
                mapping, sourcePath, archivePath, aspects):
            return
        uid  = mapping['owner'][1]
        gid  = mapping['group'][1]
        perm = self._modeInt(mapping['permissions'])

        if os.path.isdir(sourcePath):
            dir_mode = self._getDirectoryMode(perm)
            if not os.path.exists(archivePath):
                os.makedirs(archivePath)
            os.chmod(archivePath, dir_mode)
            self._file_records[archivePath] = {
                'type': 'dir',
                'mode': stat.S_IFDIR | dir_mode,
                'uid':  uid, 'gid': gid,
            }
            for child in sorted(os.listdir(sourcePath)):
                self._placeFileInArchive(
                    mapping,
                    os.path.join(sourcePath, child),
                    os.path.join(archivePath, child),
                    None)
        else:
            self._filePlacingInPackage('data', sourcePath, archivePath)
            destdir = os.path.dirname(archivePath)
            if destdir and not os.path.isdir(destdir):
                os.makedirs(destdir)
                self._file_records[destdir] = {
                    'type': 'dir',
                    'mode': stat.S_IFDIR | self._getDirectoryMode(perm),
                    'uid':  uid, 'gid': gid,
                }
            shutil.copy2(sourcePath, archivePath)
            os.chmod(archivePath, perm)
            self._file_records[archivePath] = {
                'type': 'file',
                'mode': stat.S_IFREG | perm,
                'uid':  uid, 'gid': gid,
            }

    def _finishPackage(self):
        install_location = self.options.get('install-location', '/usr/local')
        bundle_id        = self.options.get(
            'bundle-id', 'com.%s' % self.packageName)
        auth             = self.options.get('auth', 'root')

        # ---- collect file metadata ----
        bom_entries = _collect_bom_entries(self.stagingDir, self._file_records)

        n_files     = len(bom_entries)
        install_kb  = max(1, sum(
            e['size'] for e in bom_entries) // 1024)

        # ---- build package components ----
        payload_bytes = _build_payload(self.stagingDir, self._file_records)
        bom_bytes     = _build_bom(bom_entries)
        pkg_info      = _build_package_info(
            bundle_id, self.version, install_location,
            n_files, install_kb, auth)

        # ---- assemble XAR ----
        xar_bytes = _build_xar([
            ('PackageInfo', pkg_info.encode('utf-8')),
            ('Bom',         bom_bytes),
            ('Payload',     payload_bytes),
        ])

        self._ensureDirectoryExists(self.fullPathToArchive)
        with open(self.fullPathToArchive, 'wb') as fh:
            fh.write(xar_bytes)

        self.log.info("Package written: %s", self.fullPathToArchive)
        return True
