# EXVS2 Infinite Boost (VSAC29) — SDB1 二进制数据库与贴图解包逆向方法论

本文档详细记录了从《机动战士高达 EXVS2 Infinite Boost (`vsac29_Release.exe`)》客户端数据包（`data/x64/dplcache_release/*.fhm2d`）中逆向解析 **`SDB1` 二进制数据库表**、**`46XT` / `FHM ` 纹理容器**、确定**机体 (`MsId`) / 机师服装 (`CostumeId`) / 机体皮肤 (`SkinId`) / 贴纸姿势 (`PoseId`) / 领航员 (`NaviId`) / 领航员服装 (`NaviCostumeId`)** 真实运行时序号，以及像素级复刻合成 WebUI 静态与动态（`.webp` / `.gif`）资源的完整方法。

---

## 一、核心设计原则：为什么必须以客户端 `SDB1` 二进制表为唯一权威数据源？

在早期版本中，若仅凭解包出的贴图内嵌文件名（如 `46XTms_ms_s_069_001_001`、`46XTvs_p_l_028_001_c03`、`g00_nena_i07`）去推断 WebUI 的 `Id` 或 `CostumeId`，会出现三类致命问题：

1. **同作品内机体顺序与 `MsId` 并非简单升序**：
   - 例如《GQuuuuuuX》作品号 `69` 下，`69001001`（Unit 1）实际对应 **`MsId = 332`（GQuuuuuuX / 玛秋）**，而 `69002001`（Unit 2）对应 **`MsId = 329`（红高达 / 修司）**。若按 `MsId` 升序强行配对，会导致机体与机师图片张冠李戴。
2. **机师立绘共享与槽位重映射**：
   - 共有 32 台衍生机体（如 `Id=6` G装甲、`Id=8` 夏亚专用扎古II、`Id=115` 强袭高达、`Id=257/260` 巴巴托斯天狼座/帝王形态等）与同作品基础机体共用同一套机师立绘 `.fhm2d` 包，其自身名义下的 `.fhm2d` 仅为 **63 字节空壳包**。
   - 此外，如飞翼零式（`Id=82` / `Id=328`）的第 2 套机师服装在包内命名为 `c03`，但在客户端 `SDB1` 表中挂载在 **槽位 1 (`CostumeId = 1`)**。
3. **领航员服装资产编号 (`i01..i09`) $\neq$ 客户端协议 `CostumeId`**：
   - 例如妮娜（`NaviId = 30`）的圣诞服贴图名为 `g00_nena_i07`，但在 `0x6FCC0FBA.fhm2d` Block #15 中其真实 **`CostumeId = 1`**（而 `i02..i06` 在 IB 客户端中的包哈希为 `0x00000000` 未实装）。若向客户端发送未实装的 `CostumeId`，客户端会因引用空包哈希直接崩溃。

因此，**唯一 100% 准确的方法是直接解析客户端 `dplcache_release` 中的 `SDB1` 二进制数据库表**。

---

## 二、`.fhm2d` 容器与 `SDB1` 二进制表结构规范

### 2.1 `.fhm2d` 流式 Deflate 块结构

每个 `.fhm2d` 文件由若干个 16 字节对齐的 raw deflate (`wbits = -15`) 压缩块组成：

| 偏移 | 类型 | 字段名 | 说明 |
| :--- | :--- | :--- | :--- |
| `0x00` | `uint32_le` | `comp_size` | 压缩数据长度（字节） |
| `0x04` | `uint32_le` | `decomp_size` | 解压后数据长度（字节） |
| `0x08` | `uint32_le` | `flag` | 固定为 `1` 表示有效 Deflate 块 |
| `0x0C` | `uint32_le` | `reserved` | 保留字段（通常为 `0`） |
| `0x10` | `byte[comp_size]` | `deflate_stream` | Raw deflate (`-15`) 压缩流，解压后按 16 字节向上对齐到下一块 |

### 2.2 `SDB1` 二进制关系表头部与行列解析公式

