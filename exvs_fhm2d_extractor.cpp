// ============================================================================
// ExvsFhm2dExtractor v1.0.0 (Standalone C++17 Release Edition)
// Universal Native Asset, Texture (46XT -> DX10 DDS/PNG), LMB & SDB1 Unpacker
// Zero external DLL dependencies (Built-in RFC-1951 Raw Deflate Decompressor)
//
// Supports:
//   - Mobile Suit Gundam EXVS2 XBoost (XB / vsac25)
//   - Mobile Suit Gundam EXVS2 OverBoost (OB / vsac27)
//   - Mobile Suit Gundam EXVS2 Infinite Boost (IB / vsac29)
//
// Build (Windows MSVC x64 Standalone Static Release):
//   cl /O2 /MT /EHsc /std:c++17 /utf-8 exvs_fhm2d_extractor.cpp /Fe:exvs_fhm2d_extractor.exe
// Build (macOS / Linux):
//   clang++ -O3 -std=c++17 -pthread exvs_fhm2d_extractor.cpp -o exvs_fhm2d_extractor
// ============================================================================

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace fs = std::filesystem;

static constexpr const char* EXTRACTOR_VERSION = "1.0.0";
static constexpr uint32_t FHM2D_MAGIC = 0xCDB2B7B9;
static constexpr uint32_t SDB1_MAGIC  = 0xCDABB8A9; // EXVS2 SDB1 Binary Table Magic (A9 B8 AB CD)
static constexpr uint32_t SDB1_MAGIC_ASCII = 0x31424453; // "SDB1"
static constexpr uint32_t LMB_MAGIC   = 0x00424D4C; // "LMB\0"
static constexpr uint32_t TEX_MARKER  = 0x54455820; // " TEX"

// 46XT metadata offsets within the 0xB0-byte metadata block
static constexpr size_t TEX_META_SIZE         = 0xB0;
static constexpr size_t TEX_NAME_OFF          = 0x40;
static constexpr size_t TEX_WIDTH_OFF         = 0x84;
static constexpr size_t TEX_HEIGHT_OFF        = 0x88;
static constexpr size_t TEX_ARRAY_SIZE_OFF    = 0x8C;
static constexpr size_t TEX_FORMAT_OFF        = 0x90;
static constexpr size_t TEX_BLOCK_DIM_OFF     = 0x94;
static constexpr size_t TEX_MIP_COUNT_OFF     = 0x98;
static constexpr size_t TEX_DEPTH_OFF         = 0xA0;
static constexpr size_t TEX_ALLOC_SIZE_OFF    = 0xA4;
static constexpr size_t TEX_MARKER_OFF        = 0xA8;

// ============================================================================
// Built-in Zero-Dependency RFC-1951 Raw Deflate (INFLATE) Decompressor
// ============================================================================
namespace raw_inflate {

struct HuffTable {
    uint16_t counts[16]{};
    uint16_t symbols[288]{};
};

struct BitStream {
    const uint8_t* src = nullptr;
    size_t len = 0;
    size_t pos = 0;
    uint32_t bitbuf = 0;
    int bitcnt = 0;

    BitStream(const uint8_t* s, size_t l, size_t start)
        : src(s), len(l), pos(start), bitbuf(0), bitcnt(0) {}

    bool fill(int need) {
        while (bitcnt < need) {
            if (pos >= len) return false;
            bitbuf |= static_cast<uint32_t>(src[pos++]) << bitcnt;
            bitcnt += 8;
        }
        return true;
    }

    bool read_bits(int n, uint32_t& out) {
        if (n == 0) {
            out = 0;
            return true;
        }
        if (!fill(n)) return false;
        out = bitbuf & ((1u << n) - 1u);
        bitbuf >>= n;
        bitcnt -= n;
        return true;
    }

    void align_byte() {
        int drop = bitcnt & 7;
        bitbuf >>= drop;
        bitcnt -= drop;
    }

