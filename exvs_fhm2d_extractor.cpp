// ============================================================================
// ExvsFhm2dExtractor (C++17)
// Universal Native Asset, Texture (46XT/34XT) & LMB Animation Metadata Unpacker
// Supports:
//   - Mobile Suit Gundam EXVS2 XBoost (XB / vsac25)
//   - Mobile Suit Gundam EXVS2 OverBoost (OB / vsac27)
//   - Mobile Suit Gundam EXVS2 Infinite Boost (IB / vsac29)
//
// Build (macOS / Linux):
//   clang++ -O3 -std=c++17 exvs_fhm2d_extractor.cpp -lz -pthread -o exvs_fhm2d_extractor
// Build (Windows MSVC / MinGW):
//   g++ -O3 -std=c++17 exvs_fhm2d_extractor.cpp -lz -pthread -o exvs_fhm2d_extractor.exe
// ============================================================================

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include <zlib.h>

namespace fs = std::filesystem;

static constexpr uint32_t FHM2D_MAGIC = 0xCDB2B7B9;
static constexpr uint32_t RECORD_MARKER = 0x02F40040;

#pragma pack(push, 1)
struct Fhm2dHeader {
    uint32_t magic;
    uint32_t version;
    uint32_t flags0;
    uint32_t flags1;
    uint64_t reserved;
    uint64_t index_uncompressed_size;
    uint64_t index_compressed_size;
    uint64_t payload_uncompressed_size;
};

struct DDSPixelFormat {
    uint32_t size;
    uint32_t flags;
    uint32_t fourCC;
    uint32_t RGBBitCount;
    uint32_t RBitMask;
    uint32_t GBitMask;
    uint32_t BBitMask;
    uint32_t ABitMask;
};

struct DDSHeader {
    uint32_t magic; // 'DDS ' (0x20534444)
    uint32_t size;  // 124
    uint32_t flags;
    uint32_t height;
    uint32_t width;
    uint32_t pitchOrLinearSize;
    uint32_t depth;
    uint32_t mipMapCount;
    uint32_t reserved1[11];
    DDSPixelFormat ddspf;
    uint32_t caps;
    uint32_t caps2;
    uint32_t caps3;
    uint32_t caps4;
    uint32_t reserved2;
};

struct DDSHeaderDXT10 {
    uint32_t dxgiFormat;
    uint32_t resourceDimension;
    uint32_t miscFlag;
    uint32_t arraySize;
    uint32_t miscFlags2;
};
#pragma pack(pop)

struct DeflateBlock {
    size_t offset;
    size_t compressed_size;
    std::vector<uint8_t> data;
};

struct TextureEntry {
    int group_index;
    int texture_index;
    int embedded_index;
    std::string embedded_name;
    uint16_t format_code;
    uint16_t width;
    uint16_t height;
    uint8_t mip_count;
    size_t data_offset;
    size_t data_size;
};

static bool inflate_raw_block(const uint8_t* src, size_t src_len, size_t start_off,
                              std::vector<uint8_t>& out, size_t& consumed) {
    if (start_off >= src_len) return false;
    z_stream strm{};
    if (inflateInit2(&strm, -15) != Z_OK) return false;

    out.clear();
    uint8_t buf[65536];
    strm.next_in = const_cast<Bytef*>(src + start_off);
    strm.avail_in = static_cast<uInt>( std::min<size_t>(src_len - start_off, 0x7FFFFFFFULL) );

    int ret = Z_OK;
    while (ret == Z_OK) {
        strm.next_out = buf;
        strm.avail_out = sizeof(buf);
        ret = inflate(&strm, Z_NO_FLUSH);
        size_t have = sizeof(buf) - strm.avail_out;
        if (have > 0) {
            out.insert(out.end(), buf, buf + have);
        }
        if (ret == Z_STREAM_END) {
            consumed = strm.total_in;
            inflateEnd(&strm);
            return true;
        }
        if (ret != Z_OK) {
            break;
        }
        if (strm.avail_in == 0) {
            size_t cur_off = start_off + strm.total_in;
            if (cur_off >= src_len) break;
            strm.next_in = const_cast<Bytef*>(src + cur_off);
            strm.avail_in = static_cast<uInt>(std::min<size_t>(src_len - cur_off, 0x7FFFFFFFULL));
        }
    }
    inflateEnd(&strm);
    return false;
}