当 `.fhm2d` 解压出的块以魔数 `SDB1`（`0x31424453`）开头时，它是一张结构化内存数据库表：

```text
+-----------------------------------------------------------------------+
| 0x00: magic = "SDB1" (4B) | version/flags (12B)                       |
| 0x10: num_rows (uint32)   | num_cols (uint32)   | row_stride (uint32) |
| 0x1C: reserved (uint32)                                               |
+-----------------------------------------------------------------------+
| 0x20: col_hashes[num_cols] (uint32 * num_cols)                        |
|       每个字段名的 32-bit 哈希标识                                     |
+-----------------------------------------------------------------------+
| 0x20 + 4 * num_cols:                                                  |
|       col_descs[num_cols] (struct { uint64 offset; uint32 type; })    |
|       定义每列在单行记录内的字节偏移与数据类型                          |
+-----------------------------------------------------------------------+
| row_keys_offset = (num_cols + 2) * 16:                                |
|       row_keys[num_rows] (uint32 * num_rows)                          |
|       每行的主键 (RowKey)                                             |
+-----------------------------------------------------------------------+
| rows_data_offset = row_keys_offset + 4 * num_rows:                    |
|       rows_data[num_rows * row_stride]                                |
+-----------------------------------------------------------------------+
```

**字段类型 (`col_type`) 映射**：
- `0, 2, 9`：`uint8` (`<B`)
- `3`：`uint16` (`<H`)
- `4, 5, 6`：`uint32` (`<I`) —— 常用于 `Id`、`Cost`、以及指向其他 `.fhm2d` 文件名的 **32-bit 资源包哈希**（即 `0x{val:08X}.fhm2d`）
- `7`：`uint64` (`<Q`)

---

## 三、关键客户端 `SDB1` 数据库包字典

在 `data/x64/dplcache_release/` 下，以下 6 个核心 `.fhm2d` 文件控制了全游戏的机体、机师、领航员、印章、称号与贴图索引：

| `.fhm2d` 文件名 | 内部块 | 作用说明 | 行数 (IB) |
| :--- | :--- | :--- | :--- |
| **`0xDFD38C70.fhm2d`** | Block `#1..#7` 拼接 | **机体、机师服装、机体换色皮肤、贴纸姿势总表** | `263` 行（`249` 台可玩机体 + `14` 个换色皮肤行） |
| **`0x6FCC0FBA.fhm2d`** | Block `#14` & `#15` | **领航员角色总表 (`#14`) 与领航员服装总表 (`#15`)** | `#14`: `53` 行；`#15`: `144` 行（`119` 套有效服装） |
| **`0xED5C1B36.fhm2d`** | Block `#1` | **通信印章 (Stamps) 总表** | `434` 行（`431` 个有效印章 `.fhm2d` 包） |
| **`0x89407144.fhm2d`** | Block `#1..#15` | **称号 (Titles) 分类与编号总表** | 涵盖 `1..2514` 全部称号元数据 |
| **`0x0C948B3A.fhm2d`** | Block `#1` | **称号底板贴图 (`46XTshogo_m_XXXX`) 索引表** | 包含常规与隐藏称号底板哈希（如 `2084 -> 0x45BC1386`） |
| **`0xE49263F1.fhm2d`** | 全块拼接 | **全局资源哈希主索引表** | 映射全客户端 `.fhm2d` 资源包 |

---

## 四、机体与驾驶员总表 (`0xDFD38C70.fhm2d`) 逆向详解

将 `0xDFD38C70.fhm2d` 的 Block `#1..#7` 解压拼接后，得到完整的 `SDB1` 表（`263` 行 × `101` 列，`row_stride = 408` 字节）。

### 4.1 主键 (`RowKey`) 编码规则

$$\text{RowKey} = \text{SeriesId} \times 1000000 + \text{UnitIndex} \times 1000 + \text{FormOrSkinIndex}$$

