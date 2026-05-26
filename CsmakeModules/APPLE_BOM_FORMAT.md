# Apple BOM (Bill of Materials) Format Reference

Findings from reverse-engineering and empirical testing while implementing
`ApplePackage.py` — a pure-Python writer for macOS `.pkg` installer packages.

---

## Resources

| Resource | What it covers |
|---|---|
| [dbg.re — CAR File Format & BOM](https://dbg.re/posts/car-file-format/#the-foundation-bom-file-format) | Detailed binary layout of BOMStore header, block table, BOMTree, BOMPathInfo2, BOMFile, InfoPtr, VIndex; reference hex dumps of real mkbom output |
| [igankevich/stuckliste (GitHub)](https://github.com/igankevich/stuckliste) | Open-source Rust BOM reader/writer; authoritative struct definitions including the free-block list after the block table, PathComponentKey/Value, Metadata, BomInfoEntry; revealed the +52 trailing bytes structure |
| `lsbom(8)`, `pkgutil(1)` | Apple CLI tools used to validate generated BOMs and packages |
| `mkbom(8)` | Apple tool that generates reference BOMs (Xcode/CLT); used for byte-by-byte comparison |

---

## File Layout

A BOM file is a flat binary store with this physical layout:

```
[BOMStore header — 32 bytes]
[Block data — variable, blocks packed contiguously]
[Vars (named-block) section — variable]
[Block index table — fixed 21896 bytes]
```

**Critical:** the vars section comes **before** the block index table.
Apple's own tools write it this way; reversing the order silently corrupts the file
as read by `lsbom`.

---

## BOMStore Header (32 bytes, all big-endian)

| Offset | Field | Type | Value |
|---|---|---|---|
| 0 | magic | `char[8]` | `"BOMStore"` |
| 8 | version | `u32` | `1` |
| 12 | numNonNullBlocks | `u32` | count of active (non-null) blocks |
| 16 | indexOffset | `u32` | byte offset to block index table |
| 20 | indexSize | `u32` | size of block index table (always `21896`) |
| 24 | varsOffset | `u32` | byte offset to vars section |
| 28 | varsSize | `u32` | size of vars section in bytes |

`indexOffset + indexSize` equals the total file size (the block index is always last).

---

## Block Index Table (always 21896 bytes)

Located at `indexOffset`.  All fields big-endian.

```
[numSlots : u32]                     — always 2730 (Apple pre-allocates this)
[slot 0   : (offset:u32, size:u32)]  — null block, always (0, 0)
[slot 1   : (offset:u32, size:u32)]  — first real block
...
[slot 2729: (offset:u32, size:u32)]  — last pre-allocated slot (may be (0,0))
[numFreeSlots : u32]                 — count of freed/recycled blocks
[freeSlot 0 .. 5 : (offset, size)]  — 6 pre-allocated free slots (may be all zeros)
```

Size: `4 + 2730×8 + 4 + 6×8 = 4 + 21840 + 4 + 48 = 21896` ✓

**Gotcha 1:** `lsbom` reads 52 bytes past the last regular block entry.
If the file ends exactly at slot 2729 (i.e. `indexSize = 4 + 2730×8 = 21844`),
`lsbom` crashes with a buffer-overflow at `BOMStream.c:604`.
The free-block section (`4 + 6×8 = 52` bytes) **must** be present even when there are
no free blocks (write all zeros for those 52 bytes).

Block index 0 is always the null block `(offset=0, size=0)`.
Block indices are 1-based for real data.

---

## Vars (Named Blocks) Section

Located at `varsOffset`.  All fields big-endian.

```
[count : u32]
for each var:
    [blockIndex : u32]   — index into block table
    [nameLen    : u8]    — length of name in bytes (no nul terminator stored)
    [name       : char[nameLen]]
```

Standard var names in canonical order:

| Name | Content |
|---|---|
| `BomInfo` | Per-architecture file-count statistics |
| `Paths` | BOMTree → leaf with all file entries |
| `HLIndex` | Hard-links index (usually empty BOMTree) |
| `VIndex` | Virtual-path index (13-byte header wrapping an empty BOMTree) |
| `Size64` | 64-bit sizes index (usually empty BOMTree) |

---

## BOMTree Descriptor (21 bytes)

```c
char   magic[4];      // "tree"
u32    version;       // 1
u32    childBlock;    // block index of root leaf
u32    blockSize;     // 4096 for Paths/HLIndex/Size64; 128 for VIndex
u32    pathCount;     // total number of leaf entries
u8     unused;        // 0
```

---

## BOMPaths Leaf Node

Leaf block size is set by `blockSize` in the BOMTree descriptor.
Block is zero-padded to `blockSize`.

```c
// 12-byte header
u16  isLeaf;       // non-zero = leaf (data) node, 0 = interior node
u16  count;        // number of entries in this node
u32  forwardLink;  // block index of next sibling leaf (0 if none)
u32  backwardLink; // block index of previous sibling leaf (0 if none)

// count × 8 bytes of entries follow:
struct Entry {
    u32 keyBlockIndex;    // InfoPtr block
    u32 valueBlockIndex;  // BOMFile block
};
```

For packages with ≤ 510 entries `(4096 − 12) / 8`, a single leaf node is sufficient
and both link fields are 0.

---

## InfoPtr Block (8 bytes — the "key")

```c
u32  serial;           // 1-based position of this entry in the leaf
u32  pathInfo2Block;   // block index of the BOMPathInfo2 metadata block
```

---

## BOMFile Block (variable — the "value")

```c
u32  parentSerial;     // serial of the parent entry; 0 for the root "."
char basename[];       // nul-terminated UTF-8 filename component
```

---

## BOMPathInfo2 (metadata block)

All fields big-endian.  **No alignment padding anywhere.**

```c
// 23-byte common header
u8   type;      // 1=file, 2=dir, 3=symlink, 4=chardev, 5=blockdev
u8   x0;        // always 1
u16  flags;     // lower nibble: 0xf=regular BOM, 0=path-only
                // upper nibble: 0=not exec, 1=Mach-O, 2=fat binary
u16  mode;      // full st_mode (includes file-type bits, e.g. 0o100644)
u32  uid;
u32  gid;
u32  mtime;     // seconds since Unix epoch
u32  size;      // file size in bytes
u8   x1;        // always 1 (hasExtra flag)
```

Type-specific suffix (immediately after common header, no gap):

| Type | Extra bytes | Total |
|---|---|---|
| Directory | `8 × 0x00` | 31 |
| Regular file | `crc32:u32` + `8 × 0x00` | 35 |
| Symlink | `crc32:u32` + `targetLen:u32` + `target[targetLen]` (nul-terminated) | 31 + target |
| Device | `dev:u32` + `8 × 0x00` | 35 |

For non-executable files the `flags` upper nibble is 0 and the structure ends after the
8 zero bytes.  For Mach-O executables the upper nibble is 1 and additional architecture
records follow (see stuckliste source for full layout).

---

## BomInfo Block (28 bytes minimum)

```c
u32  version;     // 1
u32  numPaths;    // total path count = len(entries) + 1  (Apple convention)
u32  numEntries;  // number of BomInfoEntry records that follow (usually 1)
// BomInfoEntry × numEntries:
struct BomInfoEntry {
    u32  cpuType;   // 0 = generic
    u32  x1;        // 0
    u32  fileSize;  // total payload bytes (wraps at 4 GB)
    u32  x2;        // 0
};
```

`numPaths` must be `len(leaf_entries) + 1` — Apple's `mkbom` always adds 1.
Using `len(leaf_entries)` does not crash `lsbom` but produces an off-by-one
discrepancy when cross-checking with `pkgutil`.

---

## VIndex Header (13 bytes)

A thin wrapper that `pkgutil` expects around the VIndex BOMTree:

```c
u32  version;      // 1
u32  viTreeBlock;  // block index of the VIndex BOMTree descriptor
u32  zero;         // 0
u8   zero2;        // 0
```

---

## Leaf Entry Ordering — BFS Required

**Gotcha 2 (critical):** `lsbom` requires that `parentSerial` values across leaf
entries are **non-decreasing**.  If entries are sorted purely alphabetically
(depth-first / lexicographic order), this invariant breaks whenever a file in a
subdirectory appears before a later sibling directory at a shallower level, e.g.:

```
# DFS / alphabetical — BREAKS lsbom:
.            parent=0  serial=1
./aaa        parent=1  serial=2
./aaa/file   parent=2  serial=3   ← parentSerial goes 1→2
./bbb        parent=1  serial=4   ← parentSerial goes back 2→1  ✗

# BFS (sort by depth, then path) — CORRECT:
.            parent=0  serial=1
./aaa        parent=1  serial=2
./bbb        parent=1  serial=3   ← parentSerial stays at 1  ✓
./aaa/file   parent=2  serial=4   ← parentSerial goes 1→2  ✓
```

The correct sort key is `(path.count('/'), path)` — depth first, then lexicographic
within each depth level.  This is equivalent to breadth-first search (BFS) traversal
of the directory tree and matches the order Apple's `mkbom` produces.

Symptom of wrong ordering: `lsbom` silently stops at the entry just before the first
"backtrack" and reports fewer entries than the BOM actually contains.

---

## Summary of Gotchas

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | `lsbom` crashes at `BOMStream.c:604` ("buffer overflow") | `indexSize = 4 + 2730×8 = 21844`; lsbom reads 52 bytes past the last entry | Always write `indexSize = 21896`; append `4 + 6×8 = 52` free-block bytes (all zeros if no freed blocks) |
| 2 | `lsbom` returns no output / garbage | vars section placed *after* block index table | Write `[header][blocks][vars][block_table]` — vars before table |
| 3 | `lsbom` shows only a partial entry list (stops early, no error) | Entries sorted alphabetically (DFS); `parentSerial` backtracks | Sort by `(depth, path)` — BFS order; guarantees non-decreasing `parentSerial` |
| 4 | `pkgutil --payload-files` count off by one vs BomInfo | `numPaths = len(entries)` | Use `numPaths = len(entries) + 1` to match `mkbom` convention |
