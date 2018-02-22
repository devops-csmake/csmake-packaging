# <copyright>
# (c) Copyright 2018 Cardinal Peak Technologies, LLC
# (c) Copyright 2017 Hewlett Packard Enterprise Development LP
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
import os
import os.path
import email.Utils  #For RFC 2822 timestamp
import zlib
import sys
import subprocess
import StringIO
import tarfile
import hashlib
import struct
import time
import collections
import copy
import re

class RpmPackage(Packager):
    """Purpose: To create a .rpm package that can be consumed by rpm.
       Implements: Packager
       Type: Module   Library: csmake-packaging
       Package Name Format: rpm
       Phases:
           package - Will build a RPM package
           clean, package_clean - will delete the package
       Options:
           Common keywords:
               package-version - the version for the package
               maps - points to installmap based sections that
                      define how files should be mapped into the package
               result - directory to put the results
                        The package will be called
                             <name>-<version>-<package-version>.<arch>.rpm
               arch - (OPTIONAL) Specify the architecture
                         x86_64, i386, i586, i686 probably, or (default) noarch
               distro - (OPTIONAL) The distro the package is targeted for:
                         RHEL 5, Fedora Project, OpenSUSE
                         Default: value is not included in the tags.
               langs - (OPTIONAL) Languages supported (comma delimited)
                         NOTE: add <key>(<lang>) to the metadata
                               see "csmake --list-type metadata" for more info
                         Default: 'C' (the default region)
                         NOTE: Do *not* include 'C' in the listing
                               RPM expects C and all default metadata
                               will be mapped to 'C'
                         NOTE: In order for the metadata to be mapped,
                               The default ('C') definition must also exist.
                         NOTE: If a translation isn't provided for an entry
                               for a given language, the default ('C') will be
                               used.
               script-interpreter - (OPTIONAL) define the script interpreter
                         to specify for running pre/post scripts
                         Default: /bin/sh
               is-source - (OPTIONAL) Set to true to create a source RPM
                         Default: False
               signers - (OPTIONAL) Set to a "Signature" based submodule
                                   to get signatures
                                    This may be a comma/newline delimited
                                    list of signers

       Notes:
           RPMs use a "file flag" definition to help determine how the
           package manager will install the files - which does not correspond
           to any common mapping structure in the Packager system.
           This may be defined in the installmap section as "rpmflags:"
           corresponding to the various sections that can be used in a spec file
           i.e., config, doc, donotuse, missingok, noreplace, ghost, license
                 readme (and spec for the specfile in a source repo...)
           Technically, these flags can be used together:
               rpmflags: config, missingok
           Add "url" to the metadata to map to the package url

       Joinpoints introduced:  See Packager module

       Flowcontrol Advice introduced:  See Packager module

       Install Map Definitions:  See Packager module
           :: rpmflags ::
               The flags are a list of sections a file may be listed under
               in a spec file: config, doc, donotuse, missingok, noreplace
                               ghost, license, readme (and also 'spec')
               Flags may be combined via commas
               Aspects may be used to introduce the desired rpmflags into
               the mapping instead of defining them directly in the installmap
               It is also legitimate to specify:
                   rpmflags:
               with no flags, indicating that it's a boring 'ol regular file
               It is also assumed this is the default if it is left unspecified.
    """

    #Best documentation found thusfar for RPM structure:
    #http://rpm5.org/docs/rpm-guide.html#ch-package-structure
    #http://rpm.org/max-rpm-snapshot/s1-rpm-file-format-rpm-file-format.html
    #https://github.com/rpm-software-management/rpm/blob/master/lib/header.c
    #https://github.com/rpm-software-management/rpm/blob/master/lib/rpmtag.h

    REQUIRED_OPTIONS = ['package-version', 'maps', 'result']
    PACKAGER_NAME_FORMAT = 'rpm'

    class I18NMetadataMapper:
        @staticmethod
        def mapdict(packager):
            return packager.productMetadata

        @staticmethod
        def mapmethod(packager):
            return packager._mapMetadatai18n

    def _mapMetadatai18n(self, key, dictionary):
        metamap = self.__class__.METAMAP
        productKeys = dictionary.keys()
        result = []
        if key in metamap and metamap[key] in dictionary:
            result.append(dictionary[metamap[key]])
        else:
            return None

        if len(self.languages) > 1:
            for lang in self.languages[1:]:
                langkey = "%s(%s)" % (metamap[key], lang)
                if langkey in dictionary:
                    result.append(dictionary[langkey])
                else:
                    result.append(dictionary[metamap[key]])
        return result

    class RpmRecord:
        def __init__(self, tag, datatype, data):
            #This is set up for a default tag of a single entry
            #Overload for arrays
            self.tag = tag
            self.datatype = datatype
            self.data = data
            self.entries = 1
            self.alignment = 1
            self.offsetdata = ""

        def writeHeader(self, package, offset, hashfunctions):
            if self.entries == 0:
                return
            buf = struct.pack(
                "!4I",
                self.tag,
                self.datatype,
                offset,
                self.entries )
            package.write(buf)
            for fn in hashfunctions:
                fn.update(buf)

        def writeStore(self, package, hashfunctions):
            #Default data is just a blob - specific records must override this
            #...better yet would be to override __init__ to swab the data there
            if self.entries == 0:
                return
            package.write(self.offsetdata)
            package.write(self.data)
            for fn in hashfunctions:
                fn.update(self.offsetdata)
                fn.update(self.data)

        def calculateOffset(self, offset):
            bytesused = offset % self.alignment
            if bytesused == 0:
                return 0
            return self.alignment - bytesused

        def alignOffset(self, offset):
            pad = self.calculateOffset(offset)
            self.offsetdata = "\x00" * pad
            return offset + pad

        def _initWithNumericData(self, tag, datatype, data, packtype, limit):
            #Assume the value represented is signed to start with
            # ... thus packtype will be lowercase.
            self.entries = 1
            self.alignment = 1
            self.offsetdata = ""
            self.datatype = datatype
            self.tag = tag
            if isinstance(data, collections.Iterable):
                #This is an array
                self.entries = len(data)
                for item in data:
                    if item > limit:
                        packtype = packtype.upper()
                        break
            else:
                if data > limit:
                    packtype = packtype.upper()
                data = [data]
            self.data = struct.pack("!%d%s" % (self.entries, packtype), *data)

        def dataSize(self):
            return len(self.data)

    #Type 0: null
    class RpmRecordNull(RpmRecord):
        def __init__(self, tag):
            RpmPackage.RpmRecord.__init__(self, tag, 0, "")

    #Type 1: char
    class RpmRecordChar(RpmRecord):
        def __init__(self, tag, data):
            if type(data) != str or type(data) != bytearray:
                raise ValueError("Character data must be a str or bytearray")
            RpmPackage.RpmRecord.__init__(self, tag, 1, str(data))
            self.entries = len(data)

    #Type 2: int8
    class RpmRecordInt8(RpmRecord):
        def __init__(self, tag, data):
            self._initWithNumericData(tag, 2, data, 'b', 127)

    #Type 3: int16
    class RpmRecordInt16(RpmRecord):
        def __init__(self, tag, data):
            self._initWithNumericData(tag, 3, data, 'h', 32767)
            self.alignment = 2

    #Type 4: int32
    class RpmRecordInt32(RpmRecord):
        def __init__(self, tag, data):
            self._initWithNumericData(tag, 4, data, 'l', 2147483647)
            self.alignment = 4

    #Type 5: int64
    class RpmRecordInt64(RpmRecord):
        def __init__(self, tag, data):
            self._initWithNumericData(tag, 5, data, 'q', 9223372036854775807)
            self.alignment = 8

    #Type 6: string
    class RpmRecordString(RpmRecord):
        def __init__(self, tag, data):
            size = len(data)
            #RECHECK: it looks like strings and string arrays might be
            # padded to align on a word boundary.  This would make
            # it easier to load structures?
            # The null terminator should be aligned on a 2-byte boundary
            #if (size % 2) == 0:
            #    size += 1
            data = struct.pack("%ds" % (size+1), data)
            RpmPackage.RpmRecord.__init__(self, tag, 6, data)

    #Type 7: binary blob
    class RpmRecordBinary(RpmRecord):
        def __init__(self, tag, data):
            size = len(data)
            RpmPackage.RpmRecord.__init__(self, tag, 7, data)
            self.entries = size

    #Type 8: string array
    class RpmRecordStringArray(RpmRecord):
        def __init__(self, tag, data):
            if type(data) == str:
                raise ValueError("StringArray must be a list or tuple")
            size = len(data)
            pack = ["%ds" % (len(x) + 1) for x in data]
            data = struct.pack(' '.join(pack), *data)
            RpmPackage.RpmRecord.__init__(self, tag, 8, data)
            self.entries = size

    #Type 9: string array for internationalization/localization
    class RpmRecordI18NStringArray(RpmRecordStringArray):
        def __init__(self, tag, data):
            RpmPackage.RpmRecordStringArray.__init__(self, tag, data)
            self.datatype = 9

    class RpmRecordSection(RpmRecordBinary):
        def __init__(self, section, manager):
            RpmPackage.RpmRecordBinary.__init__(self, section, "\x00" * 16)
            self.manager = manager

        def update(self, buf):
            #This will get called because writeHeader appends self to the
            #list of hash functions.  We don't know the final total record
            #count until we're actually writing out the data...
            #This puts the negative of the size of the header headings in with
            #the original section tag.  I don't see where it specifies this
            #in the docs, but it's in the actual RPMs...
            self.data = buf[:8] + struct.pack('!l', -(self.manager.totalRecords() << 4)) + buf[12:]

        def writeHeader(self, package, offset, hashfunctions):
            hashes = list(hashfunctions)
            hashes.append(self)
            RpmPackage.RpmRecord.writeHeader(self, package, offset, hashes)

    class RpmRecordManager:
        def __init__(self, section):
            self.records = {}
            self.magic = "\x8e\xad\xe8"
            self.version = "\x01"
            self.sectionHeaderRecord = RpmPackage.RpmRecordSection(
                section, self)

        def addRecord(self, record):
            self.records[record.tag] = record

        def totalRecords(self):
            return len(self.records) + 1

        def addSectionHeaderRecord(self, record):
            self.sectionHeaderRecord = record

        def write(self, package, hashfunctions=[]):
            #First write the header
            totalStore = 0
            indices = self.records.keys()
            indices.sort()
            for index in indices:
                value = self.records[index]
                totalStore += value.calculateOffset(totalStore)
                totalStore += value.dataSize()
            buf = struct.pack(
                "!3s c 3I",
                self.magic,
                self.version,
                0,
                len(self.records) + 1,
                totalStore + 16) #Size of the output for the section tag is 16
            package.write(buf)
            for fn in hashfunctions:
                fn.update(buf)
            offset = 0
            self.sectionHeaderRecord.writeHeader(package, totalStore, hashfunctions)
            for index in indices:
                offset = self.records[index].alignOffset(offset)
                self.records[index].writeHeader(package, offset, hashfunctions)
                offset += self.records[index].dataSize()
            for index in indices:
                self.records[index].writeStore(package, hashfunctions)
            self.sectionHeaderRecord.writeStore(package, hashfunctions)

    METAMAP_METHODS = {
        (1000, RpmRecordString) : Packager.PackageNameMapper,
        (1015, RpmRecordString) : Packager.MetadataMapper,
        (1004, RpmRecordI18NStringArray) : I18NMetadataMapper,
        (1011, RpmRecordString) : Packager.MetadataMapper,
        (1016, RpmRecordI18NStringArray) : Packager.ClassifierMapper,
        '**python-lib' : Packager.AppendingClassifierMapper,
        (1014, RpmRecordString) : Packager.ClassifierMapper,
        (1020, RpmRecordString) : Packager.MetadataMapper,
        (1021, RpmRecordString) : Packager.ClassifierMapper
    }

    METAMAP = {
        (1000, RpmRecordString) : 'name',              #RPMTAG_NAME
        (1015, RpmRecordString) : 'packager',          #RPMTAG_PACKAGER
        (1004, RpmRecordI18NStringArray) : 'description',       #RPMTAG_SUMMARY
        (1011, RpmRecordString) : 'manufacturer',      #RPMTAG_VENDOR
        (1020, RpmRecordString) : 'url'                #RPMTAG_URL
    }

    #TODO: Probably would like it to be more elaborate
    #RPM Groups
    #https://fedoraproject.org/wiki/RPMGroups
    #https://en.opensuse.org/openSUSE:Package_group_guidelines
    #RPM Licenses
    #https://fedoraproject.org/wiki/Licensing:Main?rd=Licensing
    #In the RPM analyzed it appears that they use the short names
    #as well as the same 'or' and 'and' syntax used in Debian.
    #  The short names may differ from those provded in the main
    #Packaging map.
    # resulting tuple is (priority, seciton).
    # Lower priority (higher number) is less likely to be picked
    # (or less specific if you prefer)
    CLASSIFIER_MAPS = {
        (1016, RpmRecordI18NStringArray) : {  #RPMTAG_GROUP (i18n array)
            '' :
                        (sys.maxint, 'Applications'),
            'Intended Audience :: Developers' :
                        (9, 'Development'),
            'Topic :: Software Development :: Libraries' :
                        (5, 'Development/Libraries'),
            'Topic :: Software Development :: Libraries :: Python Modules' :
                        (1, 'Development/Libraries/Python'),
            'Programming Language :: Java' :
                        (1, 'Development/Libraries/Java')
        },
        '**python-lib' : Packager.CLASSIFIER_MAPS['**python-lib'],
        (1014, RpmRecordString) : Packager.CLASSIFIER_MAPS['License'],  #RPMTAG_LICENSE
        (1021, RpmRecordString) : { #RPMTAG_OS
            '' : (sys.maxint, 'linux'),
            'Operating System :: POSIX :: Linux' :
                 (1, 'linux')
        }
    }

    def _translateClassifier(self, key, defaultText, lang):
        #TODO: Provide a way to give translations for classifier mappings
        #   possibly an aspect or section that provides:
        #        key[0](<lang>) = method(<text>)
        return defaultText

    def _mapClassifiers(self, key, dictionary):
        result = Packager._mapClassifiers(self, key, dictionary)
        if key[1] is RpmPackage.RpmRecordI18NStringArray:
            #TODO: Provide a way to give translations for classifier mappings
            # - There is probably a standard translation for things like
            #   the RPMTAG_GROUP...
            result = [ result ]
            for lang in self.languages[1:]:
                result.append(self._translateClassifier(key, result[0], lang))
        return result

    #Find more licensing here: https://spdx.org/licenses/
    # And for RPM especially here: https://fedoraproject.org/wiki/Licensing:Main?rd=Licensing
    def _map_path_python_lib(self, value, pathmaps, pathkeymaps):
        Packager._map_path_python_lib(self, value, pathmaps, pathkeymaps)
        pathmaps[value] = \
            [ os.path.join(m, 'site-packages') for m in pathmaps[value] ]
        pathkeymaps['python-lib'] = pathmaps[value]

    def _map_path_root(self, value, pathmaps, pathkeymaps):
        pathmaps[value] = ['.']
        self.archiveRoot = '.'
        pathkeymaps['root'] = [self.archiveRoot]

    def _writeMaintainerScript(self, tag, control):
        text = ''
        if 'text' in control:
            text = '\n'.join(control['text'])
        info = RpmPackage.RpmRecordString(tag, text)
        self.headers.addRecord(info)

    #1025 == RPMTAG_PREUN
    def _control_prerm(self, control):
        self._writeMaintainerScript(1025, control)
    def _control_preun(self, control):
        self._control_prerm(control)

    #1026 == RPMTAG_POSTUN
    def _control_postrm(self, control):
        self._writeMaintainerScript(1026, control)
    def _control_postun(self, control):
        self._control_postrm(control)

    #1023 == RPMTAG_PREIN
    def _control_preinst(self, control):
        self._writeMaintainerScript(1023, control)
    def _control_prein(self, control):
        self._control_preinst(control)

    #1024 == RPMTAG_POSTIN
    def _control_postinst(self, control):
        self._writeMaintainerScript(1024, control)
    def _control_postin(self, control):
        self._control_postinst(control)

    #1079 == RPMTAG_VERIFYSCRIPT
    def _control_verify(self, control):
        self._writeMaintainerScript(1079, control)

    def _control_changelog(self, control):
        if 'text' in control:
            records = control['text']
            #1082 == RPMTAG_CHANGELOGTEXT
            info = RpmPackage.RpmRecordStringArray(1082, control['text'])
            self.headers.addRecord(info)
            currentTime = int(time.time())

            if 'times' in control:
                #1080 == RPMTAG_CHANGELOGTIME
                info = RpmPackage.RpmRecordInt32(1080, control['times'])
            else:
                info = RpmPackage.RpmRecordInt32(1080, [currentTime] * records)
            self.headers.addRecord(info)

            if 'names' in control:
                #1081 == RPMTAG_CHANGELOGNAME
                info = RpmPackage.RpmRecordStringArray(1081, control['names'])
            else:
                info = RpmPackage.RpmRecordStringArray(1081, ['No author'] * records)
            self.headers.addRecord(info)


    #def _writeCopyright(self, copyrightFile, copyright, path):
    #    #TODO: How are copyrights represented in RPM?
    #    #XXX: This is the debian implementation
    #    self.log.devdebug("copyright to write for (%s) is : %s", path, str(copyright))
    #    if path is not None:
    #        copyrightFile.write(
    #            "Files: %s\n" % path )
    #    copyrightFile.write(
    #        "Copyright: %s %s\n" % (
    #            copyright['years'],
    #            copyright['holder'] ) )
    #    license = copyright['license'].strip()
    #    copyrightFile.write(
    #        "License: %s\n" % license)
    #    if license in DebianPackage.LICENSE_TEXTS:
    #        copyrightFile.write(DebianPackage.LICENSE_TEXTS[license])
    #        copyrightFile.write('\n')
    #    if 'disclaimer' in copyright:
    #        copyrightFile.write(
    #            "Disclaimer: %s\n" % copyright['disclaimer'] )
    #    copyrightFile.write('\n')

    #def _control_copyright(self, control):
    #    #XXX: This is the debian implementation
    #    copyrightFile = StringIO.StringIO()
    #    copyrightKeys = control['keys'].keys()
    #    initialKeyOrder = ['Format-Specification', 'Name', 'Maintainer']
    #    for key in initialKeyOrder:
    #        copyrightFile.write("%s: %s\n" % (
    #            key,
    #            control['keys'][key] ) )
    #        copyrightKeys.remove(key)
    #    for key in copyrightKeys:
    #        copyrightFile.write("%s: %s\n" % (
    #            key,
    #            control['keys'][key] ) )
    #    copyrightFile.write('\n')
    #    if 'default' in control:
    #        if type(control['default']) != list:
    #            defaults = [control['default']]
    #        else:
    #            defaults = control['default']
    #        for default in defaults:
    #            self._writeCopyright(
    #                copyrightFile,
    #                default,
    #                None )

    #        for default in defaults:
    #            self._writeCopyright(
    #                copyrightFile,
    #                default,
    #                '*' )

    #    copyrightPath = os.path.join(
    #        'usr/share/doc',
    #        self.packageMetadata['Package'],
    #        'copyright' )

    #    for key, value in control['files'].iteritems():
    #        self._writeCopyright(
    #            copyrightFile,
    #            value,
    #            key )

    #    info = self._createArchiveFileInfo(copyrightPath)
    #    info.uid = 0
    #    info.uname = 'root'
    #    info.gid = 0
    #    info.gname = 'root'
    #    info.mode = 0644
    #    self._addFileObjToArchive(copyrightFile, info)

    #def _addInfoToControl(self, info):
    #    self._filePlacingInPackage('control',None,info.name,None)
    #    self.controlfile.addfile(info)

    #rpmsense flags from: https://github.com/rpm-software-management/rpm/blob/master/lib/rpmds.h
    RPMSENSE_INTERP = 1 << 8
    RPMSENSE_LESS = 1 << 1
    RPMSENSE_GREATER = 1 << 2
    RPMSENSE_EQUAL = 1 << 3
    RPMSENSE_PREREQ = 1 << 6 #Prerequisite, like pre-depends
    RPMSENSE_SCRIPT_PRE = 1 << 10
    RPMSENSE_SCRIPT_POST = 1 << 11
    RPMSENSE_SCRIPT_PREUN = 1 << 12
    RPMSENSE_SCRIPT_POSTUN = 1 << 13

    RPMSENSE_RPMLIB = 1 << 24
    DEB_OP_TRANSLATION = {
        '<' : RPMSENSE_LESS,
        '=' : RPMSENSE_EQUAL,
        '>' : RPMSENSE_GREATER,
        '~' : RPMSENSE_EQUAL
    }

    def _flattenDebianDependencies(self, ast):
        #TODO: For now, we don't do rich boolean...
        if ast[0] == 'id':
            return [(ast[1], ast[2])]
        if ast[0] == 'or':
            #Just take the first in the list...yuck
            return self._flattenDebianDependencies(ast[1])
        if ast[0] == 'and':
            result = []
            for item in ast[1:]:
                result.extend(
                    self._flattenDebianDependencies(item))
            return result

    def _parseDebianDependencies(self, data):
        ast = self._translatePackageNames(data)
        if ast is None:
            return ([], [], [])
        items = self._flattenDebianDependencies(ast)
        self.log.debug("flattened deps: %s", items)
        names = []
        flags = []
        versions = []
        for item, version in items:
            version = version.strip()
            resultFlags = 0
            if len(version) == 0:
                versionString = ""
            else:
                if version[0] not in RpmPackage.DEB_OP_TRANSLATION:
                    self.log.warning("'%s' is not a value version for '%s'", version, item)
                    versionString = ""
                else:
                    resultFlags |= RpmPackage.DEB_OP_TRANSLATION[version[0]]
                    if version[1] in RpmPackage.DEB_OP_TRANSLATION:
                        resultFlags |= RpmPackage.DEB_OP_TRANSLATION[version[1]]
                        versionString = version[2:].strip()
                    else:
                        versionString = version[1:].strip()
            names.append(item)
            flags.append(resultFlags)
            versions.append(versionString)
        return (names, flags, versions)

    def _doMetadataMappings(self):
        if 'arch' in self.options:
            self.arch = self.options['arch']
        else:
            self.arch = 'noarch'
        self.languages = ['C']
        if 'langs' in self.options:
            self.languages.extend(
                self._parseCommaAndNewlineList(
                    self.options['langs'] ) )

        #Create a HEADER_IMMUTABLE section
        self.headers = RpmPackage.RpmRecordManager(63)
        Packager._doMetadataMappings(self) #Calls _calculateFileNameAndVersioning
        #1005 == RPMTAG_DESCRIPTION
        entry = []
        self.packageMetadata[
            (1005, RpmPackage.RpmRecordI18NStringArray)] = entry
        entry.append(self.productMetadata['about'].replace('\n','\x0a'))
        if len(self.languages) > 1:
            for lang in self.languages[1:]:
                localAboutKey = "about(%s)" % lang
                if localAboutKey in self.productMetadata:
                    entry.append(self.productMetadata[localAboutKey])
                else:
                    entry.append(entry[0])

        #1010 == RPMTAG_DISTRIBUTION
        if 'distro' in self.options:
            self.packageMetadata[
                (1010, RpmPackage.RpmRecordString)] = self.options['distro']

        #1000 == RPMTAG_NAME
        self.packageName = self.packageMetadata[(1000, RpmPackage.RpmRecordString)]

        #1002 == RPMTAG_RELEASE
        self.packageMetadata[
            (1002, RpmPackage.RpmRecordString)] = self.packageVersion

        #1007 == RPMTAG_BUILDHOST
        self.packageMetadata[
            (1007, RpmPackage.RpmRecordString)] = 'csmake'

        #1022 == RPMTAG_ARCH
        self.packageMetadata[
            (1022, RpmPackage.RpmRecordString)] = self.arch

        #1131 == RMPTAG_RHNPLATFORM (deprecated)
        self.packageMetadata[
            (1131, RpmPackage.RpmRecordString)] = self.arch

        #1124 == RPMTAG_PAYLOADFORMAT
        self.packageMetadata[
            (1124, RpmPackage.RpmRecordString)] = 'cpio'

        #1125 == RPMTAG_PAYLOADCOMPRESSOR
        self.packageMetadata[
            (1125, RpmPackage.RpmRecordString)] = 'gzip'

        #1126 == RPMTAG_PAYLOADFLAGS (compression level used)
        self.packageMetadata[
            (1126, RpmPackage.RpmRecordString)] = '9'

        #1001 == RPMTAG_VERSION
        self.packageMetadata[
            (1001, RpmPackage.RpmRecordString)] = self.productMetadata['version']['primary']

        #1064 == RPMTAG_RPMVERSION
        self.packageMetadata[
            (1064, RpmPackage.RpmRecordString)] = "4.1.0"

        #Process all metadata that we can now
        for tag, value in self.packageMetadata.iteritems():
            if type(tag) is tuple:
                actualtag, factory = tag
                self.log.devdebug("%s: %d: %s", str(factory), actualtag, str(value))
                self.headers.addRecord(factory(actualtag, value))

        #Process the dependencies

        #Process provides, add self
        provides = [self.productMetadata['name']]
        providesFlags = [RpmPackage.RPMSENSE_EQUAL]
        providesVersion = [self.fullVersion]
        if 'provides' in self.productMetadata:
            #TODO: Do any other "provides"
            otherProvides, otherFlags, otherVersions = self._parseDebianDependencies(self.productMetadata['provides'])
            provides.extend(otherProvides)
            providesFlags.extend(otherFlags)
            providesVersion.extend(otherVersion)

        #1047 == RPMTAG_PROVIDENAME
        info = RpmPackage.RpmRecordStringArray(1047, provides)
        self.headers.addRecord(info)

        #1112 == RPMTAG_PROVIDEFLAGS
        info = RpmPackage.RpmRecordInt32(1112, providesFlags)
        self.headers.addRecord(info)

        #1113 == RPMTAG_PROVIDEVERSION
        info = RpmPackage.RpmRecordStringArray(1113, providesVersion)
        self.headers.addRecord(info)

        #Process depends, add rpmlib dependencies
        #TODO: What does RPMSENSE_CONFIG flag and ex: config(vim) do?
        #       btw this has a version associated with it in the vim package...
        #If we have dependencies with booleans, we need to specify:
        #   rpmlib(RichDependencies) v4.12.0-1
        #If we need to support large files we need to specify:
        #   rpmlib(LargeFiles) v4.12.0-1
        #See: https://github.com/rpm-software-management/rpm/blob/master/lib/rpmds.c
        #What are TRIGGERNAME, etc.?
        interpreter = '/bin/sh'
        if 'shell-interpreter' in self.options:
            interpreter = self.options['shell-interpreter']
        #NOTE: We may want to have the rpmlib stuff at the end
        #      it may not matter
        #TODO: It's unclear how to actually represent richboolean
        #      in the headers...
        requires = [
            interpreter,
            'rpmlib(VersionedDependencies)',
            'rpmlib(PayloadFilesHavePrefix)',
            'rpmlib(CompressedFileNames)' ]
        requiresFlags = [
            RpmPackage.RPMSENSE_INTERP, #The vim pkg doesn't use this?
            RpmPackage.RPMSENSE_RPMLIB | RpmPackage.RPMSENSE_LESS | RpmPackage.RPMSENSE_EQUAL,
            RpmPackage.RPMSENSE_RPMLIB | RpmPackage.RPMSENSE_LESS | RpmPackage.RPMSENSE_EQUAL,
            RpmPackage.RPMSENSE_RPMLIB | RpmPackage.RPMSENSE_LESS | RpmPackage.RPMSENSE_EQUAL ]
        requiresVersion = [
            '',
            '3.0.3-1',
            '4.0-1',
            '3.0.4-1' ]
        if 'depends' in self.productMetadata:
            otherRequires, otherFlags, otherVersion = self._parseDebianDependencies(self.productMetadata['depends'])
            requires.extend(otherRequires)
            requiresFlags.extend(otherFlags)
            requiresVersion.extend(otherVersion)

        if 'pre-depends' in self.productMetadata:
            # This is treated as RPMSENSE_SCRIPT_PRE | RPMSENSE_SCRIPT_PREUN
            # In the spec parsing
            preRequires, preFlags, preVersion = self._parseDebianDependencies(self.productMetadata['pre-depends'])
            preFlags = [ x | RpmPackage.RPMSENSE_SCRIPT_PRE | RpmPackage.RPMSENSE_SCRIPT_PREUN
                           for x in preFlags ]
            requires.extend(preRequires)
            requiresFlags.extend(preFlags)
            requiresVersion.extend(preVersion)

        #1048 == RPMTAG_REQUIREFLAGS
        info = RpmPackage.RpmRecordInt32(1048, requiresFlags)
        self.headers.addRecord(info)

        #1049 == RPMTAG_REQUIRENAME
        info = RpmPackage.RpmRecordStringArray(1049, requires)
        self.headers.addRecord(info)

        #1050 == RPMTAG_REQUIREVERSION
        info = RpmPackage.RpmRecordStringArray(1050, requiresVersion)
        self.headers.addRecord(info)

        breaks = []
        breaksFlags = []
        breaksVersion = []
        conflicts = []
        conflictsFlags = []
        conflictsVersion = []
        if 'breaks' in self.productMetadata:
            breaks, breaksFlags, breaksVersion = self._parseDebianDependencies(self.productMetadata['breaks'])

        if 'conflicts' in self.productMetadata:
            conflicts, conflictsFlags, conflictsVersion = self._parseDebianDependencies(self.productMetadata['conflicts'])

        if len(breaks) > 0 or len(conflicts) > 0 is not None:
            #1054 == RPMTAG_CONFLICTNAME
            conflicts.extend(breaks)
            info = RpmPackage.RpmRecordStringArray(1054, conflicts)
            self.headers.addRecord(info)

            #1055 == RPMTAG_CONFLICTVERSION
            conflictsVersion.extend(breaksVersion)
            info = RpmPackage.RpmRecordStringArray(1055, conflictsVersion)
            self.headers.addRecord(info)

            #1053 == RPMTAG_CONFLICTFLAGS
            conflictsFlags.extend(breaksFlags)
            info = RpmPackage.RpmRecordInt32(1053, conflictsFlags)
            self.headers.addRecord(info)

        #Format is: (metatag, (NAME id, FLAGS id, VERSION id))
        moreDependencies = [
            #1090 == RPMTAG_OBSOLETENAME
            #1114 == RPMTAG_OBSOLETEFLAGS
            #1115 == RPMTAG_OBSOLETEVERSION
            ('replaces', (1090, 1114, 1115)),

            #5046 == RPMTAG_RECOMMENDNAME
            #5048 == RPMTAG_RECOMMENDFLAGS
            #5047 == RPMTAG_RECOMMENDVERSION
            ('recommends', (5046, 5048, 5047)),

            #5049 == RPMTAG_SUGGESTNAME
            #5051 == RPMTAG_SUGGESTFLAGS
            #5050 == RPMTAG_SUGGESTVERSION
            ('suggests', (5049, 5051, 5050)),

            #5055 == RPMTAG_ENHANCENAME
            #5057 == RPMTAG_ENHANCEFLAGS
            #5056 == RPMTAG_ENHANCEVERSION
            ('enhances', (5055, 5057, 5056))
        ]

        for metatag, ids in moreDependencies:
            if metatag in self.productMetadata:
                names, flags, versions = self._parseDebianDependencies(self.productMetadata[metatag])
                if len(names) == 0:
                    continue
                nameid, flagid, versionid = ids

                info = RpmPackage.RpmRecordStringArray(nameid, names)
                self.headers.addRecord(info)
                info = RpmPackage.RpmRecordInt32(flagid, flags)
                self.headers.addRecord(info)
                info = RpmPackage.RpmRecordStringArray(versionid, versions)
                self.headers.addRecord(info)

        #XXX: Old debian copyright handling
        #copyright = self._packageControl('copyright')
        #copyright['keys'] = {}
        #copyright['keys']['Format-Specification'] = 'http://svn.debian.org/wsvn/dep/web/deps/dep5.mdwn?op=file&rev=135'
        #copyright['keys']['Name'] = self.packageName
        #copyright['keys']['Maintainer'] = self.packageMetadata['Maintainer']

        return self.packageMetadata

    def _calculateFileNameAndVersioning(self):
        #self.version = self.metadata._getDefaultDefinedVersion('+')
        self.format = 'rpm'
        Packager._calculateFileNameAndVersioning(self)
        self.version = self.metadata._getVersionWithFormat(
            [ "%(epoch)s:","%(primary)s" ],
            True,
            ['epoch', 'primary'],
            '+' )
        version = self.version
        self.filenameVersion = self.metadata._getVersionWithFormat(
            ["%(primary)s"],
            True,
            ['epoch', 'primary'],
            '+' )
        self.packageVersion = self.options['package-version']
        self.filenameFullVersion = '%s-%s' % (
            self.filenameVersion,
            self.packageVersion )
        self.fullVersion = '%s-%s' % (
            version,
            self.packageVersion )
        self.fullLeadName = '%s-%s' % (
            self.packageName,
            self.filenameFullVersion )
        self.fullPackageName = '%s.%s.rpm' % (self.fullLeadName, self.arch)
        self.fullPathToPackage = os.path.join(
            self.resultdir,
            self.fullPackageName )
        self.fullPathToArchive = os.path.join(
            self.resultdir,
            self.fullPackageName + ".tempcpio" )

    def _filePlacingInPackage(self, archive, sourcePath, archivePath, contents=None):
        #NOTE: Don't forget to call this from cpiofile...
        Packager._filePlacingInPackage(self, archive, sourcePath, archivePath, contents)

    def _addInfoToArchive(self, info):
        self.executedFileMappings.append((None, None, None, info))

    def _placeDirectoryInArchive(self, mapping, sourcePath, archivePath, aspects, info):
        dirlist = os.listdir(sourcePath)
        for entry in dirlist:
            actualArchivePath = os.path.join(archivePath, entry)
            actualSourcePath = os.path.join(sourcePath, entry)
            #If we want each directory level processed and aspected,
            #  we could remove this if section.
            if os.path.isdir(actualSourcePath):
                self._placeDirectoryInArchive(
                    mapping,
                    actualSourcePath,
                    actualArchivePath,
                    aspects,
                    info )
            elif os.path.islink(actualSourcePath):
                self.log.warning("RPM Ignoring link in directory listing")
            else:
                fileinfo = copy.deepcopy(info)
                filemapping = copy.deepcopy(mapping)
                self._placeFileInArchive(
                    filemapping,
                    actualSourcePath,
                    actualArchivePath,
                    aspects,
                    fileinfo )

    def _placeFileInArchive(self, mapping, sourcePath, archivePath, aspects, info = None):
        if info is None:
            info = tarfile.TarInfo()
        info.name = archivePath
        info.mode = self._modeInt(mapping['permissions'])
        if os.path.isdir(sourcePath): #Be sure to handle source link as real sorce
            info.type = tarfile.DIRTYPE
            info.size = 0
        else:
            info.type = tarfile.REGTYPE
            size = os.path.getsize(sourcePath)
            info.size = size
        info.uid = mapping['owner'][1]
        info.gid = mapping['group'][1]
        info.uname = mapping['owner'][0]
        info.gname = mapping['group'][0]
        info.mtime = 0
        #RPMTAG_FILEFLAGS defs are found here:
        #https://github.com/rpm-software-management/rpm/blob/1b338aa84d4c67fefa957352a028eaca1a45d1f6/lib/rpmfiles.h
        #rpmfileAttrs_e
        #How do we convey this from the installmap? %config, %doc, etc. are the
        #  related spec sections...could we use aspects to label these?
        if not self._doArchiveFileAspects(
            mapping,
            sourcePath,
            archivePath,
            aspects,
            info ):
            return None

        if info.type == tarfile.DIRTYPE:
            return self._placeDirectoryInArchive(mapping, sourcePath, archivePath, aspects, info)

        if 'rpmflags' in mapping:
            flags = mapping['rpmflags'].split(',')
            info.rpmflags = 0
            for flag in flags:
                flag = flag.strip()
                if flag == 'config':
                    info.rpmflags |= 1
                elif flag == 'doc':
                    info.rpmflags |= (1 << 1)
                elif flag == 'donotuse':
                    info.rpmflags |= (1 << 2)
                elif flag == 'missingok':
                    info.rpmflags |= (1 << 3)
                elif flag == 'noreplace':
                    info.rpmflags |= (1 << 4)
                elif flag == 'spec':
                    info.rpmflags |= (1 << 5)
                elif flag == 'ghost':
                    info.rpmflags |= (1 << 6)
                elif flag == 'license':
                    info.rpmflags |= (1 << 7)
                elif flag == 'readme':
                    info.rpmflags |= (1 << 8)
                elif flag == 'pubkey':
                    info.rpmflags |= (1 << 11)
                else:
                    self.log.warning("Flag '%s' has no definition", flag)
        else:
            info.rpmflags = 0

        #We'll actually do this after we write all the headers.
        #Now that the archive cpio is it's own file, we no longer need to wait.
        #  pulling that in would be more in line with the standard packager
        #  behavior.
        self.executedFileMappings.append((mapping, sourcePath, archivePath, info))
        #We may need to actually do _filePlacingInPackage here to capture
        #all the signature info?  That will mean two reads of the file if we
        #have to do that here....alternatively, we may be able to drop any
        #Requisite signature headers in and then go back to fill it in when
        #we write the file....hmmm...
        #self._cilePlacingInPackage(
        #    'data',
        #    sourcePath,
        #    archivePath,
        #    ??contents?? )

    #For rpmtype RPMLEAD_BINARY == 0 and RPMLEAD_SOURCE == 1
    #
    def _makeLead(
        self,
        major,
        minor,
        rpmtype,
        archnum,
        name,
        osnum,
        sig_type ):

        return struct.pack(
            '!4s 2b 2h 66s 2h 16s',
            self.leadmagic,
            major,
            minor,
            rpmtype,
            archnum,
            name,
            osnum,
            sig_type,
            '\x00' * 16)

    class CpioFile:
        def __init__(
            self,
            packager,
            archive,
            compression,
            clearDigests=[],
            compressedDigests=[]):

            self.archive = archive
            self.compression = compression
            self.clearDigests = clearDigests
            self.compressedDigests = compressedDigests
            self.totalUncompressedSize = 0
            self.totalUncompressedFileSize = 0
            self.packager = packager

            #Tracking for header information
            self.fileSizes = []
            self.fileModes = []
            self.fileRDevs = []
            self.fileMTimes = []
            self.fileMD5s = []
            self.fileLinkTos = []
            self.fileFlags = []
            self.fileVerifyFlags = []
            self.fileUsernames = []
            self.fileGroupnames = []
            self.fileDevices = []
            self.fileInodes = []
            self.fileLangs = []
            self.fileBaseNames = []
            self.dirIndex = []
            self.dirNames = []

            #I'm not convinced this matters much at all here, but
            #for proper integrity, we're going to include a incremental counter
            #for marking off the inode in the cpio header
            self.currentInode = 1

            self.CHUNK_SIZE = 10240
            self.magic = "070701"

        def _cpioHexifyHeaderField(self, data):
            result = hex(data)[2:]
            result = '0' * (8 - len(result)) + result
            return result

        def _createCpioHeader(self, info):
            header = []
            header.append(self.magic)

            #Yet to see a good example of RPMTAG_FILELANGS
            self.fileLangs.append('')

            self.fileInodes.append(self.currentInode)
            inode = self._cpioHexifyHeaderField(self.currentInode)
            header.append(inode)
            self.currentInode += 1

            if info.type == tarfile.REGTYPE:
                typeint = 0100 << 9
                self.fileLinkTos.append('')
            elif info.type == tarfile.SYMTYPE:
                typeint = 0120 << 9
                self.fileLinkTos.append(info.linkname)

            self.fileModes.append(typeint | info.mode)
            mode = self._cpioHexifyHeaderField(typeint | info.mode)
            header.append(mode)

            self.fileUsernames.append(info.uname)
            self.fileGroupnames.append(info.gname)
            uid = self._cpioHexifyHeaderField(info.uid)
            header.append(uid)
            gid = self._cpioHexifyHeaderField(info.gid)
            header.append(gid)

            nlink = "00000001"  #We're not going to do hardlinks...
            header.append(nlink)

            currentTime = int(time.time())
            info.mtime = currentTime
            self.fileMTimes.append(currentTime)
            mtime = self._cpioHexifyHeaderField(currentTime)
            header.append(mtime)

            self.fileSizes.append(info.size)
            size = self._cpioHexifyHeaderField(info.size)
            header.append(size)

            self.fileDevices.append(0x2210)
            devmajor = "00000022"
            header.append(devmajor)

            devminor = "00000010"
            header.append(devminor)

            self.fileRDevs.append(0)
            rdevmajor = "00000000"
            header.append(rdevmajor)

            rdevminor = "00000000"
            header.append(rdevminor)

            dirname, basename = os.path.split(info.name)
            rpmdirname = dirname
            if rpmdirname[0] == '.':
                rpmdirname = rpmdirname[1:]
            if rpmdirname[-1] != '/':
                rpmdirname += '/'
            if rpmdirname in self.dirNames:
                dirindex = self.dirNames.index(rpmdirname)
            else:
                dirindex = len(self.dirNames)
                self.dirNames.append(rpmdirname)

            self.dirIndex.append(dirindex)
            self.fileBaseNames.append(basename)

            #Adding a . to the name - RPM's cpio's have this probably to
            #allow users or the pkgman the ability to put the payload whereever
            #it wants to.
            if info.name[0] == '/':
                info.name = '.' + info.name
            #+1 here even though we don't add the \x00 because
            #  we will always pad the header with at least one 0 at the
            #  end (see _padHeader)
            namesize = self._cpioHexifyHeaderField(len(info.name)+1)
            header.append(namesize)

            chksum = "00000000"
            header.append(chksum)

            header.append(info.name)

            self.fileFlags.append(info.rpmflags)

            #TODO: If necessary we can expose this through the installmap
            #      like rpmflags...rpmverify?
            self.fileVerifyFlags.append(0)

            return ''.join(header)

        def _writeToArchive(self, buf):
            self.totalUncompressedSize += len(buf)
            for digest in self.clearDigests:
                digest.update(buf)
            compressed = self.compression.compress(buf)
            for digest in self.compressedDigests:
                digest.update(compressed)
            written = len(compressed)
            self.archive.write(compressed)
            return written

        def _padRecord(self, written):
            pad = 4 - (written % 4)
            if pad != 4:
                return self._writeToArchive('\x00'*pad)
            else:
                return 0

        #NOTE: The header pad includes a 0 termination for the name
        #      This is a bit goofy way to pad the header, however
        #      it works.  A +1 is added to the string for the termination
        def _padHeader(self, written):
            pad = 4 - (written % 4)
            return self._writeToArchive('\x00'*pad)

        def _writeTrailer(self):
            #I probably should use the header creation, however, this
            # is much easier than attempting to bend the will of info
            return self._writeToArchive("%s00000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000b00000000TRAILER!!!\x00\x00\x00\x00" % self.magic)

        def _addLinkToCpio(self, filetuple):
            mapping, sourcePath, archivePath, info = filetuple
            data = info.linkname
            info.size = len(data)
            header = self.createCpioHeader(info)
            written = len(header)
            result = self._writeToArchive(header)
            written += len(data)
            result += self._writeToArchive(data)
            result += self._padRecord(written)
            return result

        def _addFileToCpio(self, filetuple):
            #NOTE: This could reasonably be pulled into the actual
            #      mapping code now since the headers have been made
            #      its own file.  That would mess up the md5 processing,
            #      however, and require that the md5sum be calculated
            #      as part of the move, which means reading the data
            #      back in instead of doing the more efficient dd.
            mapping, sourcePath, archivePath, info = filetuple
            self.packager._filePlacingInPackage('data', sourcePath, archivePath)
            #REVISIT: This could be an issue - sometimes you need to create
            #         an empty directory...
            if info.type == tarfile.DIRTYPE:
                self.log.warning("Directory detected: CpioFile only does files: %s", sourcePath)
                return 0
            elif info.type == tarfile.SYMTYPE:
                return self._addLinkToCpio(filetuple)
            header = self._createCpioHeader(info)
            written = len(header)
            result = self._writeToArchive(header)
            result += self._padHeader(written)
            filemd5 = hashlib.md5()
            self.clearDigests.append(filemd5)
            written = 0
            with open(sourcePath) as source:
                buf = source.read(self.CHUNK_SIZE)
                while len(buf) > 0:
                    self.totalUncompressedFileSize += len(buf)
                    written += len(buf)
                    result += self._writeToArchive(buf)
                    buf = source.read(self.CHUNK_SIZE)
            self.clearDigests.remove(filemd5)
            themd5 = filemd5.hexdigest()
            self.fileMD5s.append(themd5)
            result += self._padRecord(written)
            return result

        def getTotalUncompressedSize(self):
            return self.totalUncompressedSize

        def getTotalUncompressedFileSize(self):
            return self.totalUncompressedFileSize

        def writePayload(self, filetuples):
            #RPM Assumes that the files are sorted so it can
            #Do a binary search to find the files....
            filetuples.sort(key=lambda x: x[3].name)
            result = 0
            for filetuple in filetuples:
                result += self._addFileToCpio(filetuple)
            result += self._writeTrailer()
            compressed = self.compression.flush(zlib.Z_FINISH)
            for digest in self.compressedDigests:
                digest.update(compressed)
            result += len(compressed)
            self.archive.write(compressed)
            return result

        def addFileTagsToManager(self, manager):
            #1028 == RPMTAG_FILESIZES
            info = RpmPackage.RpmRecordInt32(1028, self.fileSizes)
            manager.addRecord(info)

            #1030 == RPMTAG_FILEMODES
            info = RpmPackage.RpmRecordInt16(1030, self.fileModes)
            manager.addRecord(info)

            #1033 == RPMTAG_FILERDEVS
            info = RpmPackage.RpmRecordInt16(1033, self.fileRDevs)
            manager.addRecord(info)

            #1034 == RPMTAG_FILEMTIMES
            info = RpmPackage.RpmRecordInt32(1034, self.fileMTimes)
            manager.addRecord(info)

            #1035 == RPMTAG_FILEMD5S
            info = RpmPackage.RpmRecordStringArray(1035, self.fileMD5s)
            manager.addRecord(info)

            #1036 == RPMTAG_FILELINKTOS
            info = RpmPackage.RpmRecordStringArray(1036, self.fileLinkTos)
            manager.addRecord(info)

            #1037 == RPMTAG_FILEFLAGS
            info = RpmPackage.RpmRecordInt32(1037, self.fileFlags)
            manager.addRecord(info)

            #1039 == RPMTAG_FILEUSERNAME
            info = RpmPackage.RpmRecordStringArray(1039, self.fileUsernames)
            manager.addRecord(info)

            #1040 == RPMTAG_FILEGROUPNAME
            info = RpmPackage.RpmRecordStringArray(1040, self.fileGroupnames)
            manager.addRecord(info)

            #1045 == RPMTAG_FILEVERIFYFLAGS
            info = RpmPackage.RpmRecordInt32(1045, self.fileVerifyFlags)
            manager.addRecord(info)

            #1095 == RPMTAG_FILEDEVICES
            info = RpmPackage.RpmRecordInt32(1095, self.fileDevices)
            manager.addRecord(info)

            #1096 == RPMTAG_FILEINODES
            info = RpmPackage.RpmRecordInt32(1096, self.fileInodes)
            manager.addRecord(info)

            #1097 == RPMTAG_FILELANGS
            info = RpmPackage.RpmRecordStringArray(1097, self.fileLangs)
            manager.addRecord(info)

            #1116 == RPMTAG_DIRINDEXES
            info = RpmPackage.RpmRecordInt32(1116, self.dirIndex)
            manager.addRecord(info)

            #1117 == RPMTAG_BASENAMES
            info = RpmPackage.RpmRecordStringArray(1117, self.fileBaseNames)
            manager.addRecord(info)

            #1118 == RPMTAG_DIRNAMES
            info = RpmPackage.RpmRecordStringArray(1118, self.dirNames)
            manager.addRecord(info)

    def _setupArchive(self):
        self.executedFileMappings = []
        self.totalPayloadSize = 0
        self.headerSHA = hashlib.sha1()
        self.contentMD5 = hashlib.md5()
        self.leadmagic = "\xed\xab\xee\xdb"
        self.package = open(self.fullPathToPackage, 'wb')
        open(self.fullPathToArchive, 'wb').close()
        self.archive = open(self.fullPathToArchive, 'r+b')
        self.fullPathToArchiveHeaders = self.fullPathToArchive + '.headers'
        self.archiveHeaders = open(self.fullPathToArchiveHeaders, 'wb')
        self.languages = ['C']
        self.isSource = False
        self.signers = []
        if 'signers' in self.options:
            ids = self._parseCommaAndNewlineList(self.options['signers'])
            phase = self.engine.getPhase()
            for idname in ids:
                signerResult = self.engine.launchStep(idname, phase)
                if signerResult is None or not signerResult._didPass():
                    raise ValueError("%s step failed" % idname)
                signer = signerResult._getReturnValue(phase)
                self.signers.append(signer)
        if 'is-source' in self.options:
            self.isSource = self.options['is-source'] == 'True'

        #100 == RPMTAG_HEADERI18NTABLE
        info = RpmPackage.RpmRecordStringArray(100, self.languages)
        self.headers.addRecord(info)

    def _setupPackage(self):
        self._ensureDirectoryExists(self.resultdir, True)
        Packager._setupPackage(self)

    def _createSignatureBlock(self):
        #This cannot be called until the contents of the payload are compressed
        #62 == HEADER_SIGNATURES
        sigBlock = RpmPackage.RpmRecordManager(62)
        #1000 == RPMSIGTAG_SIZE - size of the header and compressed payload
        item = RpmPackage.RpmRecordInt32(1000, self.totalPayloadSize)
        sigBlock.addRecord(item)
        #1004 == RPMSIGTAG_MD5 - MD5 of the header and compressed payload
        item = RpmPackage.RpmRecordBinary(1004, self.contentMD5.digest())
        sigBlock.addRecord(item)
        signkeys = []
        for signer in self.signers:
            signtype = signer.signtype()
            if signtype == 'GPG':
                #1005 == RPMSIGTAG_GPG
                if 1005 in signkeys:
                    signer.close()
                    self.log.warning("Multiple GPG Signatures were specified")
                else:
                    item = RpmPackage.RpmRecordBinary(1005, signer.digest())
                    sigBlock.addRecord(item)
        #1007 == RPMSIGTAG_PAYLOADSIZE - uncompressed size of the payload
        item = RpmPackage.RpmRecordInt32(1007, self.totalUncompressedPayload)
        sigBlock.addRecord(item)
        #1010 == RPMSIGTAG_SHA1 - sha1 of the header content
        item = RpmPackage.RpmRecordString(1010, self.headerSHA.digest())
        sigBlock.addRecord(item)
        #RSA/DSA/PGP/GPG Signatures would go here...
        return sigBlock

    def _finishArchive(self):
        self.package.write(
            self._makeLead(
                3, #RPM's major number (post 2.1)
                0, #RPM's minor number (post 2.1)
                1 if self.isSource else 0, #(1 is source, 0 is binary)
                #file(1) reports ff as noarch, 1 as i386/x86_64
                0xff if self.arch == 'noarch' else 1,
                self.fullLeadName,
                1, #1 is linux...
                5 )) #5 == RPMSIGTYPE_HEADERSIG

        #Finish entering the metadata
        # 9 is max compression, 31 means use max bits and write a gzip header.
        compression = zlib.compressobj(9, zlib.DEFLATED, 31)
        cpiofile = self.CpioFile(
            self,
            self.archive,
            compression,
            [],
            [])
        cpiofile.writePayload(self.executedFileMappings)
        cpiofile.addFileTagsToManager(self.headers)

        #1009 == RPMTAG_SIZE - The size of all the regular files in the archive
        item = RpmPackage.RpmRecordInt32(
            1009,
            cpiofile.getTotalUncompressedFileSize())
        self.headers.addRecord(item)

        #1046 == RPMTAG_ARCHIVESIZE
        self.totalUncompressedPayload = cpiofile.getTotalUncompressedSize()
        item = RpmPackage.RpmRecordInt32(
            1046,
            self.totalUncompressedPayload )
        self.headers.addRecord(item)


        headerdigests = [self.headerSHA, self.contentMD5]
        headerdigests.extend(self.signers)
        self.headers.write(self.archiveHeaders, headerdigests)
	self.totalPayloadSize = self.archiveHeaders.tell()
        self.archiveHeaders.close()
        self.archive.seek(0)
        block = self.archive.read(10240)
        while len(block) > 0:
            self.totalPayloadSize += len(block)
            self.contentMD5.update(block)
            for signer in self.signers:
                signer.update(block)
            block = self.archive.read(10240)
        self.archive.close()

        #Ask the size of the archive here
        sigblock = self._createSignatureBlock()
        sigblock.write(self.package)

        #RPM's library seems to expect the rest of the data starting on
        # an 8 byte boundary, there's no sha tracking here because
        # this seems to count toward the signature header.
        endbytes = 8 - (self.package.tell() % 8)
        if endbytes != 8:
            self.package.write( '\x00' * endbytes)
        self.package.close()
        subprocess.check_call(
            ['dd',
             'if=%s' % self.fullPathToArchiveHeaders,
             'of=%s' % self.fullPathToPackage,
             #'bs=1M',
             'oflag=append',
             'conv=notrunc' ],
            stdout = self.log.out(),
            stderr = self.log.err() )
        subprocess.check_call(
            ['dd',
             'if=%s' % self.fullPathToArchive,
             'of=%s' % self.fullPathToPackage,
             #'bs=1M',
             'oflag=append',
             'conv=notrunc' ],
            stdout = self.log.out(),
            stderr = self.log.err() )
        return True

#TODO: These things need more investigation
#Need to understand what the "Installation Tags" are about, they are optional.
#  RPMTAG_PREINPROG, RPMTAG_POSTINPROG, RPMTAG_PREUNPROG, RPMTAG_POSTUNPROG
#  1085            , 1086             , 1087            , 1088
#  these hold the name of the interpreter to use fo the related scripts
#Figure out rich dependencies (AND OR IF)
#There doesn't appear to be any way to do copyrighted works notation?