- 例如：
  - `1001001` $\rightarrow$ `Series = 1`（初代高达），`Unit = 1`，`Form = 1` $\rightarrow$ **`MsId = 1`（高达 / 阿姆罗）**
  - `62002001` $\rightarrow$ `Series = 62`（创形者 Re:RISE），`Unit = 2`，`Form = 1` $\rightarrow$ **`MsId = 335`（沃多姆荚舱 / 梅）**
  - `69001001` $\rightarrow$ `Series = 69`（GQuuuuuuX），`Unit = 1`，`Form = 1` $\rightarrow$ **`MsId = 332`（GQuuuuuuX / 玛秋）**
  - `69002001` $\rightarrow$ `Series = 69`（GQuuuuuuX），`Unit = 2`，`Form = 1` $\rightarrow$ **`MsId = 329`（红高达 / 修司）**

### 4.2 核心列哈希 (`col_hash`) 对照表

| 列哈希 (`col_hash`) | 字段含义 | 说明 |
| :--- | :--- | :--- |
| **`0xA88E762A`** | **`MsId`（机体 ID）** | WebUI 与网络协议使用的唯一机体序号（`1..335`） |
| **`0x8157B2C0`** | **`SkinId`（机体皮肤序号）** | `0` = 默认机体；`1` = 换色皮肤（共 14 台机体拥有 `SkinId = 1` 的附加行） |
| **`0xFC33DA00`** | **`Cost`（机体 Cost 值）** | `1500` / `2000` / `2500` / `3000` |
| **`0x861175D5`** | **`SeriesId`（作品 ID）** | 对应高达系列作品编号（`1..69`） |
| **`0xE4E3753B`** | **`ms_select` 贴图包哈希** | 对应 `0x{hash:08X}.fhm2d`（内嵌 `46XTms_ms_s_SSS_UUU_FFF`，尺寸 `440×216`） |

### 4.3 驾驶员服装槽位列（`CostumeId = 0..3`）

每台机体在 `0xDFD38C70.fhm2d` 中最多支持 4 套机师服装槽位（`CostumeId = 0, 1, 2, 3`），每套服装包含 3 个贴图包哈希列：

| 槽位 (`CostumeId`) | 左侧出击立绘 (`pilots/{MsId}_{C}.webp`) | 右侧出击立绘 (`pilots/opposite/{MsId}_{C}.webp`) | 贴纸觉醒立绘 (`sticker/ex_p/{MsId}_{C}.webp`) |
| :---: | :---: | :---: | :---: |
| **`0`（默认服装）** | `0xE835F60F` | `0xCF29CF0B` | `0x7289CED8` |
| **`1`（服装 1）** | `0x713CA7B5` | `0x56209EB1` | `0xEB809F62` |
| **`2`（服装 2）** | `0x063B9723` | `0x2127AE27` | `0x9C87AFF4` |
| **`3`（服装 3）** | `0x985F0280` | `0xBF433B84` | `0x02E33A57` |

> **关键过滤规则（避免无效占位图）**：
> 1. 当某槽位的贴纸觉醒列（`0x7289CED8` / `0xEB809F62` / `0x9C87AFF4` / `0x02E33A57`）为 `0` 时，该服装槽位未启用。
> 2. 当立绘 `.fhm2d` 文件大小等于 **`41,634` 字节**（对应 `0xDFEEAF8A.fhm2d` / `0x4676CD26.fhm2d`，内嵌名为 `46XTvs_p_l_999_999_c99`）时，它是开发期遗留的空白透明占位图（出现在 `MsId=216` 勇士高达与 `MsId=233` 法尔西亚的槽位 1），必须跳过。

### 4.4 机体贴纸姿势槽位列（`PoseId = 0..5`）

每台机体的贴纸姿势（`sticker/ms/{MsId}_{PoseId}.webp`）由以下 6 列依次定义：
- `Pose 0`: `0x5C85A057`
- `Pose 1`: `0x2CEF54D8`
- `Pose 2`: `0x2B8290C1`
- `Pose 3`: `0xB28BC17B`
- `Pose 4`: `0xC2E135F4`
- `Pose 5`: `0xB5E60562`

过滤掉值为 `0` 或 63 字节空壳包后，按顺序编号为 `0..N-1` 即为 `MobileSuits.json` 中的 `Poses` 列表。