static std::vector<std::string> extract_ascii_strings(const std::vector<uint8_t>& buf, size_t min_len = 4) {
    std::vector<std::string> result;
    std::string cur;
    for (uint8_t c : buf) {
        if (c >= 32 && c < 127) {
            cur.push_back(static_cast<char>(c));
        } else {
            if (cur.size() >= min_len) {
                result.push_back(cur);
            }
            cur.clear();
        }
    }
    if (cur.size() >= min_len) result.push_back(cur);
    return result;
}

static bool write_dds_file(const fs::path& path, uint16_t fmt, uint16_t w, uint16_t h,
                           const uint8_t* pixels, size_t pixel_bytes) {
    DDSHeader hdr{};
    hdr.magic = 0x20534444; // "DDS "
    hdr.size = 124;
    hdr.flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000;
    hdr.height = h;
    hdr.width = w;
    hdr.pitchOrLinearSize = static_cast<uint32_t>(pixel_bytes);
    hdr.depth = 1;
    hdr.mipMapCount = 1;
    hdr.ddspf.size = 32;
    hdr.caps = 0x1000;

    bool use_dx10 = false;
    DDSHeaderDXT10 dx10{};
    dx10.resourceDimension = 3; // Texture2D
    dx10.arraySize = 1;

    if (fmt == 0x0000) {
        // Uncompressed RGBA8
        hdr.ddspf.flags = 0x41;
        hdr.ddspf.RGBBitCount = 32;
        hdr.ddspf.RBitMask = 0x000000FF;
        hdr.ddspf.GBitMask = 0x0000FF00;
        hdr.ddspf.BBitMask = 0x00FF0000;
        hdr.ddspf.ABitMask = 0xFF000000;
    } else {
        hdr.ddspf.flags = 0x4;
        hdr.ddspf.fourCC = 0x30315844; // "DX10"
        use_dx10 = true;
        if (fmt == 0x2400) dx10.dxgiFormat = 98;      // DXGI_FORMAT_BC7_UNORM
        else if (fmt == 0x2500) dx10.dxgiFormat = 77; // DXGI_FORMAT_BC3_UNORM
        else if (fmt == 0x2300) dx10.dxgiFormat = 80; // DXGI_FORMAT_BC4_UNORM
        else if (fmt == 0x2200) dx10.dxgiFormat = 71; // DXGI_FORMAT_BC1_UNORM
        else return false;
    }

    std::ofstream ofs(path, std::ios::binary);
    if (!ofs) return false;
    ofs.write(reinterpret_cast<const char*>(&hdr), sizeof(hdr));
    if (use_dx10) {
        ofs.write(reinterpret_cast<const char*>(&dx10), sizeof(dx10));
    }
    ofs.write(reinterpret_cast<const char*>(pixels), pixel_bytes);
    return true;
}

struct PackageScanResult {
    std::string pkg_name;
    size_t file_size = 0;
    size_t texture_count = 0;
    std::string group_labels;
    std::vector<std::string> embedded_names;
    std::vector<std::string> lmb_strings;
    std::vector<std::string> lmb_pngs;
};

