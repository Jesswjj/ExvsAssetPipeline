# ExvsAssetPipeline

**EXVS2 Infinite Boost (VSAC29)** 客户端 `.fhm2d` 贴图解包、**`SDB1` 二进制数据库更新检测**、**WebUI JSON 增删改查 (CRUD) 管理** 与 **全量 HUD / 立绘合成流水线**。

---

## 核心特性

1. **直接解析游戏客户端 `SDB1` 二进制数据库表（零启发式猜测、100% 精确匹配）**：
   - **`0xDFD38C70.fhm2d`**：机体、机师服装（`CostumeId = 0..3`）、机体换色皮肤（`SkinId`）、贴纸姿势（`PoseId`）总表（263 行 × 101 列）。
   - **`0x6FCC0FBA.fhm2d`**：领航员角色总表（Block `#14`，53 行）与领航员服装总表（Block `#15`，144 行），自动区分客户端协议 `CostumeId` 与贴图后缀 `i01..i09`，并过滤未实装空包（`0x00000000`）。
   - **`0xED5C1B36.fhm2d`**：通信印章（Stamps）总表（431 个有效印章）。
   - **`0x89407144.fhm2d` & `0x0C948B3A.fhm2d`**：称号分类与底板贴图索引总表（2,514 个静态/动态称号底板）。

2. **游戏版本更新一键增量检测 (`exvs_db_manager.py scan`)**：
   - 每次游戏客户端更新后，自动扫描 `dplcache_release` 并与 `WebUI-Infinite/Client/wwwroot/data/*.json` 对比，立即输出：
     - 新增了哪些**机体**（新 `MsId`、`RowKey`、`Cost`、所属作品、`.fhm2d` 包名與内嵌贴图名）
     - 新增了哪些**驾驶员服装**（所属 `MsId`、新 `CostumeId`、左右立绘包名）
     - 新增了哪些**机体换色皮肤**与**贴纸姿势**
     - 新增了哪些**领航员**与**领航员服装**（新 `NaviId`、角色英文代号 `CharCode`、真实协议 `CostumeId`）
     - 新增了哪些**通信印章**

3. **JSON 增删改查管理与一键同步 (`exvs_db_manager.py sync` / `ms` / `pilot` / `navi` / `interactive`)**：
   - 支持一键将客户端新增条目同步写入 `MobileSuits.json`、`Navigators.json`、`Stamps.json`。
   - 提供完整的命令行 CRUD 与**中文交互式控制台菜单**，可随时新增、修改、删除机体、机师服装、领航员及领航员服装。
   - 提供 `audit` 命令，一键校验所有 JSON 配置与 `images/` 下 6,931 张图片是否 100% 一一对应（`Missing=0, Extra=0`）。

4. **全量 `.webp` / `.gif` 资源解包与像素级 HUD 合成 (`exvs_asset_pipeline.py`)**：
   - 自动解包并合成全部 24 个 WebUI 图片目录（共 **6,931 个静态 `.webp` 与动态 `.gif` 文件**），包括透明抠图的 `COST 1500~3000`、精确修正数字图集偏移的 `PLv. 0~1000`、段位、熟练度、亲密度、觉醒图标、流光动态称号与战队背景等。

---

## 目录结构

```text
ExvsAssetPipeline/
├── README.md                           # 项目总览与快速入口
├── Docs/
│   ├── USAGE.md                        # 详细使用教程（更新检测、JSON 增删改查、贴图合成）
│   └── SDB1_REVERSE_ENGINEERING.md     # SDB1 二进制数据库结构与逆向工程完整文档
├── exvs_db_manager.py                  # 游戏更新增量检测 (scan)、JSON 自动同步 (sync)、全量校验 (audit) 与 CRUD 工具
├── exvs_asset_pipeline.py              # 全量 .fhm2d 贴图解包与 WebUI .webp/.gif 合成流水线
├── exvs_fhm2d_extractor.cpp            # 零依赖 C++17 原生 .fhm2d / FHM / 46XT / DDS / SDB1 解包器源码
├── 一键检测游戏更新_Diff.bat            # Windows 一键检测客户端新增机体/机师/领航员脚本
├── 一键同步新数据与图片_Sync.bat        # Windows 一键同步 JSON 并解包生成新图片脚本
└── 一键打开JSON管理工具_CRUD.bat        # Windows 一键打开中文交互式 JSON 增删改查菜单
```

---

## 快速使用

### 1. 检测游戏更新（查看新增机体、机师、领航员及新序号）
双击运行 `一键检测游戏更新_Diff.bat`，或执行：
```bash
python exvs_db_manager.py scan
```

### 2. 一键同步新增条目到 JSON 并校验
双击运行 `一键同步新数据与图片_Sync.bat`，或执行：
```bash
python exvs_db_manager.py sync
python exvs_asset_pipeline.py --stages ms,navis,stamps
python exvs_db_manager.py audit
```

### 3. 交互式或命令行增删改 JSON
双击运行 `一键打开JSON管理工具_CRUD.bat`，或执行：
```bash
# 启动交互式菜单
python exvs_db_manager.py interactive

# 或使用命令行直接增删改
python exvs_db_manager.py ms show 335
python exvs_db_manager.py ms set 335 --name-cn "沃多姆荚舱" --pilot-cn "梅" --cost 2500
python exvs_db_manager.py pilot set-costume 335 1 --name-cn "机动人偶梅"
python exvs_db_manager.py navi set-costume 34 2 --name-cn "服装 2 (库荻莉亚·蓝那·伯恩斯坦)"
```

---

## 详细文档

- 📖 **[使用说明与 CRUD 指南 (`Docs/USAGE.md`)](Docs/USAGE.md)**
- 🔬 **[SDB1 二进制数据库与贴图逆向工程详解 (`Docs/SDB1_REVERSE_ENGINEERING.md`)](Docs/SDB1_REVERSE_ENGINEERING.md)**
