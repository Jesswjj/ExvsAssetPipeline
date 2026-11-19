@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] 正在从客户端 SDB1 数据库同步新增机体/机师服装/领航员/印章到 WebUI JSON...
python -X utf8 exvs_db_manager.py sync
echo.
echo [2/3] 正在解包并生成新增贴图资源 (.webp / .gif)...
python -X utf8 exvs_asset_pipeline.py --stages ms,navis,stamps
echo.
echo [3/3] 正在执行 JSON 与图片全量一致性校验 (Audit)...
python -X utf8 exvs_db_manager.py audit
echo.
pause