    size_t exact_bytes_consumed(size_t start_off) const {
        size_t unread_full_bytes = static_cast<size_t>(bitcnt / 8);
        return (pos - unread_full_bytes) - start_off;
    }
};

static bool build_huff(HuffTable& h, const uint8_t* lengths, int num_syms) {
    std::memset(h.counts, 0, sizeof(h.counts));
    for (int i = 0; i < num_syms; ++i) {
        if (lengths[i] > 15) return false;
        h.counts[lengths[i]]++;
    }
    h.counts[0] = 0;
    uint16_t offs[16]{};
    uint16_t sum = 0;
    for (int bits = 1; bits <= 15; ++bits) {
        offs[bits] = sum;
        sum += h.counts[bits];
    }
    for (int i = 0; i < num_syms; ++i) {
        uint8_t l = lengths[i];
        if (l != 0) {
            h.symbols[offs[l]++] = static_cast<uint16_t>(i);
        }
    }
    return true;
}

static bool decode_sym(BitStream& bs, const HuffTable& h, int& sym) {
    int code = 0;
    int first = 0;
    int index = 0;
    for (int len = 1; len <= 15; ++len) {
        uint32_t bit = 0;
        if (!bs.read_bits(1, bit)) return false;
        code |= static_cast<int>(bit);
        int count = h.counts[len];
        if (code - count < first) {
            sym = h.symbols[index + (code - first)];
            return true;
        }
        index += count;
        first += count;
        first <<= 1;
        code <<= 1;
    }
    return false;
}

static void build_fixed_tables(HuffTable& litlen, HuffTable& dist) {
    uint8_t ll[288];
    for (int i = 0; i < 144; ++i) ll[i] = 8;
    for (int i = 144; i < 256; ++i) ll[i] = 9;
    for (int i = 256; i < 280; ++i) ll[i] = 7;
    for (int i = 280; i < 288; ++i) ll[i] = 8;
    build_huff(litlen, ll, 288);

    uint8_t dl[30];
    for (int i = 0; i < 30; ++i) dl[i] = 5;
    build_huff(dist, dl, 30);
}

static bool inflate_stream(const uint8_t* src, size_t src_len, size_t start_off,
                           std::vector<uint8_t>& out, size_t& consumed) {
    if (start_off >= src_len) return false;
    BitStream bs(src, src_len, start_off);
    out.clear();
    out.reserve(65536);

    static const uint16_t len_base[29] = {
        3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31,
        35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258
    };
    static const uint8_t len_extra[29] = {
        0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2,
        3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0
    };
    static const uint16_t dist_base[30] = {
        1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193,
        257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145,
        8193, 12289, 16385, 24577
    };
    static const uint8_t dist_extra[30] = {
        0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
        7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13
    };
    static const uint8_t clen_order[19] = {
        16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15
    };

    uint32_t bfinal = 0;
    do {
        uint32_t btype = 0;
        if (!bs.read_bits(1, bfinal)) return false;
        if (!bs.read_bits(2, btype)) return false;

        if (btype == 0) {
            bs.align_byte();
            uint32_t len = 0, nlen = 0;
            if (!bs.read_bits(16, len) || !bs.read_bits(16, nlen)) return false;
            if ((len ^ 0xFFFFu) != nlen) return false;
            for (uint32_t i = 0; i < len; ++i) {
                uint32_t byte_val = 0;
                if (!bs.read_bits(8, byte_val)) return false;
                out.push_back(static_cast<uint8_t>(byte_val));
            }
        } else if (btype == 1 || btype == 2) {
            HuffTable litlen{}, dist{};
            if (btype == 1) {
                build_fixed_tables(litlen, dist);
            } else {
                uint32_t hlit = 0, hdist = 0, hclen = 0;
                if (!bs.read_bits(5, hlit) || !bs.read_bits(5, hdist) || !bs.read_bits(4, hclen))
                    return false;
                hlit += 257;
                hdist += 1;
                hclen += 4;
                if (hlit > 286 || hdist > 30) return false;

                uint8_t clen[19]{};
                for (uint32_t i = 0; i < hclen; ++i) {
                    uint32_t v = 0;
                    if (!bs.read_bits(3, v)) return false;
                    clen[clen_order[i]] = static_cast<uint8_t>(v);
                }
                HuffTable code_table{};
                if (!build_huff(code_table, clen, 19)) return false;

                uint8_t lengths[320]{};
                uint32_t idx = 0;
                uint32_t total = hlit + hdist;
                while (idx < total) {
                    int sym = 0;
                    if (!decode_sym(bs, code_table, sym)) return false;
                    if (sym < 16) {
                        lengths[idx++] = static_cast<uint8_t>(sym);
                    } else if (sym == 16) {
                        if (idx == 0) return false;
                        uint32_t rep = 0;
                        if (!bs.read_bits(2, rep)) return false;
                        rep += 3;
                        uint8_t prev = lengths[idx - 1];
                        if (idx + rep > total) return false;
                        for (uint32_t r = 0; r < rep; ++r) lengths[idx++] = prev;
                    } else if (sym == 17) {
                        uint32_t rep = 0;
                        if (!bs.read_bits(3, rep)) return false;
                        rep += 3;
                        if (idx + rep > total) return false;
                        for (uint32_t r = 0; r < rep; ++r) lengths[idx++] = 0;
                    } else if (sym == 18) {
                        uint32_t rep = 0;
                        if (!bs.read_bits(7, rep)) return false;
                        rep += 11;
                        if (idx + rep > total) return false;
                        for (uint32_t r = 0; r < rep; ++r) lengths[idx++] = 0;
                    } else {
                        return false;
                    }
                }
                if (!build_huff(litlen, lengths, static_cast<int>(hlit))) return false;
                if (!build_huff(dist, lengths + hlit, static_cast<int>(hdist))) return false;
            }

            while (true) {
                int sym = 0;
                if (!decode_sym(bs, litlen, sym)) return false;
                if (sym < 256) {
                    out.push_back(static_cast<uint8_t>(sym));
                } else if (sym == 256) {
                    break;
                } else {
                    sym -= 257;
                    if (sym >= 29) return false;
                    uint32_t extra = 0;
                    if (!bs.read_bits(len_extra[sym], extra)) return false;
                    uint32_t length = len_base[sym] + extra;

                    int dsym = 0;
                    if (!decode_sym(bs, dist, dsym)) return false;
                    if (dsym >= 30) return false;
                    uint32_t dextra = 0;
                    if (!bs.read_bits(dist_extra[dsym], dextra)) return false;
                    uint32_t distance = dist_base[dsym] + dextra;
                    if (distance > out.size()) return false;

                    size_t copy_pos = out.size() - distance;
                    for (uint32_t i = 0; i < length; ++i) {
                        out.push_back(out[copy_pos + i]);
                    }
                }
            }
        } else {
            return false;
        }
    } while (!bfinal);

    consumed = bs.exact_bytes_consumed(start_off);
    return consumed > 0;
}

} // namespace raw_inflate

// ============================================================================
// Binary Helpers & Format Definitions
// ============================================================================
static inline uint32_t read_u32(const uint8_t* p, size_t off) {
    uint32_t v = 0;
    std::memcpy(&v, p + off, sizeof(uint32_t));
    return v;
}

static inline uint64_t read_u64(const uint8_t* p, size_t off) {
    uint64_t v = 0;
    std::memcpy(&v, p + off, sizeof(uint64_t));
    return v;
}

struct FormatDesc {
    uint32_t fhm2d_fmt;
    const char* name;
    const char* desc;
    uint32_t dxgi_fmt;
    bool is_rgba8;
    uint32_t bytes_per_block; // 16 for BC3/BC5/BC7, 8 for BC1/BC4, 4 for RGBA8
};

