"""HTTP 接口，按界面的部门分文件。顺序就是注册顺序（同一路径先注册的先匹配）。"""

from roundtable.api.routes import apartment, arcade, chat, office, system, terminal, workbench

ROUTERS = [m.router for m in (system, terminal, workbench, apartment, chat, office, arcade)]
