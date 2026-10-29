"""dsh 工作台（docs/DSH.md）：圆桌在本机启动、管理一个 dsh 进程当「边车」，dsh 插件在里面原样运行。

硬性原则（有测试守着）：

1. 圆桌是主人，dsh 是随时能拔掉的外设：只有 ``core/service.py`` 可以引用这个包，而且是可选导入；
   删掉整个 ``core/dsh/`` 圆桌照常运行（CI 的「拔掉 dsh」任务会真的删掉它再跑全套测试）。
2. dsh 不能写圆桌的数据：它自己的东西只在 ``data/dsh/``（家目录、工作区、运行时、缓存），
   删掉这个目录圆桌不受影响。
3. 控制方向不反转：永远是圆桌启动、停止、配置 dsh；dsh 版本和插件白名单由
   ``config/dsh_plugins.yaml`` 钉死。
4. dsh 不带任何人格：身份说明换成中立版本（提示词 ``dsh_workbench``），不装人设插件。
5. 只存本机：强制关掉 dsh 默认的会话日志上传、插件清单上报和遥测。

- ``catalog``：运行时版本与插件白名单（``config/dsh_plugins.yaml``）。
- ``overlay``：启动 dsh 时叠加的配置层（优先级最高，dsh 设置页改不掉）。
- ``channel``：dsh 用的模型 key（只在启动时交给 dsh 进程）和余额查询。
- ``usage``：用余额差值估算工作台每月花了多少（软上限）。
- ``sidecar``：dsh 进程的启动、停止、状态。
- ``install``：一次性安装运行时和白名单插件（``scripts/setup_dsh.py``）。
- ``manager``：把上面组装起来，给服务层用。
"""

from roundtable.core.dsh.catalog import DshCatalog, DshPlugin, load_catalog
from roundtable.core.dsh.manager import DshManager

__all__ = ["DshCatalog", "DshManager", "DshPlugin", "load_catalog"]
