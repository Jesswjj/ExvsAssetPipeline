# ExvsAssetPipeline & SDB1 JSON Manager 使用说明书

本指南介绍如何在每次游戏客户端更新后：
1. **自动检测新增的机体 (`MsId`)、驾驶员服装 (`CostumeId`)、机体皮肤 (`SkinId`)、领航员 (`NaviId`) 及领航员服装 (`NaviCostumeId`)**
2. **一键将新数据同步到 `WebUI-Infinite/Client/wwwroot/data/*.json` 并解包生成对应 `.webp` / `.gif` 图片**
3. **使用命令行或交互式菜单对 JSON 文件进行增删改查 (CRUD)**

---

## 一、快速上手（Windows 一键批处理脚本）

在仓库根目录下提供了 3 个双击即可运行的批处理脚本：

| 脚本名称 | 功能说明 |
| :--- | :--- |
| **`一键检测游戏更新_Diff.bat`** | 读取 `dplcache_release` 中的 `SDB1` 二进制数据库表，与 `WebUI-Infinite` 的 JSON 文件对比，列出所有新增/变更的机体、机师服装、领航员、印章及其精确序号 (`Id`)，并生成 `update_diff_report.json` |
| **`一键同步新数据与图片_Sync.bat`** | 自动把 `SDB1` 中新增的机体、机师服装、皮肤、姿势、领航员、印章写入 JSON 文件，并调用 `exvs_asset_pipeline.py` 解包生成缺失的 `.webp` / `.gif` 图片，最后执行全量 `audit` 校验 |
| **`一键打开JSON管理工具_CRUD.bat`** | 启动中文交互式控制台菜单，支持按编号查询、新增、修改、删除机体、驾驶员服装、领航员与领航员服装 |

---

## 二、命令行工具 `exvs_db_manager.py` 详细用法

### 1. 检测游戏更新 (`scan` / `diff`)

每次游戏更新后，运行以下命令查看是否有新增机体、驾驶员服装或领航员：

```bash
python exvs_db_manager.py scan \
  --ib-dir "G:\EXVS2InfiniteBoost\data\x64\dplcache_release" \
  --webui-data "G:\AGiteeRepo\exvs2-ib\WebUI-Infinite\Client\wwwroot\data" \
  --report update_diff_report.json
```

**输出内容包括**：
- **新增机体 (`new_mobile_suits`)**：打印新机体的 `MsId`、`RowKey (Series_Unit_Form)`、`Cost`、所属作品、`.fhm2d` 贴图包哈希（如 `0x16B41EBA`）、内嵌贴图名（如 `46XTms_ms_s_062_002_001`）以及拥有的机师服装列表 `CostumeIds`。
- **新增驾驶员服装 (`new_pilot_costumes`)**：打印 `MsId`、`CostumeId (0..3)`、左右半身立绘与贴纸觉醒立绘 `.fhm2d` 包哈希。
- **新增机体换色皮肤 (`new_ms_skins`)**：打印 `MsId`、`SkinId` 与皮肤 `.fhm2d` 包哈希。
- **新增领航员与领航员服装 (`new_navigators` / `new_navigator_costumes`)**：自动从 `.fhm2d` 内嵌的 `g00_<char>_s01` 提取英文角色代号（如 `chuchumy`、`nena`），打印客户端真实协议 `CostumeId` 与资产后缀 `i01..i09`。

---

### 2. 一键同步新条目到 JSON (`sync`)

检测到新增内容后，运行 `sync` 自动将新条目写入 `MobileSuits.json`、`Navigators.json`、`messages/Stamps.json`：

```bash
python exvs_db_manager.py sync \
  --ib-dir "G:\EXVS2InfiniteBoost\data\x64\dplcache_release" \
  --webui-data "G:\AGiteeRepo\exvs2-ib\WebUI-Infinite\Client\wwwroot\data"
```

同步完成后，对于新增机体或领航员，系统会先填入带序号与角色代号的默认中日英名称，你可以通过下方的 CRUD 命令或交互式菜单补充译名。

---

### 3. 全量校验 JSON 与图片是否 100% 匹配 (`audit`)

检查 `wwwroot/data/*.json` 中的所有有效 ID 与 `wwwroot/images/` 下的实际 `.webp` / `.gif` 文件是否完全一一对应（`Missing=0, Extra=0`）：

```bash
python exvs_db_manager.py audit \
  --webui-data "G:\AGiteeRepo\exvs2-ib\WebUI-Infinite\Client\wwwroot\data" \
  --webui-images "G:\AGiteeRepo\exvs2-ib\WebUI-Infinite\Client\wwwroot\images"
```

---

### 4. 对 `MobileSuits.json` 进行增删改查 (`ms` / `pilot`)