static const FormatDesc kSupportedFormats[] = {
    {0x4E0, "bc7",        "BC7_UNORM",            98, false, 16},
    {0x4E5, "bc7_srgb",   "BC7_UNORM_SRGB",       99, false, 16},
    {0x4A0, "bc3",        "BC3_UNORM",            77, false, 16},
    {0x4A5, "bc3_srgb",   "BC3_UNORM_SRGB",       78, false, 16},
    {0x480, "bc1",        "BC1_UNORM",            71, false, 8},
    {0x485, "bc1_srgb",   "BC1_UNORM_SRGB",       72, false, 8},
    {0x180, "bc4",        "BC4_UNORM",            80, false, 8},
    {0x280, "bc5",        "BC5_UNORM",            83, false, 16},
    {0x400, "rgba8",      "R8G8B8A8_UNORM",       28, true,  4},
    {0x405, "rgba8_srgb", "R8G8B8A8_UNORM_SRGB",  29, true,  4},
};

static const FormatDesc* find_format(uint32_t code) {
    for (const auto& f : kSupportedFormats) {
        if (f.fhm2d_fmt == code) return &f;
    }
    return nullptr;
}

static size_t calc_level_size(uint32_t w, uint32_t h, const FormatDesc& f) {
    if (f.is_rgba8) {
        return static_cast<size_t>(w) * static_cast<size_t>(h) * 4u;
    }
    size_t bw = (w + 3u) / 4u;
    size_t bh = (h + 3u) / 4u;
    return bw * bh * f.bytes_per_block;
}

static size_t calc_mip_chain_size(uint32_t w, uint32_t h, uint32_t mips, const FormatDesc& f) {
    size_t total = 0;
    for (uint32_t m = 0; m < mips; ++m) {
        total += calc_level_size(w, h, f);
        w = std::max(1u, w / 2u);
        h = std::max(1u, h / 2u);
    }
    return total;
}

static std::vector<uint8_t> build_dx10_dds(uint32_t w, uint32_t h, uint32_t mips,
                                           const FormatDesc& f, const uint8_t* pixels,
                                           size_t pixel_bytes) {
    std::vector<uint8_t> dds(148 + pixel_bytes, 0);
    std::memcpy(dds.data(), "DDS ", 4);
    auto write32 = [&](size_t off, uint32_t val) {
        std::memcpy(dds.data() + off, &val, 4);
    };
    write32(0x04, 124);
    if (f.is_rgba8) {
        uint32_t flags = 0x0000100Fu | (mips > 1 ? 0x00020000u : 0u);
        write32(0x08, flags);
        write32(0x0C, h);
        write32(0x10, w);
        write32(0x14, w * 4u);
    } else {
        uint32_t flags = 0x00081007u | (mips > 1 ? 0x00020000u : 0u);
        write32(0x08, flags);
        write32(0x0C, h);
        write32(0x10, w);
        write32(0x14, static_cast<uint32_t>(calc_level_size(w, h, f)));
    }
    write32(0x18, 1);
    write32(0x1C, mips);
    write32(0x4C, 32);
    write32(0x50, 0x4); // DDPF_FOURCC
    std::memcpy(dds.data() + 0x54, "DX10", 4);
    uint32_t caps = 0x1000u | (mips > 1 ? 0x00400008u : 0u);
    write32(0x6C, caps);
    write32(0x80, f.dxgi_fmt);
    write32(0x84, 3); // D3D10_RESOURCE_DIMENSION_TEXTURE2D
    write32(0x8C, 1); // arraySize = 1
    std::memcpy(dds.data() + 148, pixels, pixel_bytes);
    return dds;
}

// ============================================================================
// Texture Record & Container Unpacking Structures
// ============================================================================
struct TextureRecord {
    int texture_index = 0;
    int group_index = 0;
    std::string group_label;
    std::string embedded_name;
    int embedded_index = 0;
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t mip_count = 1;
    uint32_t array_size = 1;
    uint32_t depth = 1;
    uint32_t fhm2d_format = 0;
    std::string storage_format;
    uint32_t dxgi_format = 0;
    size_t data_offset = 0;
    size_t metadata_offset = 0;
    uint32_t data_size = 0;
    uint32_t allocation_size = 0;
    std::string dds_filename;
};

struct Sdb1TableSummary {
    int table_index = 0;
    size_t payload_offset = 0;
    size_t table_size = 0;
    uint32_t num_rows = 0;
    uint32_t num_cols = 0;
    uint32_t row_stride = 0;
    std::vector<uint32_t> col_hashes;
    std::vector<uint32_t> row_keys;
    std::vector<std::string> sample_strings;
};

struct PackageScanResult {
    std::string pkg_name;
    size_t file_size = 0;
    size_t block_count = 0;
    size_t texture_count = 0;
    size_t sdb1_table_count = 0;
    std::string group_labels;
    std::vector<std::string> embedded_names;
    std::vector<std::string> lmb_strings;
    std::vector<std::string> lmb_pngs;
};

static std::vector<std::string> extract_ascii_strings(const std::vector<uint8_t>& buf, size_t min_len = 4) {
    std::vector<std::string> result;
    std::string cur;
    for (uint8_t c : buf) {
        if (c >= 32 && c < 127) {
            cur.push_back(static_cast<char>(c));
        } else {
            if (cur.size() >= min_len) result.push_back(cur);
            cur.clear();
        }
    }
    if (cur.size() >= min_len) result.push_back(cur);
    return result;
}

static std::string json_escape(const std::string& s) {
    std::ostringstream oss;
    for (unsigned char c : s) {
        switch (c) {
            case '"':  oss << "\\\""; break;
            case '\\': oss << "\\\\"; break;
            case '\b': oss << "\\b";  break;
            case '\f': oss << "\\f";  break;
            case '\n': oss << "\\n";  break;
            case '\r': oss << "\\r";  break;
            case '\t': oss << "\\t";  break;
            default:
                if (c < 0x20) {
                    oss << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c) << std::dec;
                } else {
                    oss << static_cast<char>(c);
                }
        }
    }
    return oss.str();
}