---

## 五、领航员与领航员服装总表 (`0x6FCC0FBA.fhm2d`) 逆向详解

`0x6FCC0FBA.fhm2d` 包含两个与领航员直接相关的 `SDB1` 块：

### 5.1 Block `#14`：领航员角色表（53 行）
- `col[0xEA6EE4A0]`：**`NaviId`（领航员角色 ID，`1..52`）**
- `col[0x861175D5]`：**`SeriesId`（所属作品 ID）**

### 5.2 Block `#15`：领航员服装表（144 行）
- `col[0xEB603363]`：**`NaviId`（所属领航员 ID，`1..52`）**
- `col[0x111572C1]`：**`CostumeId`（客户端真实协议服装序号，`0..7`）**
- `col[0xEA6EE4A0]`：**`AssetIdx`（贴图内嵌文件名 `g00_<name>_i0{AssetIdx + 1}` 的后缀索引）**
- `col[0xE19077AC]`：**`.fhm2d` 贴图包哈希**（若为 `0x00000000`，代表该服装在当前版本未实装，必须过滤！）

### 5.3 为什么必须区分 `CostumeId` 与 `AssetIdx`？

以下是 `0x6FCC0FBA.fhm2d` Block `#15` 中最典型的非连续映射实例：

| 领航员 (`NaviId`) | 角色代号 | 客户端协议 `CostumeId` (`col 0x111572C1`) | 贴图包内嵌编号 (`i{AssetIdx+1}`) | 贴图包哈希 (`col 0xE19077AC`) |
| :--- | :--- | :---: | :---: | :--- |
| **`15`（拉克丝）** | `lacus` | `2` | `g00_lacus_i08` | `0x1CA9F9AD.fhm2d` |
| **`21`（菲尔DT）** | `feldt` | `2` | `g00_feldt_i08` | `0xF32296AC.fhm2d` |
| **`26`（塞西莉）** | `cecily` | `1` / `2` | `g00_cecily_i02` / `i08` | `0x2CC1BC51` / `0xBBDC7513` |
| **`30`（妮娜）** | `nena` | `1` / `2` | `g00_nena_i07` / `i08` | `0x091314DE` / `0xECAA7E73` |
| **`33`（文奈）** | `fumina` | `2` / `3` | `g00_fumina_i04` / `i08` | `0x31741579` / `0xF08B9AA4` |
| **`34`（库荻莉亚）** | `kudelia` | `1` / `2` | `g00_kudelia_i04` / `i07` | `0x16808BE7` / `0xD4525D44` |
| **`44`（米奥莉奈）** | `miorine` | `1` / `2` | `g00_miorine_i08` / `i09` | `0xCC711689` / `0x5903D7FC` |

---

## 六、每次游戏更新后的自动化检测与同步流程

当游戏客户端更新（`dplcache_release` 目录下的 `.fhm2d` 文件发生变化）时，只需运行 `exvs_db_manager.py`：

1. **检测增量更新 (`scan` / `diff`)**：
   ```bash
   python exvs_db_manager.py scan --report update_diff.json
   ```
   脚本会直接读取 `0xDFD38C70.fhm2d`、`0x6FCC0FBA.fhm2d`、`0xED5C1B36.fhm2d`，自动对比现有的 `MobileSuits.json`、`Navigators.json`、`Stamps.json`，并打印所有：
   - 新增机体的 `MsId`、`RowKey`、`Cost`、所属作品、`.fhm2d` 包哈希与内嵌贴图名
   - 新增驾驶员服装的 `MsId`、`CostumeId` 与左右立绘包哈希
   - 新增领航员及服装的 `NaviId`、`CharCode`、真实 `CostumeId` 与包哈希

2. **一键同步到 JSON (`sync`) 并生成新图片 (`exvs_asset_pipeline.py`)**：
   ```bash
   python exvs_db_manager.py sync
   python exvs_asset_pipeline.py --stages ms,navis,stamps
   python exvs_db_manager.py audit
   ```