#### (1) 查询与列出机体
```bash
# 列出所有可玩机体
python exvs_db_manager.py ms list

# 按作品或 Cost 筛选机体
python exvs_db_manager.py ms list --series "GQuuuuuuX"
python exvs_db_manager.py ms list --cost 3000

# 查看指定机体的完整 JSON 结构（含服装、皮肤、贴纸姿势）
python exvs_db_manager.py ms show 335
```

#### (2) 新增或修改机体 (`ms set` / `ms add` / `ms update`)
```bash
python exvs_db_manager.py ms set 335 \
  --name-jp "ウォドムポッド" \
  --name-en "Wodom Pod" \
  --name-cn "沃多姆荚舱" \
  --pilot-jp "メイ" \
  --pilot-en "May" \
  --pilot-cn "梅" \
  --series-en "Gundam Build Divers Re:Rise" \
  --series-jp "ガンダムビルドダイバーズRe:RISE" \
  --series-cn "高达创形者Re:RISE" \
  --cost 2500
```

#### (3) 删除机体 (`ms delete`)
```bash
python exvs_db_manager.py ms delete 999
```

#### (4) 新增、修改或删除驾驶员服装 (`pilot set-costume` / `pilot delete-costume`)
```bash
# 新增或修改机体 335 的第 2 套机师服装 (CostumeId = 1)
python exvs_db_manager.py pilot set-costume 335 1 \
  --name-jp "モビルドールメイ" \
  --name-en "Mobile Doll May" \
  --name-cn "机动人偶梅"

# 删除指定机师服装
python exvs_db_manager.py pilot delete-costume 335 1
```

---

### 5. 对 `Navigators.json` 进行增删改查 (`navi`)

#### (1) 列出与查看领航员
```bash
python exvs_db_manager.py navi list
python exvs_db_manager.py navi show 34
```

#### (2) 新增或修改领航员本体信息 (`navi set`)
```bash
python exvs_db_manager.py navi set 52 \
  --name-jp "チュチュミィ" \
  --name-en "Chuchumy" \
  --name-cn "丘丘莉" \
  --series-en "Gundam Reconguista in G" \
  --series-jp "ガンダム Gのレコンギスタ" \
  --series-cn "高达 G之复国运动"
```

#### (3) 新增、修改或删除领航员服装 (`navi set-costume` / `navi delete-costume`)
```bash
# 新增或修改领航员 34 (库荻莉亚) 的 CostumeId = 2
python exvs_db_manager.py navi set-costume 34 2 \
  --name-jp "衣装 2 (クーデリア・藍那・バーンスタイン)" \
  --name-en "Costume 2 (Kudelia Aina Bernstein)" \
  --name-cn "服装 2 (库荻莉亚·蓝那·伯恩斯坦)"

# 删除指定领航员服装
python exvs_db_manager.py navi delete-costume 34 3
```

---

## 三、全量贴图解包与 HUD 合成流水线 (`exvs_asset_pipeline.py`)

当需要从客户端 `dplcache_release` 重新解包并生成全部或部分图片目录时，运行 `exvs_asset_pipeline.py`：

```bash
# 运行全量流水线（自动扫描解包缺失的 .fhm2d 并生成全部 6,931 张图片）
python exvs_asset_pipeline.py \
  --ib-dir "G:\EXVS2InfiniteBoost\data\x64\dplcache_release" \
  --ws-tex "G:\GitHubRepo\fhm2d-unpack\workspace\ib_all_textures" \
  --webui-data "G:\AGiteeRepo\exvs2-ib\WebUI-Infinite\Client\wwwroot\data" \
  --out-images "G:\AGiteeRepo\exvs2-ib\WebUI-Infinite\Client\wwwroot\images"
```

也可以通过 `--stages` 只运行指定的资源模块：
- `stamps`：通信印章 (`stamps/1.webp..475.webp`)
- `player_level`：玩家等级 (`player_level/level/0.webp..1000.webp`、`round/`、`frames/`) 与段位 (`class/1.webp..20.webp`)
- `cost_mastery_bursts`：Cost 图标 (`cost/1500..3000.webp`)、机体熟练度 (`mastery/mobile_suit/0..22.webp`)、领航员亲密度 (`mastery/navi/0..16.webp`)、觉醒图标 (`bursts/0..3.webp`)
- `navis`：领航员头像与全身立绘静态/动态图 (`navis/{Id}.webp`、`navis/{Id}_{C}.webp/.gif`)
- `ms`：机体选择图、机师左右立绘、贴纸觉醒立绘、机体皮肤、战历图、贴纸姿势、贴纸背景/特效、战队背景/队徽、地图缩略图
- `titles`：全部 `2,514` 个静态与流光动态称号底板 (`titles/1..2514.webp/.gif`)