static std::vector<std::string> parse_group_labels(const uint8_t* trailing, size_t trailing_len) {
    std::vector<std::string> labels;
    size_t len = 0;
    while (len < trailing_len && trailing[len] != 0) len++;
    if (len == 0 || (len % 2) != 0) return labels;
    for (size_t i = 0; i < len; i += 2) {
        char c0 = static_cast<char>(trailing[i]);
        char c1 = static_cast<char>(trailing[i + 1]);
        if (c0 < 'a' || c0 > 'z' || c1 < '0' || c1 > '9') {
            labels.clear();
            return labels;
        }
        labels.push_back(std::string{c0, c1});
    }
    return labels;
}

static std::vector<TextureRecord> scan_payload_textures(
    const std::vector<uint8_t>& payload,
    const std::vector<std::string>& group_labels
) {
    std::vector<TextureRecord> textures;
    if (payload.size() < TEX_META_SIZE) return textures;

    for (size_t i = TEX_NAME_OFF; i + (TEX_META_SIZE - TEX_NAME_OFF) <= payload.size(); ++i) {
        if (payload[i] != '4' || payload[i + 1] != '6' || payload[i + 2] != 'X' || payload[i + 3] != 'T')
            continue;

        size_t meta_off = i - TEX_NAME_OFF;
        uint32_t marker = read_u32(payload.data(), meta_off + TEX_MARKER_OFF);
        if (marker != TEX_MARKER) continue;

        std::string name;
        for (size_t j = 0; j < 0x40; ++j) {
            uint8_t ch = payload[i + j];
            if (ch == 0) break;
            if (ch < 33 || ch > 126) {
                name.clear();
                break;
            }
            name.push_back(static_cast<char>(ch));
        }
        if (name.size() <= 4) continue;

        uint32_t data_size   = read_u32(payload.data(), meta_off);
        uint32_t width       = read_u32(payload.data(), meta_off + TEX_WIDTH_OFF);
        uint32_t height      = read_u32(payload.data(), meta_off + TEX_HEIGHT_OFF);
        uint32_t array_size  = read_u32(payload.data(), meta_off + TEX_ARRAY_SIZE_OFF);
        uint32_t fmt_code    = read_u32(payload.data(), meta_off + TEX_FORMAT_OFF);
        uint32_t block_dim   = read_u32(payload.data(), meta_off + TEX_BLOCK_DIM_OFF);
        uint32_t mip_count   = read_u32(payload.data(), meta_off + TEX_MIP_COUNT_OFF);
        uint32_t depth       = read_u32(payload.data(), meta_off + TEX_DEPTH_OFF);
        uint32_t alloc_size  = read_u32(payload.data(), meta_off + TEX_ALLOC_SIZE_OFF);

        if (width == 0 || height == 0 || mip_count < 1 || array_size != 1 || depth != 1) continue;
        if (block_dim != 2 && block_dim != 4) continue;
        if (alloc_size < data_size || meta_off < alloc_size) continue;

        const FormatDesc* fdesc = find_format(fmt_code);
        if (!fdesc) continue;

        size_t expected_top = calc_level_size(width, height, *fdesc);
        size_t expected_chain = calc_mip_chain_size(width, height, mip_count, *fdesc);
        if (data_size != expected_top || alloc_size != expected_chain) continue;

        int emb_idx = static_cast<int>(textures.size());
        if (name.rfind("46XTimg-", 0) == 0 && name.size() > 8) {
            bool all_digits = true;
            for (size_t k = 8; k < name.size(); ++k) {
                if (name[k] < '0' || name[k] > '9') { all_digits = false; break; }
            }
            if (all_digits) emb_idx = std::atoi(name.c_str() + 8);
        }

        TextureRecord rec;
        rec.texture_index = static_cast<int>(textures.size());
        rec.embedded_name = name;
        rec.embedded_index = emb_idx;
        rec.width = width;
        rec.height = height;
        rec.mip_count = mip_count;
        rec.array_size = array_size;
        rec.depth = depth;
        rec.fhm2d_format = fmt_code;
        rec.storage_format = fdesc->name;
        rec.dxgi_format = fdesc->dxgi_fmt;
        rec.data_offset = meta_off - alloc_size;
        rec.metadata_offset = meta_off;
        rec.data_size = data_size;
        rec.allocation_size = alloc_size;
        textures.push_back(std::move(rec));
    }

    int grp_idx = 0;
    int prev_emb = -1;
    for (auto& t : textures) {
        if (prev_emb >= 0 && t.embedded_index <= prev_emb) {
            grp_idx++;
        }
        t.group_index = grp_idx;
        if (static_cast<size_t>(grp_idx) < group_labels.size()) {
            t.group_label = group_labels[grp_idx];
        } else {
            char buf[16];
            std::snprintf(buf, sizeof(buf), "g%02d", grp_idx);
            t.group_label = buf;
        }
        char fname[256];
        std::snprintf(fname, sizeof(fname), "%s_%05d_%ux%u_%s.dds",
                      t.group_label.c_str(), t.embedded_index, t.width, t.height, t.storage_format.c_str());
        t.dds_filename = fname;
        prev_emb = t.embedded_index;
    }
    return textures;
}