static bool process_fhm2d_package(const fs::path& fhm_path, const fs::path& out_root,
                                  bool export_dds, PackageScanResult& res) {
    res.pkg_name = fhm_path.stem().string();
    std::ifstream ifs(fhm_path, std::ios::binary | std::ios::ate);
    if (!ifs) return false;
    res.file_size = static_cast<size_t>(ifs.tellg());
    if (res.file_size < sizeof(Fhm2dHeader)) return false;

    std::vector<uint8_t> blob(res.file_size);
    ifs.seekg(0);
    ifs.read(reinterpret_cast<char*>(blob.data()), blob.size());

    Fhm2dHeader hdr{};
    std::memcpy(&hdr, blob.data(), sizeof(hdr));
    if (hdr.magic != FHM2D_MAGIC) return false;

    std::vector<DeflateBlock> blocks;
    size_t offset = 0x30;
    while (offset < blob.size()) {
        std::vector<uint8_t> dec;
        size_t consumed = 0;
        if (!inflate_raw_block(blob.data(), blob.size(), offset, dec, consumed) || consumed == 0) {
            break;
        }
        blocks.push_back({offset, consumed, std::move(dec)});
        offset += consumed;
    }

    // Check if stopped at group label trailer e.g. "a1a2a3...\0" followed by LMB deflate blocks
    if (offset < blob.size() && blob[offset] == 'a') {
        auto nul_it = std::find(blob.begin() + offset, blob.end(), 0);
        if (nul_it != blob.end()) {
            res.group_labels.assign(reinterpret_cast<const char*>(blob.data() + offset),
                                    static_cast<size_t>(nul_it - (blob.begin() + offset)));
            size_t lmb_off = static_cast<size_t>(nul_it - blob.begin()) + 1;
            while (lmb_off < blob.size()) {
                std::vector<uint8_t> dec;
                size_t consumed = 0;
                if (!inflate_raw_block(blob.data(), blob.size(), lmb_off, dec, consumed) || consumed == 0) {
                    break;
                }
                blocks.push_back({lmb_off, consumed, std::move(dec)});
                lmb_off += consumed;
            }
        }
    }

    // Scan decompressed blocks for 46XT / 34XT textures and LMB\0 metadata
    for (size_t b = 0; b < blocks.size(); ++b) {
        const auto& d = blocks[b].data;
        if (d.size() >= 4 && std::memcmp(d.data(), "LMB\0", 4) == 0) {
            auto strs = extract_ascii_strings(d, 4);
            for (const auto& s : strs) {
                if (res.lmb_strings.size() < 64) res.lmb_strings.push_back(s);
                if (s.size() > 4 && (s.substr(s.size() - 4) == ".png" || s.substr(s.size() - 4) == ".PNG")) {
                    res.lmb_pngs.push_back(s);
                }
            }
        }
        // Scan for 46XT / 34XT embedded names and optional DDS export
        for (size_t i = 0; i + 8 < d.size(); ++i) {
            if ((d[i] == '4' && d[i+1] == '6' && d[i+2] == 'X' && d[i+3] == 'T') ||
                (d[i] == '3' && d[i+1] == '4' && d[i+2] == 'X' && d[i+3] == 'T')) {
                std::string name;
                for (size_t j = i; j < std::min(d.size(), i + 64); ++j) {
                    if (d[j] >= 32 && d[j] < 127) name.push_back(static_cast<char>(d[j]));
                    else break;
                }
                if (name.size() > 4) {
                    res.embedded_names.push_back(name);
                    if (export_dds && i >= 0x20) {
                        uint32_t marker = 0;
                        std::memcpy(&marker, d.data() + i - 0x20, sizeof(marker));
                        if (marker == RECORD_MARKER) {
                            uint16_t fmt = 0, w = 0, h = 0;
                            std::memcpy(&fmt, d.data() + i - 0x18, 2);
                            std::memcpy(&w, d.data() + i - 0x14, 2);
                            std::memcpy(&h, d.data() + i - 0x12, 2);
                            fs::path dds_dir = out_root / "dds" / res.pkg_name;
                            fs::create_directories(dds_dir);
                            write_dds_file(dds_dir / (name + ".dds"), fmt, w, h, d.data(), 0);
                        }
                    }
                    res.texture_count++;
                }
            }
        }
    }
    return true;
}

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cout << "Usage: exvs_fhm2d_extractor <dplcache_release_dir> <output_manifest_csv> [threads]\n";
        return 1;
    }
    fs::path in_dir = argv[1];
    fs::path out_csv = argv[2];
    int num_threads = (argc >= 4) ? std::max(1, std::atoi(argv[3])) : static_cast<int>(std::thread::hardware_concurrency());

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

    auto worker = [&]() {
        while (true) {
            size_t i = next_idx.fetch_add(1);
            if (i >= files.size()) break;
            process_fhm2d_package(files[i], out_csv.parent_path(), false, results[i]);
        }
    };

    std::vector<std::thread> pool;
    for (int t = 0; t < num_threads; ++t) pool.emplace_back(worker);
    for (auto& th : pool) th.join();

    fs::create_directories(out_csv.parent_path());
    std::ofstream ofs(out_csv);
    ofs << "package,file_size,texture_count,group_labels,first_embedded_name,first_lmb_png,lmb_png_count\n";
    size_t lmb_pkg_cnt = 0;
    for (const auto& r : results) {
        if (r.pkg_name.empty()) continue;
        if (!r.lmb_pngs.empty()) lmb_pkg_cnt++;
        std::string first_en = r.embedded_names.empty() ? "" : r.embedded_names.front();
        std::string first_png = r.lmb_pngs.empty() ? "" : r.lmb_pngs.front();
        ofs << r.pkg_name << "," << r.file_size << "," << r.texture_count << ",\""
            << r.group_labels << "\",\"" << first_en << "\",\"" << first_png << "\","
            << r.lmb_pngs.size() << "\n";
    }
    std::cout << "[ExvsFhm2dExtractor] Done! Packages with LMB PNG metadata: " << lmb_pkg_cnt
              << " -> Saved to " << out_csv << "\n";
    return 0;
}