static bool parse_sdb1_blob(const uint8_t* data, size_t len, int tbl_idx, size_t offset_in_pkg, Sdb1TableSummary& out) {
    if (len < 0x20) return false;
    uint32_t magic = read_u32(data, 0);
    if (magic != SDB1_MAGIC && magic != SDB1_MAGIC_ASCII) return false;

    out.table_index = tbl_idx;
    out.payload_offset = offset_in_pkg;
    out.table_size = read_u32(data, 0x08);
    if (out.table_size == 0 || out.table_size > len) out.table_size = len;
    out.num_rows = read_u32(data, 0x10);
    out.num_cols = read_u32(data, 0x14);
    out.row_stride = read_u32(data, 0x18);

    if (out.num_cols > 10000 || out.num_rows > 1000000) return false;
    if (0x20 + static_cast<size_t>(out.num_cols) * 4 > len) return false;

    out.col_hashes.resize(out.num_cols);
    for (uint32_t c = 0; c < out.num_cols; ++c) {
        out.col_hashes[c] = read_u32(data, 0x20 + c * 4);
    }

    size_t rk_off = (static_cast<size_t>(out.num_cols) + 2) * 16;
    if (rk_off + static_cast<size_t>(out.num_rows) * 4 <= len) {
        out.row_keys.resize(out.num_rows);
        for (uint32_t r = 0; r < out.num_rows; ++r) {
            out.row_keys[r] = read_u32(data, rk_off + r * 4);
        }
    }

    size_t rows_end = rk_off + static_cast<size_t>(out.num_rows) * 4 +
                      static_cast<size_t>(out.num_rows) * static_cast<size_t>(out.row_stride);
    if (rows_end < len) {
        std::vector<uint8_t> str_region(data + rows_end, data + len);
        auto strs = extract_ascii_strings(str_region, 3);
        for (const auto& s : strs) {
            if (out.sample_strings.size() >= 25) break;
            out.sample_strings.push_back(s);
        }
    }
    return true;
}

static fs::path find_texconv_exe(const fs::path& self_exe) {
    std::vector<fs::path> candidates;
    if (!self_exe.empty()) {
        fs::path base = self_exe.parent_path();
        candidates.push_back(base / "texconv.exe");
        candidates.push_back(base / "tools" / "texconv.exe");
        candidates.push_back(base / "_internal" / "core" / "tools" / "texconv.exe");
    }
    candidates.push_back(R"(G:\GitHubRepo\fhm2d-unpack\_internal\core\tools\texconv.exe)");
    for (const auto& c : candidates) {
        std::error_code ec;
        if (fs::exists(c, ec) && fs::is_regular_file(c, ec)) return c;
    }
    return {};
}

// ============================================================================
// Core Unpacker / Scanner Implementation
// ============================================================================
struct UnpackOptions {
    bool export_dds = true;
    bool export_png = false;
    bool export_raw_blocks = false;
    bool export_lmb = true;
    bool export_sdb1 = true;
    fs::path texconv_path;
};

static bool unpack_fhm2d_file(
    const fs::path& fhm_path,
    const fs::path& out_root,
    const UnpackOptions& opts,
    PackageScanResult& res
) {
    res.pkg_name = fhm_path.stem().string();
    std::ifstream ifs(fhm_path, std::ios::binary | std::ios::ate);
    if (!ifs) return false;
    res.file_size = static_cast<size_t>(ifs.tellg());
    if (res.file_size < 0x30) return false;

    std::vector<uint8_t> blob(res.file_size);
    ifs.seekg(0);
    ifs.read(reinterpret_cast<char*>(blob.data()), blob.size());
    if (read_u32(blob.data(), 0) != FHM2D_MAGIC) return false;

    std::vector<std::vector<uint8_t>> blocks;
    std::vector<size_t> block_offsets;
    size_t offset = 0x30;
    while (offset < blob.size()) {
        std::vector<uint8_t> dec;
        size_t consumed = 0;
        if (!raw_inflate::inflate_stream(blob.data(), blob.size(), offset, dec, consumed) || consumed == 0) {
            break;
        }
        block_offsets.push_back(offset);
        blocks.push_back(std::move(dec));
        offset += consumed;
    }
    if (blocks.empty()) return false;

    // Reconstruct main payload from index block[0] declared_size (at +0x34)
    uint64_t declared_payload_size = 0;
    if (blocks[0].size() >= 0x3C) {
        declared_payload_size = read_u64(blocks[0].data(), 0x34);
    }

    std::vector<uint8_t> payload;
    size_t trailing_file_off = offset;
    if (blocks.size() >= 2 && declared_payload_size > 0) {
        payload.reserve(static_cast<size_t>(declared_payload_size));
        for (size_t bi = 1; bi < blocks.size(); ++bi) {
            size_t rem = static_cast<size_t>(declared_payload_size) - payload.size();
            if (rem == 0) break;
            const auto& bdata = blocks[bi];
            size_t take = std::min(rem, bdata.size());
            payload.insert(payload.end(), bdata.begin(), bdata.begin() + take);
            if (payload.size() == declared_payload_size) {
                trailing_file_off = (bi + 1 < block_offsets.size()) ? block_offsets[bi + 1] : offset;
                break;
            }
        }
    }

    // Check group labels trailer (e.g. "a1a2a3\0") and any trailing LMB deflate blocks
    std::vector<std::string> group_labels;
    if (trailing_file_off < blob.size()) {
        group_labels = parse_group_labels(blob.data() + trailing_file_off, blob.size() - trailing_file_off);
        for (const auto& gl : group_labels) res.group_labels += gl;

        if (!group_labels.empty()) {
            size_t lmb_off = trailing_file_off + res.group_labels.size() + 1;
            while (lmb_off < blob.size()) {
                std::vector<uint8_t> dec;
                size_t consumed = 0;
                if (!raw_inflate::inflate_stream(blob.data(), blob.size(), lmb_off, dec, consumed) || consumed == 0) {
                    break;
                }
                blocks.push_back(std::move(dec));
                lmb_off += consumed;
            }
        }
    }

    res.block_count = blocks.size();
    fs::path pkg_out = out_root / res.pkg_name;

    // 1. Scan & optionally export 46XT textures as DX10 DDS
    auto textures = scan_payload_textures(payload, group_labels);
    res.texture_count = textures.size();
    for (const auto& t : textures) {
        res.embedded_names.push_back(t.embedded_name);
    }

    if (opts.export_dds && !textures.empty()) {
        fs::path dds_dir = pkg_out / "dds";
        fs::create_directories(dds_dir);
        std::ofstream csv_ofs(pkg_out / "textures.csv");
        csv_ofs << "texture_index,group_index,group_label,embedded_name,embedded_index,width,height,mip_count,fhm2d_format,storage_format,dxgi_format,data_offset,allocation_size,dds_output\n";

        for (const auto& t : textures) {
            const FormatDesc* fdesc = find_format(t.fhm2d_format);
            if (!fdesc) continue;
            auto dds_bytes = build_dx10_dds(
                t.width, t.height, t.mip_count, *fdesc,
                payload.data() + t.data_offset, t.allocation_size
            );
            std::ofstream dofs(dds_dir / t.dds_filename, std::ios::binary);
            dofs.write(reinterpret_cast<const char*>(dds_bytes.data()), dds_bytes.size());

            csv_ofs << t.texture_index << "," << t.group_index << "," << t.group_label << ",\""
                    << t.embedded_name << "\"," << t.embedded_index << "," << t.width << ","
                    << t.height << "," << t.mip_count << ",0x" << std::hex << t.fhm2d_format << std::dec
                    << "," << t.storage_format << "," << t.dxgi_format << "," << t.data_offset << ","
                    << t.allocation_size << ",dds/" << t.dds_filename << "\n";
        }

        if (opts.export_png && !opts.texconv_path.empty()) {
            fs::path png_dir = pkg_out / "png";
            fs::create_directories(png_dir);
            std::string cmd = "\"" + opts.texconv_path.string() + "\" -ft png -y -o \"" +
                              png_dir.string() + "\" \"" + (dds_dir / "*.dds").string() + "\" >nul 2>&1";
            std::system(cmd.c_str());
        }
    }

    // 2. Scan & export LMB animation blocks and SDB1 database tables
    std::vector<Sdb1TableSummary> sdb1_tables;
    int lmb_idx = 0;
    for (size_t b = 0; b < blocks.size(); ++b) {
        const auto& d = blocks[b];
        if (opts.export_raw_blocks) {
            fs::path raw_dir = pkg_out / "blocks";
            fs::create_directories(raw_dir);
            char bname[64];
            std::snprintf(bname, sizeof(bname), "block_%03zu.bin", b);
            std::ofstream bofs(raw_dir / bname, std::ios::binary);
            bofs.write(reinterpret_cast<const char*>(d.data()), d.size());
        }

        if (d.size() >= 4 && read_u32(d.data(), 0) == LMB_MAGIC) {
            auto strs = extract_ascii_strings(d, 4);
            for (const auto& s : strs) {
                if (res.lmb_strings.size() < 64) res.lmb_strings.push_back(s);
                if (s.size() > 4 && (s.substr(s.size() - 4) == ".png" || s.substr(s.size() - 4) == ".PNG")) {
                    res.lmb_pngs.push_back(s);
                }
            }
            if (opts.export_lmb) {
                fs::path lmb_dir = pkg_out / "lmb";
                fs::create_directories(lmb_dir);
                char lname[64];
                std::snprintf(lname, sizeof(lname), "anim_%02d_block%03zu.lmb", lmb_idx++, b);
                std::ofstream lofs(lmb_dir / lname, std::ios::binary);
                lofs.write(reinterpret_cast<const char*>(d.data()), d.size());
            }
        }
    }

    // Scan concatenated payload for SDB1 tables (0xCDABB8A9 / "SDB1")
    for (size_t off = 0; off + 0x20 <= payload.size(); off += 16) {
        uint32_t m = read_u32(payload.data(), off);
        if (m != SDB1_MAGIC && m != SDB1_MAGIC_ASCII) continue;
        Sdb1TableSummary st;
        size_t max_rem = payload.size() - off;
        if (parse_sdb1_blob(payload.data() + off, max_rem, static_cast<int>(sdb1_tables.size()), off, st)) {
            sdb1_tables.push_back(st);
            if (opts.export_sdb1) {
                fs::path sdb_dir = pkg_out / "sdb1_raw";
                fs::create_directories(sdb_dir);
                char sname[64];
                std::snprintf(sname, sizeof(sname), "table_%02d_off0x%06zX.sdb1", st.table_index, off);
                std::ofstream sofs(sdb_dir / sname, std::ios::binary);
                sofs.write(reinterpret_cast<const char*>(payload.data() + off), st.table_size);
            }
            size_t adv = ((st.table_size + 15) / 16) * 16;
            if (adv >= 16) off += (adv - 16);
        }
    }

    res.sdb1_table_count = sdb1_tables.size();
    if (opts.export_sdb1 && !sdb1_tables.empty()) {
        fs::create_directories(pkg_out);
        std::ofstream jofs(pkg_out / "sdb1_tables.json");
        jofs << "{\n  \"package\": \"" << res.pkg_name << "\",\n"
             << "  \"sdb1_table_count\": " << sdb1_tables.size() << ",\n"
             << "  \"tables\": [\n";
        for (size_t i = 0; i < sdb1_tables.size(); ++i) {
            const auto& t = sdb1_tables[i];
            jofs << "    {\n"
                 << "      \"table_index\": " << t.table_index << ",\n"
                 << "      \"payload_offset\": " << t.payload_offset << ",\n"
                 << "      \"table_size\": " << t.table_size << ",\n"
                 << "      \"num_rows\": " << t.num_rows << ",\n"
                 << "      \"num_cols\": " << t.num_cols << ",\n"
                 << "      \"row_stride\": " << t.row_stride << ",\n"
                 << "      \"col_hashes\": [";
            for (size_t c = 0; c < t.col_hashes.size(); ++c) {
                char hbuf[16];
                std::snprintf(hbuf, sizeof(hbuf), "\"0x%08X\"", t.col_hashes[c]);
                jofs << hbuf << (c + 1 < t.col_hashes.size() ? ", " : "");
            }
            jofs << "],\n      \"row_keys_preview\": [";
            size_t rk_cnt = std::min<size_t>(t.row_keys.size(), 30);
            for (size_t r = 0; r < rk_cnt; ++r) {
                jofs << t.row_keys[r] << (r + 1 < rk_cnt ? ", " : "");
            }
            jofs << "],\n      \"sample_strings\": [";
            for (size_t s = 0; s < t.sample_strings.size(); ++s) {
                jofs << "\"" << json_escape(t.sample_strings[s]) << "\""
                     << (s + 1 < t.sample_strings.size() ? ", " : "");
            }
            jofs << "]\n    }" << (i + 1 < sdb1_tables.size() ? "," : "") << "\n";
        }
        jofs << "  ]\n}\n";
    }

    return true;
}

static void print_banner() {
    std::cout << "========================================================================\n"
              << "  ExvsFhm2dExtractor v" << EXTRACTOR_VERSION << " (Standalone x64 Release - Zero Dependencies)\n"
              << "  Supports EXVS2 XBoost / OverBoost / Infinite Boost (.fhm2d / 46XT / SDB1 / LMB)\n"
              << "========================================================================\n";
}

static void print_usage() {
    print_banner();
    std::cout << "\nUsage:\n"
              << "  1. Single File / Drag-and-Drop Unpack (DDS + LMB + SDB1):\n"
              << "     exvs_fhm2d_extractor <file.fhm2d> [output_dir] [--png] [--raw]\n\n"
              << "  2. Batch Extract Directory or File:\n"
              << "     exvs_fhm2d_extractor extract <input_file_or_dir> <output_dir> [--png] [--raw] [threads]\n\n"
              << "  3. Batch Fast Manifest Scan (CSV):\n"
              << "     exvs_fhm2d_extractor scan <dplcache_release_dir> <output_manifest.csv> [threads]\n\n"
              << "  4. SDB1 Database Inspector (JSON):\n"
              << "     exvs_fhm2d_extractor sdb1 <0xDFD38C70.fhm2d> [output_dir]\n\n";
}

static int run_single_or_batch_extract(
    const fs::path& in_path,
    const fs::path& out_root,
    const UnpackOptions& opts,
    int num_threads
) {
    if (fs::is_regular_file(in_path)) {
        PackageScanResult res;
        std::cout << "[ExvsFhm2dExtractor] Unpacking " << in_path.filename().string() << " ...\n";
        if (!unpack_fhm2d_file(in_path, out_root, opts, res)) {
            std::cerr << "[Error] Failed to unpack or invalid .fhm2d header: " << in_path.string() << "\n";
            return 1;
        }
        std::cout << "  -> Package:       " << res.pkg_name << " (" << res.file_size << " bytes)\n"
                  << "  -> Deflate Blks:  " << res.block_count << "\n"
                  << "  -> 46XT Textures: " << res.texture_count << " (DDS exported)\n"
                  << "  -> SDB1 Tables:   " << res.sdb1_table_count << "\n"
                  << "  -> LMB Sprites:   " << res.lmb_pngs.size() << "\n"
                  << "  -> Output Folder: " << (out_root / res.pkg_name).string() << "\n";
        return 0;
    }

    if (!fs::is_directory(in_path)) {
        std::cerr << "[Error] Input path does not exist: " << in_path.string() << "\n";
        return 1;
    }

    std::vector<fs::path> files;
    for (const auto& entry : fs::directory_iterator(in_path)) {
        if (entry.is_regular_file() && entry.path().extension() == ".fhm2d") {
            files.push_back(entry.path());
        }
    }
    std::sort(files.begin(), files.end());
    std::cout << "[ExvsFhm2dExtractor] Extracting " << files.size() << " .fhm2d packages using "
              << num_threads << " threads into " << out_root.string() << " ...\n";

    std::atomic<size_t> next_idx{0};
    std::atomic<size_t> total_tex{0};
    std::atomic<size_t> total_sdb1{0};

    auto worker = [&]() {
        while (true) {
            size_t i = next_idx.fetch_add(1);
            if (i >= files.size()) break;
            PackageScanResult r;
            if (unpack_fhm2d_file(files[i], out_root, opts, r)) {
                total_tex.fetch_add(r.texture_count);
                total_sdb1.fetch_add(r.sdb1_table_count);
            }
        }
    };

    std::vector<std::thread> pool;
    for (int t = 0; t < num_threads; ++t) pool.emplace_back(worker);
    for (auto& th : pool) th.join();

    std::cout << "[ExvsFhm2dExtractor] Batch Extract Complete!\n"
              << "  -> Packages Processed: " << files.size() << "\n"
              << "  -> Total DDS Textures: " << total_tex.load() << "\n"
              << "  -> Total SDB1 Tables:  " << total_sdb1.load() << "\n";
    return 0;
}

int main(int argc, char** argv) {
#ifdef _WIN32
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCP(CP_UTF8);
#endif

    fs::path self_exe = (argc >= 1 && argv[0]) ? fs::path(argv[0]) : fs::path();

    // Interactive prompt if double-clicked with no arguments
    if (argc == 1) {
        print_usage();
        std::cout << "Drag & drop a .fhm2d file or folder here and press Enter (or press Enter to exit):\n> ";
        std::string line;
        std::getline(std::cin, line);
        // Trim quotes and whitespace
        while (!line.empty() && (line.front() == ' ' || line.front() == '"' || line.front() == '\''))
            line.erase(line.begin());
        while (!line.empty() && (line.back() == ' ' || line.back() == '"' || line.back() == '\'' || line.back() == '\r' || line.back() == '\n'))
            line.pop_back();
        if (line.empty()) return 0;

        fs::path target(line);
        fs::path out_dir = target.parent_path() / "unpacked_output";
        UnpackOptions opts;
        opts.export_dds = true;
        opts.export_lmb = true;
        opts.export_sdb1 = true;
        opts.texconv_path = find_texconv_exe(self_exe);
        opts.export_png = !opts.texconv_path.empty();
        int rc = run_single_or_batch_extract(target, out_dir, opts, std::max(1u, std::thread::hardware_concurrency()));
        std::cout << "\nPress Enter to exit...";
        std::string dummy;
        std::getline(std::cin, dummy);
        return rc;
    }

    std::string mode = argv[1];
    if (mode == "-h" || mode == "--help" || mode == "/?") {
        print_usage();
        return 0;
    }

    // Mode: scan <dplcache_dir> <output_csv> [threads]
    if (mode == "scan" || (argc >= 3 && fs::path(argv[2]).extension() == ".csv")) {
        int arg_base = (mode == "scan") ? 2 : 1;
        if (argc < arg_base + 2) {
            print_usage();
            return 1;
        }
        fs::path in_dir = argv[arg_base];
        fs::path out_csv = argv[arg_base + 1];
        int num_threads = (argc > arg_base + 2)
                              ? std::max(1, std::atoi(argv[arg_base + 2]))
                              : static_cast<int>(std::max(1u, std::thread::hardware_concurrency()));

        std::vector<fs::path> files;
        for (const auto& entry : fs::directory_iterator(in_dir)) {
            if (entry.is_regular_file() && entry.path().extension() == ".fhm2d") {
                files.push_back(entry.path());
            }
        }
        std::sort(files.begin(), files.end());
        std::cout << "[ExvsFhm2dExtractor] Scanning " << files.size() << " .fhm2d packages using "
                  << num_threads << " threads...\n";

        std::vector<PackageScanResult> results(files.size());
        std::atomic<size_t> next_idx{0};
        UnpackOptions scan_opts;
        scan_opts.export_dds = false;
        scan_opts.export_lmb = false;
        scan_opts.export_sdb1 = false;
        scan_opts.export_raw_blocks = false;

        auto worker = [&]() {
            while (true) {
                size_t i = next_idx.fetch_add(1);
                if (i >= files.size()) break;
                unpack_fhm2d_file(files[i], out_csv.parent_path(), scan_opts, results[i]);
            }
        };

        std::vector<std::thread> pool;
        for (int t = 0; t < num_threads; ++t) pool.emplace_back(worker);
        for (auto& th : pool) th.join();

        if (!out_csv.parent_path().empty()) {
            fs::create_directories(out_csv.parent_path());
        }
        std::ofstream ofs(out_csv);
        ofs << "package,file_size,block_count,texture_count,sdb1_table_count,group_labels,first_embedded_name,first_lmb_png,lmb_png_count\n";
        size_t tex_pkg_cnt = 0, lmb_pkg_cnt = 0, sdb_pkg_cnt = 0;
        for (const auto& r : results) {
            if (r.pkg_name.empty()) continue;
            if (r.texture_count > 0) tex_pkg_cnt++;
            if (!r.lmb_pngs.empty()) lmb_pkg_cnt++;
            if (r.sdb1_table_count > 0) sdb_pkg_cnt++;
            std::string first_en = r.embedded_names.empty() ? "" : r.embedded_names.front();
            std::string first_png = r.lmb_pngs.empty() ? "" : r.lmb_pngs.front();
            ofs << r.pkg_name << "," << r.file_size << "," << r.block_count << ","
                << r.texture_count << "," << r.sdb1_table_count << ",\""
                << r.group_labels << "\",\"" << first_en << "\",\"" << first_png << "\","
                << r.lmb_pngs.size() << "\n";
        }
        std::cout << "[ExvsFhm2dExtractor] Scan Complete!\n"
                  << "  -> Total Packages: " << files.size() << "\n"
                  << "  -> Texture Pkgs:   " << tex_pkg_cnt << "\n"
                  << "  -> LMB Anim Pkgs:  " << lmb_pkg_cnt << "\n"
                  << "  -> SDB1 DB Pkgs:   " << sdb_pkg_cnt << "\n"
                  << "  -> Saved CSV to:   " << out_csv.string() << "\n";
        return 0;
    }

    // Mode: sdb1 <file.fhm2d> [output_dir]
    if (mode == "sdb1") {
        if (argc < 3) {
            print_usage();
            return 1;
        }
        fs::path in_file = argv[2];
        fs::path out_dir = (argc >= 4) ? fs::path(argv[3]) : (in_file.parent_path() / "unpacked_sdb1");
        UnpackOptions opts;
        opts.export_dds = false;
        opts.export_lmb = false;
        opts.export_sdb1 = true;
        return run_single_or_batch_extract(in_file, out_dir, opts, 1);
    }

    // Mode: extract <in> [out] [--png] [--raw] [threads] OR direct <file.fhm2d> [out]
    int arg_idx = (mode == "extract") ? 2 : 1;
    if (argc <= arg_idx) {
        print_usage();
        return 1;
    }

    fs::path in_path = argv[arg_idx];
    fs::path out_dir = in_path.parent_path() / "unpacked_output";
    UnpackOptions opts;
    opts.texconv_path = find_texconv_exe(self_exe);
    int num_threads = static_cast<int>(std::max(1u, std::thread::hardware_concurrency()));

    for (int i = arg_idx + 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--png") {
            opts.export_png = true;
        } else if (a == "--raw") {
            opts.export_raw_blocks = true;
        } else if (a.rfind("--", 0) != 0) {
            bool is_num = !a.empty() && std::all_of(a.begin(), a.end(), ::isdigit);
            if (is_num && i == argc - 1 && i > arg_idx + 1) {
                num_threads = std::max(1, std::atoi(a.c_str()));
            } else {
                out_dir = a;
            }
        }
    }

    return run_single_or_batch_extract(in_path, out_dir, opts, num_threads);
}
